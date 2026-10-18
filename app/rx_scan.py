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


def scan(
    conn,
    roots: list[str],
    cfg: dict,
    probe: Optional[callable] = None,
) -> int:
    """
    Scan library roots for video files, probe them, and upsert records.

    Args:
        conn: SQLite connection (rx_db.connect(...)).
        roots: List of root paths to walk.
        cfg: Configuration dict (used by rx_model.skip_reason).
        probe: Function(path) -> info dict or None. Defaults to rx_media.probe.

    Returns:
        Number of video files seen.
    """
    if probe is None:
        import rx_media
        probe = rx_media.probe

    import rx_db
    import rx_model

    seen_paths: Set[str] = set()
    count = 0

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            # Sort case-insensitively and prune hidden dirs in place
            dirnames.sort(key=lambda x: x.lower())
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            filenames.sort(key=lambda x: x.lower())
            for name in filenames:
                if name.startswith('.'):
                    continue

                path = os.path.join(dirpath, name)
                base = os.path.basename(path).lower()
                if not any(base.endswith(ext) for ext in VIDEO_EXT):
                    continue

                count += 1
                seen_paths.add(path)

                # Check if already known with same size/mtime
                existing = rx_db.get_file(conn, path)
                if existing and existing["size"] == os.path.getsize(path) \
                        and existing["mtime"] == os.stat(path).st_mtime:
                    continue

                info = probe(path)
                if info is None:
                    rec = {
                        "path": path,
                        "library": os.path.basename(root),
                        "folder": rx_model.folder_key(root, path),
                        "size": os.path.getsize(path),
                        "mtime": os.stat(path).st_mtime,
                        "codec": None,
                        "height": None,
                        "bitrate_kbps": None,
                        "duration_s": None,
                        "status": "skipped",
                        "reason": "unreadable",
                    }
                else:
                    reason = rx_model.skip_reason(info, cfg)
                    if reason is not None:
                        rec = {
                            "path": path,
                            "library": os.path.basename(root),
                            "folder": rx_model.folder_key(root, path),
                            "size": info["size"],
                            "mtime": os.stat(path).st_mtime,
                            "codec": info["codec"],
                            "height": info["height"],
                            "bitrate_kbps": info["bitrate_kbps"],
                            "duration_s": info["duration_s"],
                            "status": "skipped",
                            "reason": reason,
                        }
                    else:
                        rec = {
                            "path": path,
                            "library": os.path.basename(root),
                            "folder": rx_model.folder_key(root, path),
                            "size": info["size"],
                            "mtime": os.stat(path).st_mtime,
                            "codec": info["codec"],
                            "height": info["height"],
                            "bitrate_kbps": info["bitrate_kbps"],
                            "duration_s": info["duration_s"],
                            "status": "pending",
                            "reason": None,
                        }

                rx_db.upsert_file(conn, rec)

    # Remove DB rows for paths no longer on disk
    rx_db.remove_missing(conn, seen_paths)
    return count
