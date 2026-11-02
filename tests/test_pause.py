"""Pause freezes the ffmpeg commands already running (SIGSTOP) and holds back new ones (rx_search.default_run)."""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_search  # noqa: E402


def proc_state(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return None


class Pause(unittest.TestCase):
    def setUp(self):
        rx_search.resume_all()  # other tests pause the App (a module-wide switch)

    def tearDown(self):
        rx_search.resume_all()

    def test_pause_freezes_running_and_holds_new(self):
        out = {}
        t = threading.Thread(target=lambda: out.update(r=rx_search.default_run(["sleep", "1.5"])))
        t.start()
        for _ in range(50):
            if rx_search._procs:
                break
            time.sleep(0.02)
        proc = next(iter(rx_search._procs))
        rx_search.pause_all()
        time.sleep(0.2)
        self.assertEqual(proc_state(proc.pid), "T")  # stopped
        self.assertTrue(rx_search.is_paused())
        time.sleep(1.6)  # longer than the command: frozen, so it has not finished
        self.assertTrue(t.is_alive())

        # a new command waits while paused
        started = threading.Event()
        t2 = threading.Thread(target=lambda: (rx_search.default_run(["true"]), started.set()))
        t2.start()
        time.sleep(0.2)
        self.assertFalse(started.is_set())

        rx_search.resume_all()
        t.join(5)
        t2.join(5)
        self.assertFalse(t.is_alive())
        self.assertTrue(started.is_set())
        self.assertEqual(out["r"][0], 0)
        self.assertEqual(rx_search._procs, set())


if __name__ == "__main__":
    unittest.main()
