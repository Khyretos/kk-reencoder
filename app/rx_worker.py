"""
kk-reencoder worker module.

Processes files from the database, probing media, searching for better encodes,
and optionally staging new AV1 files or predicting quality.

Re-exports:
    from rx_scan import scan
    from rx_search import default_run, search_file, check_quality, folder_quality
"""

import json
import os

import rx_db
import rx_model
import rx_stage
from datetime import datetime, timezone

# Re-export required symbols
from rx_scan import scan  # noqa: F401
from rx_search import default_run, search_file, check_quality, folder_quality  # noqa: F401


def now():
    """Return current UTC time as 'YYYY-MM-DDTHH:MM:SSZ'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def library_root(cfg, library):
    """Return the entry of cfg['roots'] whose basename is library."""
    for root in cfg.get("roots", []):
        if os.path.basename(root) == library:
            return root
    raise ValueError(f"Library '{library}' not found in roots")


def write_report(conn, library, folder, cfg, in_media=False):
    """
    Write an encoding report to JSON.

    Writes to <cfg['report_dir']>/<library>/<folder>/encoding_report.json via a .tmp file
    and os.replace. When in_media is True, also writes to <library_root>/<folder>/encoding_report.json.

    Returns the report dict.
    """

    report = rx_model.folder_report(
        library,
        folder,
        rx_db.folder_files(conn, library, folder),
        now()
    )

    report_path_base = os.path.join(cfg["report_dir"], library, folder, "encoding_report.json")
    tmp_path = report_path_base + ".tmp"

    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, report_path_base)

    if in_media:
        lib_root = library_root(cfg, library)
        media_path = os.path.join(lib_root, folder, "encoding_report.json")
        os.makedirs(os.path.dirname(media_path), exist_ok=True)
        tmp_media = media_path + ".tmp"
        with open(tmp_media, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        os.replace(tmp_media, media_path)

    return report


def make_frames(path, staged, info, worst, cfg, run):
    """
    Generate comparison frames for the original and staged video.

    Creates pairs <i>-a.jpg (original) and <i>-b.jpg (staged) at times derived from
    frame_times(info['duration_s'], worst, 5). Errors are ignored.

    Returns the list of times used.
    """

    times = rx_stage.frame_times(info["duration_s"], worst, 5)
    d = rx_stage.frames_dir(cfg["frames_dir"], path)
    os.makedirs(d, exist_ok=True)

    try:
        for i, t in enumerate(times):
            orig_frame = os.path.join(d, f"{i}-a.jpg")
            stage_frame = os.path.join(d, f"{i}-b.jpg")
            run(rx_stage.frame_cmd(path, t, orig_frame))
            run(rx_stage.frame_cmd(staged, t, stage_frame))

        frames_json = os.path.join(d, "frames.json")
        data = {"path": path, "times": times}
        tmp_json = frames_json + ".tmp"
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_json, frames_json)
    except Exception:
        pass  # Frames are nice-to-have; ignore errors

    return times


def process(conn, f, cfg, run, probe=None, progress=None):
    """
    Process one file row from the database.

    Handles probing, quality checking/searching, reporting, or staging.

    Args:
        conn: Database connection.
        f: File dict from rx_db (status pending or running).
        cfg: Configuration dict.
        run: Function to execute ffmpeg commands (run(cmd, timeout=None, on_line=None)).
        probe: Optional probe function; defaults to rx_media.probe.
        progress: Optional dict to update with phase, percent, fps, eta_s.

    Returns:
        None. Updates database and filesystem as needed.
    """
    if probe is None:
        import rx_media
        probe = rx_media.probe

    path = f["path"]
    lib = f["library"]
    folder = f["folder"]

    # Step 1: Probe
    info = probe(path)
    if info is None:
        rx_db.set_result(conn, f["path"], status="failed", error="unreadable")
        return

    # Step 2: Check existing quality or search
    if progress is not None:
        progress["phase"] = "checking"
    q = folder_quality(conn, lib, folder, cfg)
    if q is not None:
        vmaf, ratio, worst = check_quality(path, info, q, cfg, run, lib)
        # Keep q only if it meets target criteria
        if vmaf >= cfg["target_vmaf"] - 1 and ratio <= cfg["max_ratio"]:
            pass  # keep q
        else:
            q = None

    if q is None:
        if progress is not None:
            progress["phase"] = "searching quality"
        res = search_file(path, info, cfg, run, lib)
        if res is None or res.get("q") is None:
            rx_db.set_result(conn, f["path"], status="skipped", reason="no smaller encode at target quality")
            write_report(conn, lib, folder, cfg, in_media=False)
            return
        q, vmaf, ratio, worst = res["q"], res["vmaf"], res["ratio"], res["worst"]

    # Compute effective mode for report/stage branches
    # manual: only series marked for encoding on the page are staged, the rest is estimated
    effective_mode = cfg.get("mode")
    if effective_mode == "manual":
        effective_mode = "stage" if rx_db.folder_encode(conn, f["library"], f["folder"]) else "report"

    # Step 3: Report mode
    if effective_mode == "report":
        new_bytes = int(f["size"] * ratio)
        rx_db.set_result(conn, f["path"], status="predicted", quality=q, vmaf=vmaf, new_bytes=new_bytes)
        write_report(conn, lib, folder, cfg, in_media=False)
        return

    # Step 4: Stage mode (handles 'stage' and 'manual')
    if effective_mode == "stage":
        import rx_media

        staged = rx_stage.staged_path(path)
        part = staged + ".part"
        # Ensure extension is kept: name without ext + .part + ext
        _, ext = os.path.splitext(path)
        part_name = os.path.basename(staged).rsplit(".", 1)[0]
        part = os.path.join(os.path.dirname(staged), f"{part_name}.part{ext}")

        os.makedirs(os.path.dirname(part), exist_ok=True)

        # Build encode command
        filters = cfg.get("filters", {}).get(lib, {})
        cmd = rx_media.encode_cmd(
            path, part, cfg["encoder"], q, cfg["device"],
            info.get("video_index", 0), cfg.get("filters", {}).get(lib)
        )

        block = []  # ffmpeg -progress prints key=value lines and ends each block with progress=...

        def on_line(line):
            block.append(line)
            if progress is not None and line.startswith("progress="):
                p = rx_model.parse_progress(block, info["duration_s"])
                block.clear()
                if p:
                    progress.update({
                        "phase": "encoding",
                        "percent": p["percent"],
                        "fps": p["fps"],
                        "eta_s": p["eta_s"]
                    })

        # first try the full-GPU path; files the GPU cannot decode (10-bit H.264, old codecs) fall back to the CPU decode
        gpu_cmd = rx_media.encode_cmd(path, part, cfg["encoder"], q, cfg["device"], info.get("video_index", 0),
                                      cfg.get("filters", {}).get(lib), hw_decode=cfg.get("hw_decode", True))
        rc, out, err = run(gpu_cmd, on_line=on_line)
        if rc != 0 and gpu_cmd != cmd:
            block.clear()
            rc, out, err = run(cmd, on_line=on_line)
        if rc != 0:
            rx_db.set_result(conn, f["path"], status="failed", error=err[-500:] if err else "encode failed")
            if os.path.exists(part):
                os.remove(part)
            write_report(conn, lib, folder, cfg, in_media=False)
            return

        # Probe the result
        new_info = probe(part)
        if new_info is None:
            rx_db.set_result(conn, f["path"], status="failed", error="probe failed on encoded file")
            if os.path.exists(part):
                os.remove(part)
            write_report(conn, lib, folder, cfg, in_media=False)
            return

        if new_info["codec"] != "av1":
            rx_db.set_result(conn, f["path"], status="failed", error="bad codec")
            if os.path.exists(part):
                os.remove(part)
            write_report(conn, lib, folder, cfg, in_media=False)
            return

        dur_diff = abs(new_info["duration_s"] - info["duration_s"])
        if dur_diff > 2:
            rx_db.set_result(conn, f["path"], status="failed", error="bad duration")
            if os.path.exists(part):
                os.remove(part)
            write_report(conn, lib, folder, cfg, in_media=False)
            return

        new_size = os.path.getsize(part)
        if new_size > f["size"] * cfg["max_ratio"]:
            rx_db.set_result(conn, f["path"], status="skipped", reason="encode not smaller")
            if os.path.exists(part):
                os.remove(part)
            write_report(conn, lib, folder, cfg, in_media=False)
            return

        # Replace part with staged
        os.replace(part, staged)

        # Make frames
        if progress is not None:
            progress["phase"] = "comparison frames"
        make_frames(path, staged, info, worst, cfg, run)

        # Set result
        rx_db.set_result(
            conn, f["path"],
            status="staged",
            codec_after="av1",
            quality=q,
            vmaf=vmaf,
            new_bytes=new_size,
            encoded_at=now(),
            reason=None
        )

        write_report(conn, lib, folder, cfg, in_media=False)
        return

    # If no mode matches, treat as failed
    rx_db.set_result(conn, f["path"], status="failed", error="unknown mode")
    return
