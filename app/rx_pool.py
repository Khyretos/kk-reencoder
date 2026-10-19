"""
app/rx_pool.py

Encode pool for kk-reencoder. Manages a thread pool that re-encodes video files to AV1.
Runs up to N files in parallel, respecting pause state and storage limits.
"""

import threading
import traceback
import time
from typing import Any, Callable, Dict, List, Optional

import rx_db


class Worker:
    """Background worker pool for encoding videos."""

    def __init__(
        self,
        conn: Any,
        cfg: Dict[str, Any],
        run: Optional[Callable] = None,
        probe: Optional[Callable] = None,
        process: Optional[Callable] = None,
        scan: Optional[Callable] = None,
    ):
        import rx_media
        import rx_worker
        self.conn = conn
        self.cfg = cfg
        self.run = run or rx_worker.default_run
        self.probe = probe or rx_media.probe
        self.process = process or rx_worker.process
        self.scan = scan or rx_worker.scan

        self.lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self.blocked: Optional[str] = None
        self.scanning: bool = False
        self._stop = threading.Event()
        self._scan_now = threading.Event()
        self._scan_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the scheduler and scan threads."""
        rx_db.reset_running(self.conn)
        self._start_scheduler_thread()
        self._start_scan_thread()

    def stop(self) -> None:
        """Signal the scheduler and scan threads to stop."""
        self._stop.set()
        if self._scan_thread is not None:
            self._scan_thread.join(timeout=2.0)
            self._scan_thread = None

    def jobs_list(self) -> List[Dict[str, Any]]:
        """Return a copy of current job progress dicts."""
        with self.lock:
            return list(self._jobs.values())

    @staticmethod
    def parallel(conn: Any, cfg: Dict[str, Any]) -> int:
        """Get parallel limit from DB or config, clamped to 1..5."""
        raw = rx_db.get_state(conn, "parallel", str(cfg.get("parallel", 2)))
        try:
            val = int(raw)
        except ValueError:
            val = 2
        return max(1, min(5, val))

    def _start_scheduler_thread(self) -> None:
        """Start the background scheduler thread."""
        if not hasattr(self, "_scheduler_thread") or self._scheduler_thread is None or not self._scheduler_thread.is_alive():
            self._scheduler_thread = threading.Thread(target=self._scheduler_loop, args=(), daemon=True)
            self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        """Hand out files to encode, up to parallel() at a time, also while a scan runs."""
        while not self._stop.is_set():

            # Check pause state
            if rx_db.get_state(self.conn, "paused", "0") == "1":
                self.blocked = None
                self._stop.wait(self.cfg.get("idle_s", 10))
                continue

            # Check space guard
            max_gb = self.cfg.get("max_staged_gb", 1000)
            limit_bytes = max_gb * 1024**3
            staged = rx_db.staged_bytes(self.conn)
            if staged > limit_bytes:
                self.blocked = "staged copies use more than the space limit"
                self._stop.wait(self.cfg.get("idle_s", 10))
                continue
            self.blocked = None

            # Check parallel limit
            with self.lock:
                n_jobs = len(self._jobs)
            if n_jobs >= self.parallel(self.conn, self.cfg):
                self._stop.wait(0.05)
                continue

            # Get next file
            f = rx_db.next_file(self.conn)
            if f is None:
                self._stop.wait(self.cfg.get("idle_s", 10))
                continue

            # Mark as running
            rx_db.set_result(self.conn, f["path"], status="running")

            with self.lock:
                self._jobs[f["path"]] = {
                    "path": f["path"],
                    "folder": f["folder"],
                    "library": f["library"],
                    "phase": "starting",
                    "percent": 0.0,
                    "fps": 0.0,
                    "eta_s": None,
                }

            # Start worker thread
            t = threading.Thread(
                target=self._worker_thread,
                args=(self.conn, f, self.cfg, self.run, self.probe),
                daemon=True,
            )
            t.start()

    def request_scan(self) -> None:
        """Signal an immediate scan."""
        self._scan_now.set()

    def _start_scan_thread(self) -> None:
        """Start the background scan thread."""
        if self._scan_thread is None or not self._scan_thread.is_alive():
            self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
            self._scan_thread.start()

    def _scan_loop(self) -> None:
        """Rescan the library in this thread so encoding never waits for a scan."""
        while not self._stop.is_set():
            self.scanning = True
            try:
                self._scan_now.clear()
                self.scan(self.conn, self.cfg["roots"], self.cfg, probe=self.probe)
                rx_db.set_state(self.conn, "last_scan", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            except Exception:
                traceback.print_exc()
            finally:
                self.scanning = False
            self._scan_now.wait(self.cfg.get("rescan_s", 3600))
            if self._stop.is_set():
                return

    def _worker_thread(self, conn: Any, f: Dict[str, Any], cfg: Dict[str, Any], run, probe) -> None:
        """Worker thread for a single file."""
        with self.lock:
            progress = self._jobs[f["path"]]  # the dict jobs_list() reports

        try:
            self.process(conn, f, cfg, run, probe=probe, progress=progress)
        except Exception:
            traceback.print_exc()
        finally:
            with self.lock:
                path = f["path"]
                if path in self._jobs:
                    del self._jobs[path]

            # If still running in DB, mark failed
            cur = rx_db.get_file(conn, path)
            if cur and cur.get("status") == "running":
                rx_db.set_result(conn, path, status="failed", error="stopped")
