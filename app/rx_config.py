"""Configuration for kk-reencoder.

This module provides configuration management for the re-encoder service,
reading settings from RX_* environment variables and providing defaults.
"""

import os
from typing import Any, Dict, List, Optional


def config_from_env(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Build the configuration dictionary from RX_* environment variables.

    Args:
        env: Dictionary of environment variables. If None, uses os.environ.

    Returns:
        Configuration dictionary with all settings.

    Raises:
        ValueError: If an invalid mode is specified.
    """
    if env is None:
        env = os.environ

    # Parse comma-separated lists
    def parse_list(val: str, default: List[str]) -> List[str]:
        if not val:
            return default
        return [item.strip() for item in val.split(",") if item.strip()]

    # Parse integer with clamping
    def parse_int(val: str, default: int, min_val: int, max_val: int) -> int:
        try:
            n = int(val)
        except (ValueError, TypeError):
            return default
        return max(min_val, min(max_val, n))

    # Parse float
    def parse_float(val: str, default: float) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # Parse boolean
    def parse_bool(val: str, default: bool) -> bool:
        if val.lower() in ("false", "0", "no"):
            return False
        return True

    # Parse roots (comma-separated paths)
    roots_raw = env.get("RX_ROOTS", "")
    if roots_raw:
        roots = [r.strip() for r in roots_raw.split(",") if r.strip()]
    else:
        # When empty, use every directory in /media
        media_path = "/media"
        if os.path.exists(media_path):
            try:
                roots = [os.path.join(media_path, d) for d in os.listdir(media_path)
                         if os.path.isdir(os.path.join(media_path, d))]
            except PermissionError:
                roots = []
        else:
            roots = []

    # Parse mode
    mode = env.get("RX_MODE", "report").strip()
    if mode not in ("report", "stage", "manual"):
        raise ValueError(f"Invalid mode '{mode}'. Must be 'report', 'stage' or 'manual'.")

    # Parse encoder
    encoder = env.get("RX_ENCODER", "qsv").strip()

    # Parse device
    device = env.get("RX_DEVICE", "/dev/dri/renderD128").strip()

    # Parse target_vmaf
    target_vmaf = parse_float(env.get("RX_TARGET_VMAF", "95"), 95.0)

    # Parse max_ratio
    max_ratio = parse_float(env.get("RX_MAX_RATIO", "0.8"), 0.8)

    # Parse q_min, q_max, q_start
    q_min = parse_int(env.get("RX_Q_MIN", "10"), 10, 1, 51)
    q_max = parse_int(env.get("RX_Q_MAX", "40"), 40, 1, 51)
    q_start = parse_int(env.get("RX_Q_START", "22"), 22, 1, 51)

    # Parse samples
    samples = parse_int(env.get("RX_SAMPLES", "3"), 3, 1, 10)

    # Parse sample_len
    sample_len = parse_int(env.get("RX_SAMPLE_LEN", "20"), 20, 1, 60)

    # Parse vmaf_threads
    vmaf_threads = parse_int(env.get("RX_VMAF_THREADS", "4"), 4, 1, 32)

    # Parse search_per_folder
    search_per_folder = parse_int(env.get("RX_SEARCH_PER_FOLDER", "2"), 2, 1, 10)

    # Parse parallel (clamped 1..5)
    parallel = parse_int(env.get("RX_PARALLEL", "2"), 2, 1, 5)

    # Optional CPU filters per library, like 'Anime=deband'. Default none: the deband filter only runs on
    # the CPU and made anime ~5x slower than the full-GPU path (Kees chose speed, 2026-10-19).
    filters_raw = env.get("RX_FILTERS", "")
    filters: Dict[str, str] = {}
    for item in filters_raw.split(","):
        if "=" in item:
            lib, filter_name = (x.strip() for x in item.split("=", 1))
            if filter_name and filter_name.lower() != "none":
                filters[lib] = filter_name

    # Parse codecs
    codecs = parse_list(env.get("RX_CODECS", "h264,hevc,mpeg4,vc1,mpeg2video,wmv3,msmpeg4v3"),
                        ["h264", "hevc", "mpeg4", "vc1", "mpeg2video", "wmv3", "msmpeg4v3"])

    # Parse skip_hdr
    skip_hdr = parse_bool(env.get("RX_SKIP_HDR", "true"), True)

    # Parse max_height
    max_height = parse_int(env.get("RX_MAX_HEIGHT", "2160"), 2160, 360, 8192)

    # Parse min_kbps (fixed dict)
    min_kbps = {
        "sd": 900,
        "720": 1500,
        "1080": 2500,
        "2160": 8000
    }

    # Parse work_dir
    work_dir = env.get("RX_WORK_DIR", "/data/work").strip()

    # Parse report_dir
    report_dir = env.get("RX_REPORT_DIR", "/data/reports").strip()

    # Parse frames_dir
    frames_dir = env.get("RX_FRAMES_DIR", "/data/frames").strip()

    # Parse max_staged_gb
    max_staged_gb = parse_float(env.get("RX_MAX_STAGED_GB", "1000"), 1000.0)

    # Parse db
    db = env.get("RX_DB", "/data/reencoder.db").strip()

    # Parse port
    port = parse_int(env.get("RX_PORT", "8080"), 8080, 1, 65535)

    # Parse idle_s
    idle_s = parse_int(env.get("RX_IDLE_S", "10"), 10, 1, 3600)

    # Parse rescan_s
    rescan_s = parse_int(env.get("RX_RESCAN_S", "21600"), 21600, 60, 86400)

    return {
        "roots": roots,
        "mode": mode,
        "encoder": encoder,
        "device": device,
        "target_vmaf": target_vmaf,
        "max_ratio": max_ratio,
        "q_min": q_min,
        "q_max": q_max,
        "q_start": q_start,
        "samples": samples,
        "sample_len": sample_len,
        "vmaf_threads": vmaf_threads,
        "search_per_folder": search_per_folder,
        "parallel": parallel,
        "filters": filters,
        "codecs": codecs,
        "skip_hdr": skip_hdr,
        "max_height": max_height,
        "min_kbps": min_kbps,
        "work_dir": work_dir,
        "report_dir": report_dir,
        "frames_dir": frames_dir,
        "max_staged_gb": max_staged_gb,
        "db": db,
        "port": port,
        "idle_s": idle_s,
        "rescan_s": rescan_s,
    }
