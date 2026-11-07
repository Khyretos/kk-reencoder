"""
rx_search.py

VMAF-based search for finding the highest quality AV1 encoding that is smaller
than the original file. This module orchestrates sampling, encoding trials,
and VMAF scoring to determine the optimal quality setting (q) for re-encoding.

It relies on sibling modules:
- rx_db: Database access for configuration and library filters.
- rx_media: FFmpeg command builders and VMAF parsing.
- rx_model: Quality selection logic (next_quality, pick_result).
- rx_stage: Helper functions for worst time calculation from logs.
"""

import csv
import io
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Sibling modules
import rx_db
import rx_media
import rx_model
import rx_stage


# ffmpeg sizes its thread pools by the host's CPU count, not by the container's CPU limit: on a 4-CPU container on a
# bigger host every decoder started dozens of threads (CPU throttled all the time, and 4K frames in flight filled the
# 3 GB memory limit until the kernel killed ffmpeg). Every ffmpeg gets this many threads per decoder, encoder and filter.
FFMPEG_THREADS = max(1, int(os.environ.get("RX_FFMPEG_THREADS", "2") or 2))
# encodes run at the lowest CPU and IO priority, so the web page and the server stay quick while they work
POLITE = ["nice", "-n", "19", "ionice", "-c", "3"]


def polite_cmd(cmd: List[str], threads: int = FFMPEG_THREADS) -> List[str]:
    """cmd at the lowest priority; an ffmpeg also gets a thread cap per input, output and filter graph."""
    if not cmd or os.path.basename(cmd[0]) != "ffmpeg":
        return POLITE + list(cmd)
    out = [cmd[0], "-filter_threads", str(threads)]
    i = 1
    while i < len(cmd):
        if cmd[i] == "-i":
            out += ["-threads", str(threads)]
        out.append(cmd[i])
        i += 1
    # encoder threads: an output option, right before the output name (the last argument)
    out = out[:-1] + ["-threads", str(threads), out[-1]]
    return POLITE + out


# Pause: every ffmpeg / ffprobe this module starts is listed here, so a pause can freeze the ones already running
# (SIGSTOP: no CPU, no GPU work, nothing lost; SIGCONT carries on where they were). Before, pause only kept new files
# from starting, and an encode that had begun ran on for hours.
_procs_lock = threading.Lock()
_procs: set = set()
_go = threading.Event()  # set = running; cleared while paused (new commands wait for it)
_go.set()


def _signal_all(sig: int) -> None:
    with _procs_lock:
        for proc in list(_procs):
            try:
                os.killpg(proc.pid, sig)  # nice/ionice exec into ffmpeg, which leads its own process group
            except (ProcessLookupError, PermissionError):
                pass


def pause_all() -> None:
    """Freeze every running command and hold back new ones until resume_all()."""
    _go.clear()
    _signal_all(signal.SIGSTOP)


def resume_all() -> None:
    """Let frozen commands carry on and new ones start."""
    _go.set()
    _signal_all(signal.SIGCONT)


def is_paused() -> bool:
    return not _go.is_set()


def describe_rc(rc: int) -> str:
    """A return code in words (negative: killed by that signal)."""
    if rc == -9:
        return "killed (-9), most likely the container ran out of memory"
    if rc == -1:
        return "timed out"
    return f"exit code {rc}"


