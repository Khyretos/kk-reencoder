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
    proc = subprocess.Popen(
        polite_cmd(cmd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
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
        
    t_err.join()
    
    return rc, "\n".join(stdout_lines), "\n".join(stderr_lines)


def getsize(path: str) -> int:
    """Get file size in bytes."""
    return os.path.getsize(path)


def useful_tail(err: Optional[str], limit: int = 400) -> str:
    """The end of ffmpeg's stderr without the libva/VPL start-up lines, which say nothing about the failure."""
    lines = [l for l in (err or "").splitlines()
             if l.strip() and not l.lstrip().startswith(("libva info:", "libva error: vaGetDriverNameByIndex", "frame=", "size="))
             and "elapsed=" not in l]
    return "\n".join(lines)[-limit:]


def _run_ok(run: Callable, cmd: List[str]) -> str:
    """Run a command; a non-zero exit raises RuntimeError with the stderr tail. Returns stderr."""
    rc, _, err = run(cmd)
    if rc != 0:
        tail = useful_tail(err)
        raise RuntimeError(f"{os.path.basename(cmd[0])} {describe_rc(rc)}" + (f": {tail}" if tail else ""))
    return err or ""


def _cut_samples(path: str, info: Dict[str, Any], starts: List[int], cfg: Dict[str, Any], run: Callable,
                 made: List[str], filter_name: Optional[str] = None) -> List[Tuple[int, str, str]]:
    """Cut each sample and make its lossless reference: [(start, sample, ref)]."""
    out = []
    for i, start in enumerate(starts):
        sample = os.path.join(cfg["work_dir"], f"s{i}.mkv")
        ref = os.path.join(cfg["work_dir"], f"s{i}-ref.mkv")
        made += [sample, ref]
        _run_ok(run, rx_media.sample_cmd(path, sample, start, cfg["sample_len"], info.get("video_index", 0)))
        _run_ok(run, rx_media.source_reference_cmd(path, ref, start, cfg["sample_len"], info.get("video_index", 0),
                                                   filter_name))
        out.append((start, sample, ref))
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
        err = _run_ok(run, rx_media.vmaf_cmd(enc, ref, cfg["vmaf_threads"], log, info.get("height")))
        score = rx_media.parse_vmaf(err)
        if score is None:
            raise RuntimeError(f"no VMAF score for q={q}")
        scores.append(score)
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
