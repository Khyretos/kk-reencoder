"""Hardware detection for kk-reencoder.

Finds the GPUs the container can see (render nodes under /dev/dri with their PCI vendor, and NVIDIA devices),
tries a tiny AV1 test encode on each candidate and picks the first that works:

    Intel  -> av1_qsv (Arc / Core Ultra and newer), else av1_vaapi
    AMD    -> av1_vaapi (RX 7000 / RDNA3 and newer)
    NVIDIA -> av1_nvenc (RTX 40 and newer)
    none   -> libsvtav1 on the CPU (works everywhere, slower)

RX_ENCODER=auto (the default) does this at start-up; qsv, vaapi, nvenc or svt use that encoder as told.
"""

import glob
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple

import rx_media

VENDORS = {"0x8086": "intel", "0x1002": "amd", "0x10de": "nvidia"}
LIBVA_DRIVERS = {"intel": "iHD", "amd": "radeonsi"}
NAMES = {"qsv": "Intel Quick Sync (av1_qsv)", "vaapi": "VA-API (av1_vaapi)", "nvenc": "NVIDIA NVENC (av1_nvenc)",
         "svt": "CPU (libsvtav1)"}
ENCODERS = ("auto", "qsv", "vaapi", "nvenc", "svt")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def gpus(dev_root: str = "/dev", sys_root: str = "/sys") -> List[Dict[str, Any]]:
    """The GPUs this container can use: [{'node': '/dev/dri/renderD128', 'vendor': 'intel'}, ...]."""
    out = []
    for node in sorted(glob.glob(os.path.join(dev_root, "dri", "renderD*"))):
        vendor_id = _read(os.path.join(sys_root, "class", "drm", os.path.basename(node), "device", "vendor"))
        out.append({"node": node, "vendor": VENDORS.get(vendor_id or "", "unknown"), "vendor_id": vendor_id})
    if os.path.exists(os.path.join(dev_root, "nvidiactl")) or glob.glob(os.path.join(dev_root, "nvidia[0-9]*")):
        if not any(g["vendor"] == "nvidia" for g in out):
            out.append({"node": None, "vendor": "nvidia", "vendor_id": "0x10de"})
    return out


def candidates(found: List[Dict[str, Any]]) -> List[Tuple[str, Optional[str], str]]:
    """(encoder, device, vendor) to try, best first; the CPU encoder always comes last."""
    out: List[Tuple[str, Optional[str], str]] = []
    for g in found:
        if g["vendor"] == "intel":
            out += [("qsv", g["node"], "intel"), ("vaapi", g["node"], "intel")]
    for g in found:
        if g["vendor"] == "nvidia":
            out.append(("nvenc", None, "nvidia"))
            break
    for g in found:
        if g["vendor"] in ("amd", "unknown") and g["node"]:
            out.append(("vaapi", g["node"], g["vendor"]))
    out.append(("svt", None, "cpu"))
    return out


def test_cmd(encoder: str, device: Optional[str]) -> List[str]:
    """A one-second AV1 test encode of a generated picture, thrown away."""
    return (["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"] + rx_media.hw_init_args(encoder, device)
            + ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-frames:v", "10"]
            + rx_media.encoder_args(encoder, 30, device) + ["-f", "null", "-"])


def _run(cmd: List[str], env: Dict[str, str]) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=60, env=env)
        return p.returncode, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _env_for(vendor: str) -> Dict[str, str]:
    env = dict(os.environ)
    if vendor in LIBVA_DRIVERS and not os.environ.get("RX_LIBVA_DRIVER"):
        env["LIBVA_DRIVER_NAME"] = LIBVA_DRIVERS[vendor]
    elif os.environ.get("RX_LIBVA_DRIVER"):
        env["LIBVA_DRIVER_NAME"] = os.environ["RX_LIBVA_DRIVER"]
    return env


def detect(cfg: Dict[str, Any], run: Optional[Callable[[List[str], Dict[str, str]], Tuple[int, str]]] = None,
           found: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Settle cfg['encoder'] and cfg['device'] from cfg['encoder_pref'] and what works here; returns what was
    found and tried (shown on the Settings screen). Sets LIBVA_DRIVER_NAME for the chosen GPU's vendor."""
    run = run or _run
    found = gpus() if found is None else found
    pref = cfg.get("encoder_pref") or cfg.get("encoder") or "auto"
    tried = []
    chosen = None
    if pref == "auto":
        for enc, dev, vendor in candidates(found):
            rc, err = run(test_cmd(enc, dev), _env_for(vendor))
            line = next((ln for ln in err.splitlines() if ln.strip()), "")[:200]
            tried.append({"encoder": enc, "device": dev, "ok": rc == 0, "error": None if rc == 0 else line})
            if rc == 0:
                chosen = (enc, dev, vendor)
                break
        if chosen is None:  # not even the CPU encoder ran: keep svt so the errors say why
            chosen = ("svt", None, "cpu")
    else:
        dev = cfg.get("device")
        vendor = next((g["vendor"] for g in found if g["node"] == dev), "intel" if pref == "qsv" else "unknown")
        if pref == "nvenc":
            vendor = "nvidia"
        elif pref == "qsv":
            vendor = "intel"
        chosen = (pref, dev if pref in ("qsv", "vaapi") else None, vendor if pref != "svt" else "cpu")
    enc, dev, vendor = chosen
    cfg["encoder"] = enc
    if dev:
        cfg["device"] = dev
    if vendor in LIBVA_DRIVERS or os.environ.get("RX_LIBVA_DRIVER"):
        os.environ["LIBVA_DRIVER_NAME"] = _env_for(vendor)["LIBVA_DRIVER_NAME"]
    info = {"pref": pref, "encoder": enc, "device": dev, "vendor": vendor, "name": NAMES.get(enc, enc),
            "gpus": found, "tried": tried}
    cfg["hardware"] = info
    return info