def default_run(
    cmd: List[str],
    timeout: Optional[float] = None,
    on_line: Optional[Callable[[str], None]] = None,
) -> Tuple[int, str, str]:
    """
    Execute a command via subprocess, collecting stdout line-by-line and stderr.
    
    Args:
        cmd: The argument list to execute (no shell).
        timeout: Maximum seconds to wait for completion.
        on_line: Callback(line) called for each stdout line.
        
    Returns:
        Tuple of (returncode, stdout_text, stderr_text).
    """
    _go.wait()  # paused: the next step of a job waits here instead of starting
    proc = subprocess.Popen(
        polite_cmd(cmd),
        text=True,
        # ffmpeg prints file names and tags as raw bytes: one non-UTF-8 byte (0x81 in an old AVI tag) killed the
        # stderr reader, the pipe closed, and ffmpeg died half-way (short or 0 s AV1 copies, broken samples)
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # its own process group, so a pause reaches everything it started
    )
    with _procs_lock:
        _procs.add(proc)
    if not _go.is_set():  # paused while it was being started
        try:
            os.killpg(proc.pid, signal.SIGSTOP)
        except ProcessLookupError:
            pass
    
    stdout_lines = []
    stderr_lines = []
    
    def read_stdout():
        try:
            for line in proc.stdout:
                line = line.rstrip('\n')
                stdout_lines.append(line)
                if on_line:
                    on_line(line)
        finally:
            proc.stdout.close()
            
    def read_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.append(line.rstrip('\n'))
        finally:
            proc.stderr.close()
            
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_err.start()
    
    try:
        stdout_thread = threading.Thread(target=read_stdout)
        stdout_thread.start()
        stdout_thread.join(timeout=timeout)
        
        if stdout_thread.is_alive():
            proc.kill()
            stdout_thread.join()
            proc.wait()
            return -1, "", "\n".join(stderr_lines)
            
        rc = proc.wait()
    except Exception:
        proc.kill()
        proc.wait()
        raise
        
    finally:
        with _procs_lock:
            _procs.discard(proc)

    t_err.join()

    return rc, "\n".join(stdout_lines), "\n".join(stderr_lines)


def getsize(path: str) -> int:
    """Get file size in bytes."""
    return os.path.getsize(path)


def useful_tail(err: Optional[str], limit: int = 400) -> str:
    """The end of ffmpeg's stderr without the libva/VPL start-up lines, which say nothing about the failure."""
    lines = [l for l in (err or "").splitlines()
             if l.strip() and "libva info:" not in l and "vaGetDriverNameByIndex" not in l
             and not l.lstrip().startswith(("frame=", "size=")) and "elapsed=" not in l]
    # whole lines from the end: a line cut in half ("I version 1.22.0") read as the reason on the page
    out: List[str] = []
    for l in reversed(lines):
        if out and sum(len(x) + 1 for x in out) + len(l) > limit:
            break
        out.insert(0, l[-limit:])
    return "\n".join(out)


def _run_ok(run: Callable, cmd: List[str]) -> str:
    """Run a command; a non-zero exit raises RuntimeError with the stderr tail. Returns stderr."""
    rc, _, err = run(cmd)
    if rc != 0:
        tail = useful_tail(err)
        raise RuntimeError(f"{os.path.basename(cmd[0])} {describe_rc(rc)}" + (f": {tail}" if tail else ""))
    return err or ""


# GPU VMAF: None until the first GPU score of this run has been compared with the CPU's, then True (use the GPU) or
# False (stay on the CPU). Scores must match: the search picks the quality from them.
_gpu_vmaf: Dict[str, Optional[bool]] = {"ok": None}
GPU_TOLERANCE = 0.01
# below 720p the GPU path cost more CPU than the CPU one (a 480p episode: 568 s against 101 s; the per-frame GPU
# round trip dominates), so small videos stay on the CPU
GPU_MIN_HEIGHT = 720


def gpu_vmaf_state() -> Optional[bool]:
    return _gpu_vmaf["ok"]


