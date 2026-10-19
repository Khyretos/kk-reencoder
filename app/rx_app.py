"""
kk-reencoder application logic.

This module provides the App class, which acts as the backend for the HTTP API.
It manages state via rx_db, coordinates encoding via rx_worker and rx_stage,
handles scanning via rx_scan, and reports via rx_model.
"""

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import rx_config
import rx_db
import rx_model
import rx_scan
import rx_stage
import rx_sys
import rx_worker

def totals(conn):
    """Return database totals."""
    rows = rx_db.all_files(conn)
    total_original = sum(r["size"] for r in rows)
    total_new = sum(r.get("new_bytes") or r["size"] for r in rows)
    return {"original": total_original, "new": total_new}


def now():
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class App:
    """Application backend for the re-encoder HTTP API."""

    def __init__(self, conn, cfg, worker=None, sampler=None):
        self.conn = conn
        self.cfg = cfg
        self.worker = worker
        self.sampler = sampler if sampler is not None else rx_sys.Sampler()

    def status(self) -> Dict[str, Any]:
        """Return current application status."""
        paused = rx_db.get_state(self.conn, "paused", "0") == "1"
        jobs = []
        blocked = None
        scanning = False
        if self.worker:
            jobs = self.worker.jobs_list()
            blocked = self.worker.blocked
            scanning = getattr(self.worker, 'scanning', False)

        # a job's ffmpeg processes name either the file itself or the job's own work folder
        keys = {j["path"]: [j["path"], hashlib.sha1(j["path"].encode()).hexdigest()[:12]] for j in jobs}
        sample = self.sampler.sample([k for ks in keys.values() for k in ks])
        sys_info = {k: v for k, v in sample.items() if k != "jobs"}
        for job in jobs:
            parts = [sample["jobs"].get(k) or {} for k in keys[job["path"]]]
            cpus = [p["cpu_pct"] for p in parts if p.get("cpu_pct") is not None]
            job["cpu_pct"] = sum(cpus) if cpus else None
            job["rss_bytes"] = sum(p.get("rss_bytes") or 0 for p in parts)

        return {
            "mode": self.cfg["mode"],
            "encoder": self.cfg["encoder"],
            "target_vmaf": self.cfg["target_vmaf"],
            "paused": paused,
            "scanning": scanning,
            "jobs": jobs,
            "blocked": blocked,
            "staged_bytes": rx_db.staged_bytes(self.conn),
            "max_staged_gb": self.cfg.get("max_staged_gb", 1000),
            "sys": sys_info,
            "parallel": max(1, min(5, int(rx_db.get_state(self.conn, "parallel", str(self.cfg.get("parallel", 2)))))),
            "totals": self.totals(),
        }

    TOTAL_KEYS = ("files", "pending", "encoded", "predicted", "staged", "confirmed", "skipped", "failed",
                  "original_bytes", "current_bytes", "projected_bytes", "extra_bytes")

    def totals(self) -> Dict[str, Any]:
        """Sums over every folder, plus the net saving now and when everything is done."""
        t = {k: 0 for k in self.TOTAL_KEYS}
        for f in rx_db.folders(self.conn):
            for k in self.TOTAL_KEYS:
                t[k] += f.get(k) or 0
        t["saved_bytes"] = t["original_bytes"] - t["current_bytes"] - t["extra_bytes"]
        t["projected_saved_bytes"] = t["original_bytes"] - t["projected_bytes"]
        t.update(self.estimate())
        return t

    def estimate(self) -> Dict[str, Any]:
        """Whole-library estimate: files still waiting for a measurement are expected to shrink like the
        files already measured in the same library (or the whole library when that library has none)."""
        rows = self.conn.execute("""
            SELECT library,
                   SUM(CASE WHEN status IN ('predicted', 'staged', 'confirmed', 'encoded') AND new_bytes IS NOT NULL
                            THEN size ELSE 0 END) AS m_size,
                   SUM(CASE WHEN status IN ('predicted', 'staged', 'confirmed', 'encoded') AND new_bytes IS NOT NULL
                            THEN new_bytes ELSE 0 END) AS m_new,
                   SUM(CASE WHEN status IN ('predicted', 'staged', 'confirmed', 'encoded') AND new_bytes IS NOT NULL
                            THEN 1 ELSE 0 END) AS m_files,
                   SUM(CASE WHEN status IN ('pending', 'running') THEN size ELSE 0 END) AS waiting,
                   SUM(CASE WHEN status NOT IN ('predicted', 'staged', 'confirmed', 'encoded', 'pending', 'running')
                              OR (status IN ('predicted', 'staged', 'confirmed', 'encoded') AND new_bytes IS NULL)
                            THEN size ELSE 0 END) AS stays
            FROM files GROUP BY library""").fetchall()
        all_size = sum(r["m_size"] or 0 for r in rows)
        all_new = sum(r["m_new"] or 0 for r in rows)
        overall = all_new / all_size if all_size else 1.0
        estimated = 0
        original = 0
        for r in rows:
            ratio = (r["m_new"] / r["m_size"]) if r["m_size"] else overall
            estimated += (r["m_new"] or 0) + int((r["waiting"] or 0) * ratio) + (r["stays"] or 0)
            original += (r["m_size"] or 0) + (r["waiting"] or 0) + (r["stays"] or 0)
        return {"measured_files": sum(r["m_files"] or 0 for r in rows), "estimated_bytes": estimated,
                "estimated_saved_bytes": original - estimated}

    def folders(self) -> List[Dict[str, Any]]:
        """Return list of folder summaries."""
        return rx_db.folders(self.conn)

    def report(self, library: str, folder: str) -> Dict[str, Any]:
        """Generate a detailed report for a library/folder."""
        files = rx_db.folder_files(self.conn, library, folder)
        return rx_model.folder_report(
            library, folder, files, now()
        )

    def set_paused(self, paused: bool) -> Dict[str, Any]:
        """Pause or unpause all processing."""
        val = "1" if paused else "0"
        rx_db.set_state(self.conn, "paused", val)
        return self.status()

    def set_parallel(self, n: int) -> Dict[str, Any]:
        """Set parallelism level, clamped to 1..5."""
        n = max(1, min(5, n))
        rx_db.set_state(self.conn, "parallel", str(n))
        return self.status()

    def set_folder(self, library: str, folder: str, priority: Optional[int] = None,
                   paused: Optional[bool] = None, encode: Optional[bool] = None) -> Dict[str, Any]:
        """Change a folder's priority or pause; returns that folder's summary."""
        rx_db.set_folder(self.conn, library, folder, priority=priority, paused=paused, encode=encode)
        for f in rx_db.folders(self.conn):
            if f["library"] == library and f["name"] == folder:
                return f
        raise KeyError(f"{library}/{folder}")

    def encode_many(self, items: List[Dict[str, Any]], encode: bool) -> Dict[str, Any]:
        """Mark (or unmark) several series for re-encoding at once."""
        n = 0
        for it in items:
            rx_db.set_folder(self.conn, it["library"], it["folder"], encode=bool(encode))
            n += 1
        return {"updated": n}

    def rescan(self) -> None:
        """Start a background scan."""
        t = threading.Thread(target=rx_scan.scan, args=(self.conn, self.cfg["roots"], self.cfg))
        t.daemon = True
        t.start()

    ACTIONS = {
        # action: (status it needs, rx_stage step, status afterwards)
        "confirm": ("staged", rx_stage.confirm, "confirmed"),
        "undo": ("confirmed", rx_stage.undo, "staged"),
        "finalize": ("confirmed", rx_stage.finalize, "encoded"),
        "discard": ("staged", rx_stage.discard, "pending"),
    }

    def file_action(self, path: str, action: str) -> Dict[str, Any]:
        """Confirm, undo, finalize or discard one re-encode; returns the updated row."""
        if action not in self.ACTIONS:
            raise ValueError(f"unknown action: {action}")
        row = rx_db.get_file(self.conn, path)
        if row is None:
            raise KeyError(path)
        _, step, after = self.ACTIONS[action]
        step(path)  # raises rx_stage.StageError when the files are not in the needed state
        if action == "discard":
            rx_db.set_result(self.conn, path, status=after, new_bytes=None, quality=None, vmaf=None,
                             codec_after=None, encoded_at=None, reason="discarded")
        else:
            rx_db.set_result(self.conn, path, status=after)
        try:
            rx_worker.write_report(self.conn, row["library"], row["folder"], self.cfg, in_media=(action == "finalize"))
        except OSError as e:  # the swap is done; a report that cannot be written must not undo that
            print(f"report for {row['library']}/{row['folder']} not written: {e}", file=sys.stderr)
        return rx_db.get_file(self.conn, path)

    def folder_action(self, library: str, folder: str, action: str) -> Dict[str, Any]:
        """Perform an action on all files in a folder."""
        done = 0
        failed = []

        # Determine required status based on action
        required_status = {
            "confirm": "staged",
            "discard": "staged",
            "undo": "confirmed",
            "finalize": "confirmed",
        }.get(action)

        if required_status is None:
            raise ValueError(f"Unknown action: {action}")

        files = rx_db.files_with_status(self.conn, required_status, library, folder)
        for f in files:
            try:
                self.file_action(f["path"], action)
                done += 1
            except Exception as e:
                failed.append({"path": f["path"], "error": str(e)})

        return {"done": done, "failed": failed}

    def frames(self, path: str) -> Dict[str, Any]:
        """Comparison frame pairs of a re-encode: [{t, a (original), b (re-encode)}] as /frames URLs."""
        d = rx_stage.frames_dir(self.cfg["frames_dir"], path)
        try:
            with open(os.path.join(d, "frames.json")) as fh:
                times = json.load(fh).get("times", [])
        except (OSError, ValueError):
            return {"pairs": []}
        name = os.path.basename(d)
        pairs = [{"t": t, "a": f"/frames/{name}/{i}-a.jpg", "b": f"/frames/{name}/{i}-b.jpg"}
                 for i, t in enumerate(times)
                 if os.path.exists(os.path.join(d, f"{i}-a.jpg")) and os.path.exists(os.path.join(d, f"{i}-b.jpg"))]
        return {"pairs": pairs}

    def libraries(self) -> List[Dict[str, Any]]:
        """Folder summaries summed per library, sorted by library."""
        keys = ("files", "pending", "encoded", "predicted", "staged", "confirmed", "skipped",
                "original_bytes", "current_bytes", "projected_bytes", "extra_bytes")
        out: Dict[str, Dict[str, Any]] = {}
        for f in rx_db.folders(self.conn):
            lib = out.setdefault(f["library"], dict({k: 0 for k in keys}, library=f["library"], folders=0))
            lib["folders"] += 1
            for k in keys:
                lib[k] += f.get(k) or 0
        return [out[k] for k in sorted(out)]

    def overview(self) -> Dict[str, Any]:
        """Everything the overview page draws."""
        done = ("encoded", "confirmed", "staged")
        top = []
        for f in rx_db.folders(self.conn):
            saved = f["original_bytes"] - f["projected_bytes"]
            pct = round(saved / f["original_bytes"] * 100, 1) if f["original_bytes"] else 0.0
            top.append({"library": f["library"], "name": f["name"], "original_bytes": f["original_bytes"],
                        "projected_bytes": f["projected_bytes"], "saved_bytes": saved, "saved_pct": pct})
        top.sort(key=lambda x: -x["saved_bytes"])
        before: Dict[str, int] = {}
        after: Dict[str, int] = {}
        for r in self.conn.execute("SELECT codec, codec_after, status, size, new_bytes FROM files"):
            before[r["codec"] or "?"] = before.get(r["codec"] or "?", 0) + (r["size"] or 0)
            if r["status"] in done and r["new_bytes"] is not None:
                c, b = r["codec_after"] or "av1", r["new_bytes"]
            else:
                c, b = r["codec"] or "?", r["size"] or 0
            after[c] = after.get(c, 0) + b
        history, total = [], 0
        q = ("SELECT substr(encoded_at, 1, 10) AS day, COUNT(*) AS n, SUM(size - new_bytes) AS saved FROM files"
             " WHERE status IN ('encoded', 'confirmed', 'staged') AND encoded_at IS NOT NULL AND new_bytes IS NOT NULL"
             " GROUP BY day ORDER BY day")
        for r in self.conn.execute(q):
            total += r["saved"] or 0
            history.append({"date": r["day"], "files": r["n"], "saved_bytes": total})
        return {"totals": self.totals(), "libraries": self.libraries(), "top": top[:10],
                "codecs": {"before": before, "after": after}, "history": history}
