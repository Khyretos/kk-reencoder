"""Tests for app/rx_pool.py: the encode pool (parallel limit, pause, space guard) with a fake process()."""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_db  # noqa: E402
import rx_pool  # noqa: E402


def add(conn, n):
    for i in range(n):
        rx_db.upsert_file(conn, {"path": f"/m/A/Show/{i:02d}.mkv", "library": "A", "folder": "Show", "size": 1000,
                                 "mtime": 1.0, "codec": "h264", "height": 1080, "bitrate_kbps": 8000,
                                 "duration_s": 100.0, "status": "pending", "reason": None})


class Pool(unittest.TestCase):
    def make(self, n_files, **cfg):
        conn = rx_db.connect(":memory:")
        add(conn, n_files)
        gate = threading.Event()
        seen = []
        active = []
        peak = [0]

        def fake_process(conn_, f, cfg_, run, probe=None, progress=None):
            active.append(f["path"])
            peak[0] = max(peak[0], len(active))
            seen.append(f["path"])
            progress["phase"] = "encoding"
            gate.wait(5)
            rx_db.set_result(conn_, f["path"], status="staged", new_bytes=100)
            active.remove(f["path"])

        base = {"parallel": 2, "idle_s": 0.05, "rescan_s": 0, "roots": [], "max_staged_gb": 1000}
        base.update(cfg)
        pool = rx_pool.Worker(conn, base, process=fake_process, scan=lambda *a, **k: 0)
        return conn, pool, gate, seen, peak

    def wait_for(self, cond, secs=5):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_runs_up_to_parallel(self):
        conn, pool, gate, seen, peak = self.make(5)
        pool.start()
        try:
            self.assertTrue(self.wait_for(lambda: len(pool.jobs_list()) == 2))
            time.sleep(0.2)
            self.assertEqual(len(pool.jobs_list()), 2)
            self.assertEqual(pool.jobs_list()[0]["phase"], "encoding")
            gate.set()
            self.assertTrue(self.wait_for(lambda: len(seen) == 5 and not pool.jobs_list()))
            self.assertEqual(peak[0], 2)
            self.assertEqual(len(set(seen)), 5)  # no file twice
        finally:
            gate.set()
            pool.stop()

    def test_parallel_from_state(self):
        conn, pool, gate, seen, peak = self.make(5)
        rx_db.set_state(conn, "parallel", "3")
        pool.start()
        try:
            self.assertTrue(self.wait_for(lambda: len(pool.jobs_list()) == 3))
        finally:
            gate.set()
            pool.stop()

    def test_paused(self):
        conn, pool, gate, seen, peak = self.make(3)
        rx_db.set_state(conn, "paused", "1")
        gate.set()
        pool.start()
        try:
            time.sleep(0.3)
            self.assertEqual(seen, [])
            rx_db.set_state(conn, "paused", "0")
            self.assertTrue(self.wait_for(lambda: len(seen) == 3))
        finally:
            pool.stop()

    def test_space_guard(self):
        conn, pool, gate, seen, peak = self.make(3, max_staged_gb=0)
        rx_db.set_result(conn, "/m/A/Show/00.mkv", status="staged", new_bytes=10)
        gate.set()
        pool.start()
        try:
            time.sleep(0.3)
            self.assertEqual(seen, [])
            self.assertEqual(pool.blocked, "staged copies use more than the space limit")
        finally:
            pool.stop()

    def test_running_rows_reset_on_start(self):
        conn, pool, gate, seen, peak = self.make(1)
        rx_db.set_result(conn, "/m/A/Show/00.mkv", status="running")
        gate.set()
        pool.start()
        try:
            self.assertTrue(self.wait_for(lambda: len(seen) == 1))
        finally:
            pool.stop()


if __name__ == "__main__":
    unittest.main()