def _vmaf_score(enc: str, ref: str, log: str, info: Dict[str, Any], cfg: Dict[str, Any], run: Callable) -> float:
    """VMAF of enc against ref: on the GPU where it is available and proved to score like the CPU, else on the CPU."""
    threads, height = cfg["vmaf_threads"], info.get("height")
    if (cfg.get("vmaf_gpu", "auto") != "off" and _gpu_vmaf["ok"] is not False and (height or 0) >= GPU_MIN_HEIGHT
            and os.path.exists(rx_media.VMAF_GPU_FFMPEG)):
        qsv = cfg.get("device") if cfg.get("encoder") == "qsv" and cfg.get("hw_decode", True) else None
        rc, _, err = run(rx_media.vmaf_cmd(enc, ref, threads, log, height, gpu=True, qsv_device=qsv))
        score = rx_media.parse_vmaf(err) if rc == 0 else None
        first = _gpu_vmaf["ok"] is None
        if first:
            # first GPU score of this run: the CPU scores the same pair, every frame, and must agree
            cpu = rx_media.parse_vmaf(_run_ok(run, rx_media.vmaf_cmd(enc, ref, threads, None, height, subsample=1)))
            ok = score is not None and cpu is not None and abs(score - cpu) <= GPU_TOLERANCE
            _gpu_vmaf["ok"] = ok
            print(f"GPU VMAF check: gpu {score} cpu {cpu}: " + ("scoring on the GPU" if ok else "staying on the CPU")
                  + ("" if rc == 0 else f" ({describe_rc(rc)}: {useful_tail(err, 200)})"), file=sys.stderr, flush=True)
        if score is not None and _gpu_vmaf["ok"]:
            return score
        if rc != 0 and not first:  # a later GPU failure: this score comes from the CPU, the next one tries the GPU again
            print(f"GPU VMAF failed ({describe_rc(rc)}: {useful_tail(err, 200)}), scored on the CPU", file=sys.stderr, flush=True)
    score = rx_media.parse_vmaf(_run_ok(run, rx_media.vmaf_cmd(enc, ref, threads, log, height)))
    if score is None:
        raise RuntimeError("no VMAF score")
    return score


# a lossless reference with no frames is a bare Matroska header (~600 bytes); one real frame is far bigger
EMPTY_REF_BYTES = 16 * 1024


