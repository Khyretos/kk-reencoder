"""Tests for the v0.2 dashboard: per-library progress, the settings added to the Settings screen,
and waiting files leaving the queue when their codec or height is no longer selected."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_settings as st  # noqa: E402
from rx_app import App  # noqa: E402


def add(conn, path, lib, status, size=1000, new=None, codec="h264", height=1080):
    rx_db.upsert_file(conn, {"path": path, "library": lib, "folder": "Show", "size": size, "mtime": 1, "codec": codec,
                             "height": height, "bitrate_kbps": 4000, "duration_s": 100, "status": status, "reason": None})
    if new is not None:
        rx_db.set_result(conn, path, new_bytes=new)


class Worker:
    blocked = None
    scanning = False

    def __init__(self):
        self.scans = 0

    def jobs_list(self):
        return []

    def request_scan(self):
        self.scans += 1


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.conn = rx_db.connect(":memory:")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": "/m/Anime,/m/Movies", "RX_MODE": "stage"})
        self.worker = Worker()
        self.app = App(self.conn, self.cfg, self.worker)

    def test_library_progress_counts_reencoded_files_and_saving(self):
        add(self.conn, "/m/Anime/Show/1.mkv", "Anime", "encoded", 1000, 400)
        add(self.conn, "/m/Anime/Show/2.mkv", "Anime", "confirmed", 1000, 500)
        add(self.conn, "/m/Anime/Show/3.mkv", "Anime", "staged", 1000, 600)
        add(self.conn, "/m/Anime/Show/4.mkv", "Anime", "pending")
        add(self.conn, "/m/Anime/Show/5.mkv", "Anime", "skipped")
        add(self.conn, "/m/Anime/Show/6.mkv", "Anime", "failed")
        add(self.conn, "/m/Anime/Show/7.mkv", "Anime", "predicted", 1000, 300)  # measured, not re-encoded
        add(self.conn, "/m/Movies/Show/m.mkv", "Movies", "pending")
        libs = {l["library"]: l for l in self.app.libraries()}
        a = libs["Anime"]
        self.assertEqual(a["files"], 7)
        self.assertEqual(a["reencoded"], 3)
        self.assertEqual(a["reencoded_pct"], 42.9)
        self.assertEqual(a["reencoded_before_bytes"], 3000)
        self.assertEqual(a["reencoded_after_bytes"], 1500)
        self.assertEqual(a["saved_bytes"], 1500)
        self.assertEqual((a["failed"], a["skipped"], a["pending"], a["predicted"]), (1, 1, 1, 1))
        m = libs["Movies"]
        self.assertEqual((m["reencoded"], m["reencoded_pct"], m["saved_bytes"]), (0, 0.0, 0))

    def test_status_says_version_and_libraries(self):
        s = self.app.status()
        self.assertEqual(s["version"], rx_config.VERSION)
        self.assertEqual(s["libraries"], ["Anime", "Movies"])

    def test_new_settings_round_trip(self):
        v = st.save(self.conn, self.cfg, {"samples": 5, "sample_len": 30, "search_per_folder": 3, "max_height": 1080,
                                          "max_staged_gb": 250, "hw_decode": False, "vmaf_gpu": "off",
                                          "codecs": ["h264", "hevc"]})
        self.assertEqual(self.cfg["codecs"], ["h264", "hevc"])
        self.assertEqual(v["max_staged_gb"], 250.0)
        cfg2 = rx_config.config_from_env({"RX_ROOTS": "/m/Anime"})
        st.load(self.conn, cfg2)
        for k, want in (("samples", 5), ("sample_len", 30), ("search_per_folder", 3), ("max_height", 1080),
                        ("max_staged_gb", 250.0), ("hw_decode", False), ("vmaf_gpu", "off"), ("codecs", ["h264", "hevc"])):
            self.assertEqual(cfg2[k], want, k)

    def test_new_settings_reject_bad_values(self):
        for bad in ({"codecs": []}, {"codecs": ["rm -rf"]}, {"codecs": 5}, {"samples": 0},
                    {"sample_len": 2}, {"max_height": 100}, {"vmaf_gpu": "yes"}, {"max_staged_gb": 0}):
            with self.assertRaises(ValueError, msg=str(bad)):
                st.save(self.conn, self.cfg, bad)

    def test_unticking_a_codec_skips_waiting_files_and_starts_a_scan(self):
        add(self.conn, "/m/Anime/Show/a.mkv", "Anime", "pending", codec="h264")
        add(self.conn, "/m/Anime/Show/b.mkv", "Anime", "pending", codec="mpeg4")
        add(self.conn, "/m/Anime/Show/c.mkv", "Anime", "pending", codec="hevc", height=2160)
        add(self.conn, "/m/Anime/Show/d.mkv", "Anime", "encoded", 1000, 400, codec="mpeg4")
        self.app.set_settings({"codecs": ["h264", "hevc"], "max_height": 1080})
        get = lambda p: rx_db.get_file(self.conn, p)
        self.assertEqual(get("/m/Anime/Show/a.mkv")["status"], "pending")
        self.assertEqual((get("/m/Anime/Show/b.mkv")["status"], get("/m/Anime/Show/b.mkv")["reason"]),
                         ("skipped", "codec not selected"))
        self.assertEqual(get("/m/Anime/Show/c.mkv")["reason"], "resolution too high")
        self.assertEqual(get("/m/Anime/Show/d.mkv")["status"], "encoded")  # done work is never touched
        self.assertEqual(self.worker.scans, 1)
        self.app.set_settings({"target_vmaf": 94})
        self.assertEqual(self.worker.scans, 1)  # only codec / height changes start a scan


if __name__ == "__main__":
    unittest.main()
