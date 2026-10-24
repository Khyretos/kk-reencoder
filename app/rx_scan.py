"""
app/rx_scan.py

Scans media libraries to discover video files, probes them for metadata, and
records their status in the database. It walks library roots, skips hidden
directories (starting with '.'), and avoids re-probing files whose size and
mtime match existing records.

It uses rx_db for persistence, rx_model for folder keys and skip reasons,
and an optional probe function (defaulting to rx_media.probe) to gather
video information.
"""

import os
from typing import Optional, Set

VIDEO_EXT = ('.mkv', '.mp4', '.m4v', '.avi', '.ts', '.mov', '.wmv')


def _walk(top: str):
    """Video files under top, directories and files sorted case-insensitively, hidden ones skipped."""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted((d for d in dirnames if not d.startswith(".")), key=str.lower)
        for name in sorted(filenames, key=str.lower):
            if not name.startswith(".") and name.lower().endswith(VIDEO_EXT):
                yield os.path.join(dirpath, name)


def _record(conn, root: str, path: str, cfg: dict, probe) -> None:
    """Probe a new or changed file and store it as pending or skipped (unchanged files are left alone)."""
    import rx_db
    import rx_model

    st = os.stat(path)
    known = rx_db.get_file(conn, path)
    unchanged = known and known["size"] == st.st_size and known["mtime"] == st.st_mtime
    if unchanged and not (known["status"] == "skipped" and known["reason"] in rx_model.SETTING_REASONS):
        return
    info = probe(path)
    if unchanged:
        # skipped because of a setting (HDR, codecs, height, bitrate): look again, the setting may have changed
        reason = rx_model.skip_reason(info, cfg) if info is not None else known["reason"]
        if reason != known["reason"]:
            rx_db.set_result(conn, path, status="pending" if reason is None else "skipped", reason=reason)
        return
    rec = {"path": path, "library": os.path.basename(root), "folder": rx_model.folder_key(root, path),
           "size": st.st_size, "mtime": st.st_mtime, "codec": None, "height": None, "bitrate_kbps": None,
           "duration_s": None, "status": "skipped", "reason": "unreadable"}
    if info is not None:
        reason = rx_model.skip_reason(info, cfg)
        rec.update(codec=info["codec"], height=info["height"], bitrate_kbps=info["bitrate_kbps"],
                   duration_s=info["duration_s"], status="pending" if reason is None else "skipped", reason=reason)
    rx_db.upsert_file(conn, rec)


KEEP = ("encoded", "staged", "confirmed", "running")  # rows that stay even when the file is not found


def scan(conn, roots: list, cfg: dict, probe: Optional[callable] = None, only: Optional[tuple] = None) -> int:
    """Find video files and record new or changed ones; returns how many video files were seen.

    only=(library, folder) scans one series or movie folder. A library root (or the folder) that does not
    exist is skipped and none of its rows are removed: an unmounted disk must not look like deleted files.
    """
    import rx_db

    if probe is None:
        import rx_media
        probe = rx_media.probe

    if only is not None:
        library, folder = only
        root = next((r for r in roots if os.path.basename(r) == library), None)
        top = os.path.join(root, folder) if root else None
        if not top or not os.path.isdir(top):
            return 0
        seen = set()
        for path in _walk(top):
            seen.add(path)
            _record(conn, root, path, cfg, probe)
        rows = conn.execute("SELECT path, status FROM files WHERE library = ? AND folder = ?", (library, folder)).fetchall()
        for r in rows:
            if r["path"] not in seen and r["status"] not in KEEP:
                conn.execute("DELETE FROM files WHERE path = ?", (r["path"],))
        conn.commit()
        return len(seen)

    seen = set()
    found = 0
    for root in roots:
        if not os.path.isdir(root):
            # keep every row of a library whose folder is missing right now
            seen.update(r["path"] for r in conn.execute("SELECT path FROM files WHERE library = ?",
                                                        (os.path.basename(root),)).fetchall())
            continue
        for path in _walk(root):
            seen.add(path)
            found += 1
            _record(conn, root, path, cfg, probe)
    rx_db.remove_missing(conn, seen)
    return found
