"""Tests for app/server.py: config from the environment and the HTTP API (no worker thread)."""

import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_db  # noqa: E402
import server  # noqa: E402


class Config(unittest.TestCase):
    def test_defaults(self):
        c = server.config_from_env({"RX_ROOTS": "/media/Anime,/media/Movies"})
        self.assertEqual(c["roots"], ["/media/Anime", "/media/Movies"])
        self.assertEqual(c["mode"], "report")
        self.assertEqual(c["encoder"], "qsv")
        self.assertEqual(c["target_vmaf"], 95.0)
        self.assertEqual(c["max_ratio"], 0.8)
        self.assertEqual(c["samples"], 3)
        self.assertEqual(c["parallel"], 2)
        self.assertEqual(c["filters"], {"Anime": "deband", "Cartoons": "deband"})
        self.assertEqual(c["min_kbps"]["1080"], 2500)
        self.assertTrue(c["skip_hdr"])
        self.assertIn("h264", c["codecs"])

    def test_overrides_and_bad_mode(self):
        c = server.config_from_env({"RX_ROOTS": "/a", "RX_ENCODER": "svt", "RX_TARGET_VMAF": "93.5",
                                    "RX_SKIP_HDR": "false", "RX_CODECS": "h264, vc1"})
        self.assertEqual(c["encoder"], "svt")
        self.assertEqual(c["target_vmaf"], 93.5)
        self.assertFalse(c["skip_hdr"])
        self.assertEqual(c["codecs"], ["h264", "vc1"])
        with self.assertRaises(ValueError):
            server.config_from_env({"RX_ROOTS": "/a", "RX_MODE": "delete"})


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = rx_db.connect(":memory:")
        for i, (folder, status, new) in enumerate([("Show", "predicted", 400), ("Show", "pending", None),
                                                   ("Film", "skipped", None)]):
            rx_db.upsert_file(cls.conn, {"path": f"/m/Anime/{folder}/{i}.mkv", "library": "Anime", "folder": folder,
                                         "size": 1000, "mtime": 1.0, "codec": "h264", "height": 1080,
                                         "bitrate_kbps": 8000, "duration_s": 100.0, "status": "pending", "reason": None})
            if status != "pending":
                rx_db.set_result(cls.conn, f"/m/Anime/{folder}/{i}.mkv", status=status, new_bytes=new)
        import tempfile
        tmp = tempfile.mkdtemp()
        cfg = server.config_from_env({"RX_ROOTS": "/m/Anime", "RX_FRAMES_DIR": tmp, "RX_REPORT_DIR": tmp + "/reports"})
        cls.app = server.App(cls.conn, cfg, worker=None)
        cls.httpd = server.make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_health(self):
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_index(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)

    def test_status(self):
        s = json.loads(self.get("/api/status")[2])
        self.assertEqual(s["mode"], "report")
        self.assertFalse(s["paused"])
        self.assertEqual(s["totals"]["files"], 3)
        self.assertEqual(s["totals"]["original_bytes"], 3000)
        self.assertEqual(s["totals"]["projected_bytes"], 2400)
        self.assertEqual(s["totals"]["pending"], 1)
        self.assertEqual(s["jobs"], [])
        self.assertEqual(s["parallel"], 2)

    def test_folders_and_report(self):
        fs = json.loads(self.get("/api/folders")[2])
        self.assertEqual([f["name"] for f in fs], ["Film", "Show"])
        r = json.loads(self.get("/api/report?library=Anime&folder=Show")[2])
        self.assertEqual(r["totals"]["files"], 2)
        self.assertEqual(r["totals"]["predicted"], 1)

    def test_pause_and_priority(self):
        self.assertTrue(self.post("/api/pause", {"paused": True})["paused"])
        self.assertTrue(json.loads(self.get("/api/status")[2])["paused"])
        self.post("/api/pause", {"paused": False})
        self.post("/api/folder", {"library": "Anime", "folder": "Show", "priority": 3, "paused": True})
        show = [f for f in json.loads(self.get("/api/folders")[2]) if f["name"] == "Show"][0]
        self.assertEqual(show["priority"], 3)
        self.assertTrue(show["paused"])
        self.post("/api/folder", {"library": "Anime", "folder": "Show", "priority": 0, "paused": False})

    def test_parallel_setting(self):
        self.assertEqual(self.post("/api/parallel", {"parallel": 4})["parallel"], 4)
        self.assertEqual(json.loads(self.get("/api/status")[2])["parallel"], 4)
        self.assertEqual(self.post("/api/parallel", {"parallel": 9})["parallel"], 5)  # clamped to 1..5
        self.post("/api/parallel", {"parallel": 2})

    def test_overview(self):
        o = json.loads(self.get("/api/overview")[2])
        self.assertEqual(o["totals"]["files"], 3)
        self.assertEqual([l["library"] for l in o["libraries"]], ["Anime"])
        self.assertEqual(o["top"][0]["name"], "Show")
        self.assertEqual(o["top"][0]["saved_bytes"], 600)
        self.assertEqual(o["codecs"]["before"], {"h264": 3000})
        self.assertIsInstance(o["history"], list)

    def test_status_has_sys(self):
        s = json.loads(self.get("/api/status")[2])
        self.assertIn("sys", s)
        for k in ("cpu_pct", "mem_bytes", "mem_limit"):
            self.assertIn(k, s["sys"])

    def test_overview_page(self):
        status, ctype, body = self.get("/overview")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)

    def test_libraries(self):
        libs = json.loads(self.get("/api/libraries")[2])
        self.assertEqual([l["library"] for l in libs], ["Anime"])
        self.assertEqual(libs[0]["original_bytes"], 3000)
        self.assertEqual(libs[0]["projected_bytes"], 2400)
        self.assertEqual(libs[0]["folders"], 2)

    def test_zz_file_actions(self):
        import tempfile
        import rx_stage
        d = tempfile.mkdtemp()
        p = os.path.join(d, "Show", "e9.mkv")
        os.makedirs(os.path.dirname(rx_stage.staged_path(p)))
        open(p, "wb").write(b"o" * 1000)
        open(rx_stage.staged_path(p), "wb").write(b"n" * 300)
        rx_db.upsert_file(self.conn, {"path": p, "library": "Anime", "folder": "Show9", "size": 1000, "mtime": 1.0,
                                      "codec": "h264", "height": 1080, "bitrate_kbps": 8000, "duration_s": 100.0,
                                      "status": "pending", "reason": None})
        rx_db.set_result(self.conn, p, status="staged", new_bytes=300)
        r = self.post("/api/file", {"path": p, "action": "confirm"})
        self.assertEqual(r["status"], "confirmed")
        self.assertEqual(os.path.getsize(p), 300)
        self.assertEqual(self.post("/api/file", {"path": p, "action": "undo"})["status"], "staged")
        self.assertEqual(os.path.getsize(p), 1000)
        self.post("/api/file", {"path": p, "action": "confirm"})
        self.assertEqual(self.post("/api/file", {"path": p, "action": "finalize"})["status"], "encoded")
        self.assertEqual(os.path.getsize(p), 300)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/file", {"path": p, "action": "undo"})
        self.assertEqual(e.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/file", {"path": "/etc/passwd", "action": "confirm"})
        self.assertEqual(e.exception.code, 404)

    def test_folder_bulk_action(self):
        r = self.post("/api/folder-action", {"library": "Anime", "folder": "Show", "action": "confirm"})
        self.assertEqual(r, {"done": 0, "failed": []})

    def test_frames(self):
        r = json.loads(self.get("/api/frames?path=/m/Anime/Show/0.mkv")[2])
        self.assertEqual(r, {"pairs": []})
        import rx_stage
        d = rx_stage.frames_dir(self.app.cfg["frames_dir"], "/m/Anime/Show/0.mkv")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "0-a.jpg"), "wb").write(b"\xff\xd8jpg")
        status, ctype, body = self.get(f"/frames/{os.path.basename(d)}/0-a.jpg")
        self.assertEqual((status, ctype, body), (200, "image/jpeg", b"\xff\xd8jpg"))
        with self.assertRaises(urllib.error.HTTPError):
            self.get(f"/frames/{os.path.basename(d)}/../x.jpg")

    def test_unknown_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/nope")
        self.assertEqual(e.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/static/../server.py")
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
