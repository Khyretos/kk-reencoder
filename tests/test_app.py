"""Tests for app/rx_app.py: the App object behind the HTTP API, without a server."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_app  # noqa: E402
import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_stage  # noqa: E402


class FakeWorker:
    blocked = None

    def jobs_list(self):
        return [{"path": "/m/Anime/Show/1.mkv", "folder": "Show", "library": "Anime", "phase": "encoding",
                 "percent": 40.0, "fps": 90.0, "eta_s": 60.0}]


class FakeSampler:
    def __init__(self):
        self.asked = None

    def sample(self, paths):
        self.asked = paths
        return {"cpu_pct": 150.0, "cpu_share": 37.5, "cpus": 4, "mem_bytes": 100, "mem_limit": 1000,
                "jobs": {p: {"cpu_pct": 120.0, "rss_bytes": 50, "procs": 1} for p in paths}}


def add(conn, path, folder, status, size=1000, new=None, codec="h264", at=None):
    rx_db.upsert_file(conn, {"path": path, "library": "Anime", "folder": folder, "size": size, "mtime": 1.0,
                             "codec": codec, "height": 1080, "bitrate_kbps": 8000, "duration_s": 100.0,
                             "status": "pending", "reason": None})
    if status != "pending":
        rx_db.set_result(conn, path, status=status, new_bytes=new, encoded_at=at,
                         codec_after="av1" if new else None)


class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = rx_db.connect(":memory:")
        add(self.conn, "/m/Anime/Show/0.mkv", "Show", "encoded", new=400, at="2026-10-18T10:00:00Z")
        add(self.conn, "/m/Anime/Show/1.mkv", "Show", "pending")
        add(self.conn, "/m/Anime/Film/2.mkv", "Film", "skipped", codec="av1")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": "/m/Anime", "RX_FRAMES_DIR": self.tmp,
                                              "RX_REPORT_DIR": os.path.join(self.tmp, "reports")})
        self.sampler = FakeSampler()
        self.app = rx_app.App(self.conn, self.cfg, worker=FakeWorker(), sampler=self.sampler)

    def test_status(self):
        s = self.app.status()
        self.assertEqual(s["totals"]["files"], 3)
        self.assertEqual(s["totals"]["saved_bytes"], 600)
        self.assertEqual(s["jobs"][0]["percent"], 40.0)
        # the job card gets the CPU and memory of its ffmpeg
        self.assertEqual(s["jobs"][0]["cpu_pct"], 240.0)  # the file's ffmpeg + the work folder's
        self.assertEqual(s["jobs"][0]["rss_bytes"], 100)
        self.assertEqual(self.sampler.asked[0], "/m/Anime/Show/1.mkv")
        self.assertEqual(s["sys"]["cpu_pct"], 150.0)
        self.assertEqual(s["parallel"], 2)

    def test_estimate_extrapolates_from_measured_files(self):
        # Show/0 measured: 1000 -> 400 (ratio 0.4); Show/1 pending is estimated at that ratio; Film is skipped
        t = self.app.status()["totals"]
        self.assertEqual(t["measured_files"], 1)
        self.assertEqual(t["estimated_bytes"], 400 + 400 + 1000)
        self.assertEqual(t["estimated_saved_bytes"], 3000 - 1800)

    def test_queue_and_reorder(self):
        for i, name in enumerate(["Alpha", "Beta", "Gamma"]):
            add(self.conn, f"/m/Anime/{name}/1.mkv", name, "pending")
        q = self.app.queue()
        names = [x["name"] for x in q["items"]]
        self.assertEqual(names, ["Alpha", "Beta", "Gamma", "Show"])  # alphabetical, only series with work left
        self.assertEqual(q["items"][0]["action"], "measure")  # report mode: measure only
        self.assertEqual(q["total"], 4)
        self.app.reorder([{"library": "Anime", "folder": "Gamma"}, {"library": "Anime", "folder": "Alpha"}])
        self.assertEqual([x["name"] for x in self.app.queue()["items"]], ["Gamma", "Alpha", "Beta", "Show"])
        # taking a series out of the queue pauses it; it shows under paused and comes back on resume
        self.app.set_folder("Anime", "Beta", paused=True)
        q = self.app.queue()
        self.assertNotIn("Beta", [x["name"] for x in q["items"]])
        self.assertEqual([x["name"] for x in q["paused"]], ["Beta"])
        self.app.cfg["mode"] = "manual"
        self.app.set_folder("Anime", "Show", encode=True)
        q = self.app.queue()
        self.assertEqual(q["items"][0]["name"], "Show")  # ticked series are encoded first
        self.assertEqual(q["items"][0]["action"], "encode")
        self.assertEqual(q["items"][1]["action"], "measure")

    def test_overview(self):
        o = self.app.overview()
        self.assertEqual(o["codecs"]["before"], {"h264": 2000, "av1": 1000})
        self.assertEqual(o["codecs"]["after"], {"av1": 1400, "h264": 1000})
        self.assertEqual(o["history"], [{"date": "2026-10-18", "files": 1, "saved_bytes": 600}])
        self.assertEqual(o["top"][0], {"library": "Anime", "name": "Show", "original_bytes": 2000,
                                       "projected_bytes": 1400, "saved_bytes": 600, "saved_pct": 30.0})

    def test_file_action_and_errors(self):
        p = os.path.join(self.tmp, "Show", "e.mkv")
        os.makedirs(os.path.dirname(rx_stage.staged_path(p)))
        open(p, "wb").write(b"o" * 1000)
        open(rx_stage.staged_path(p), "wb").write(b"n" * 300)
        add(self.conn, p, "Show", "staged", new=300)
        self.assertEqual(self.app.file_action(p, "confirm")["status"], "confirmed")
        with self.assertRaises(rx_stage.StageError):
            self.app.file_action(p, "confirm")
        with self.assertRaises(KeyError):
            self.app.file_action("/nope.mkv", "confirm")
        with self.assertRaises(ValueError):
            self.app.file_action(p, "explode")
        self.assertEqual(self.app.folder_action("Anime", "Show", "undo"), {"done": 1, "failed": []})
        self.assertEqual(os.path.getsize(p), 1000)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "reports", "Anime", "Show", "encoding_report.json")))

    def test_settings(self):
        self.assertTrue(self.app.set_paused(True)["paused"])
        self.assertEqual(self.app.set_parallel(0)["parallel"], 1)
        self.assertEqual(self.app.set_folder("Anime", "Show", priority=4)["priority"], 4)


if __name__ == "__main__":
    unittest.main()