def _cut_samples(path: str, info: Dict[str, Any], starts: List[int], cfg: Dict[str, Any], run: Callable,
                 made: List[str], filter_name: Optional[str] = None) -> List[Tuple[int, str, str]]:
    """Cut each sample and make its lossless reference: [(start, sample, ref)]."""
    out = []
    for i, start in enumerate(starts):
        sample = os.path.join(cfg["work_dir"], f"s{i}.mkv")
        ref = os.path.join(cfg["work_dir"], f"s{i}-ref.mkv")
        made += [sample, ref]
        # a start where the original has no readable video (past the real end of a cut-off file, or a damaged
        # stretch) gives a reference with no frames, which every later step reads as "End of file": try earlier
        # starts before giving up
        tries = [start] + [t for t in (start // 2, start // 4, 0) if t < start]
        for t in dict.fromkeys(tries):
            _run_ok(run, rx_media.sample_cmd(path, sample, t, cfg["sample_len"], info.get("video_index", 0)))
            _run_ok(run, rx_media.source_reference_cmd(path, ref, t, cfg["sample_len"], info.get("video_index", 0),
                                                       filter_name, ten_bit=bool(info.get("ten_bit"))))
            if getsize(ref) >= EMPTY_REF_BYTES:
                break
            print(f"{path}: no readable video at {t} s for a quality sample, trying earlier", file=sys.stderr, flush=True)
        else:
            raise RuntimeError(f"the original has no readable video at {start} s or before it for a quality sample "
                               f"(damaged or cut-off file?)")
        out.append((t, sample, ref))
    return out


def _trial(q: int, samples: List[Tuple[int, str, str]], info: Dict[str, Any], cfg: Dict[str, Any],
           run: Callable, filter_name: Optional[str], made: List[str]) -> Dict[str, Any]:
    """Encode every sample at q and score it: {'q', 'vmaf' (mean), 'ratio' (mean), 'worst'}."""
    fps = info.get("fps") or 24.0
    scores, ratios, worst = [], [], []
    for i, (start, sample, ref) in enumerate(samples):
        enc = os.path.join(cfg["work_dir"], f"s{i}-q{q}.mkv")
        log = os.path.join(cfg["work_dir"], f"s{i}-q{q}.csv")
        made += [enc, log]
        # the reference already carries the filter, so the sample encode must not apply it twice
        _run_ok(run, rx_media.sample_encode_cmd(ref, enc, cfg["encoder"], q, cfg["device"]))
        scores.append(_vmaf_score(enc, ref, log, info, cfg, run))
        ratios.append(getsize(enc) / max(1, getsize(sample)))
        text = open(log).read() if os.path.exists(log) else ""
        w = rx_stage.worst_time(text, float(start), fps)
        if w is not None:
            worst.append(w)
    return {"q": q, "vmaf": sum(scores) / len(scores), "ratio": sum(ratios) / len(ratios), "worst": worst}


def _cleanup(made: List[str]) -> None:
    for p in made:
        try:
            os.remove(p)
        except OSError:
            pass


def _own_work_dir(cfg: Dict[str, Any], path: str) -> Dict[str, Any]:
    """cfg with a work_dir of its own for this file, so parallel jobs never share sample names."""
    import hashlib
    sub = os.path.join(cfg["work_dir"], hashlib.sha1(path.encode()).hexdigest()[:12])
    os.makedirs(sub, exist_ok=True)
    return dict(cfg, work_dir=sub)


def _drop_work_dir(cfg: Dict[str, Any]) -> None:
    try:
        os.rmdir(cfg["work_dir"])
    except OSError:
        pass


def search_file(path: str, info: Dict[str, Any], cfg: Dict[str, Any], run: Callable,
                library: Optional[str] = None) -> Dict[str, Any]:
    """VMAF search: the highest q whose samples reach the target at most max_ratio of the size."""
    cfg = _own_work_dir(cfg, path)
    filter_name = cfg.get("filters", {}).get(library)
    made: List[str] = []
    try:
        samples = _cut_samples(path, info, rx_media.sample_points(info["duration_s"], cfg["samples"], cfg["sample_len"]),
                               cfg, run, made, filter_name)
        trials: List[Dict[str, Any]] = []
        while True:
            q = rx_model.next_quality(trials, cfg["target_vmaf"], cfg["q_min"], cfg["q_max"], start=cfg["q_start"])
            if q is None:
                break
            trials.append(_trial(q, samples, info, cfg, run, filter_name, made))
    finally:
        _cleanup(made)
        _drop_work_dir(cfg)
    best = rx_model.pick_result(trials, cfg["target_vmaf"], cfg["max_ratio"])
    if best is None:
        return {"q": None, "vmaf": None, "ratio": None, "worst": [], "trials": trials}
    return dict(best, trials=trials)


def check_quality(path: str, info: Dict[str, Any], q: int, cfg: Dict[str, Any], run: Callable,
                  library: Optional[str] = None) -> Tuple[float, float, List[float]]:
    """One sample in the middle of the file at q: (vmaf, ratio, worst times)."""
    cfg = _own_work_dir(cfg, path)
    made: List[str] = []
    try:
        start = rx_media.sample_points(info["duration_s"], 1, cfg["sample_len"])
        filter_name = cfg.get("filters", {}).get(library)
        samples = _cut_samples(path, info, start, cfg, run, made, filter_name)
        t = _trial(q, samples, info, cfg, run, filter_name, made)
    finally:
        _cleanup(made)
        _drop_work_dir(cfg)
    return t["vmaf"], t["ratio"], t["worst"]


def folder_quality(
    conn,
    library: str,
    folder: str,
    cfg: Dict[str, Any],
) -> Optional[int]:
    """
    Calculate the median quality of files in a folder.
    
    Only considers files with status in ['predicted', 'staged', 'confirmed', 'encoded']
    that have a non-null quality value.
    
    Returns:
        Median quality as an integer, or None if fewer than cfg['search_per_folder'] files qualify.
    """
    statuses = ['predicted', 'staged', 'confirmed', 'encoded']
    min_count = cfg['search_per_folder']
    
    files = rx_db.folder_files(conn, library, folder)
    
    qualities = []
    for f in files:
        if f['status'] in statuses and f.get('quality') is not None:
            qualities.append(f['quality'])
            
    if len(qualities) < min_count:
        return None
        
    # Sort and find median
    qualities.sort()
    n = len(qualities)
    mid = n // 2
    if n % 2 == 0:
        return int((qualities[mid - 1] + qualities[mid]) / 2)
    else:
        return int(qualities[mid])
