"""Tests for skip reasons (with their numbers), re-checking skipped files, polite ffmpeg and the cached summary."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_model as m  # noqa: E402
import rx_pool  # noqa: E402
import rx_scan  # noqa: E402
import rx_search  # noqa: E402
import rx_settings as st  # noqa: E402


def add(conn, path, status, reason=None, folder="Show"):
    rx_db.upsert_file(conn, {"path": path, "library": "Anime", "folder": folder, "size": 1000, "mtime": 1.0,
                             "codec": "hevc", "height": 2160, "bitrate_kbps": 20000, "duration_s": 100.0,
                             "status": status, "reason": reason})


class SearchReason(unittest.TestCase):
    def test_too_big_at_target_names_the_size(self):
        trials = [{"q": 22, "vmaf": 96.0, "ratio": 0.97}, {"q": 31, "vmaf": 94.0, "ratio": 0.7},
                  {"q": 26, "vmaf": 95.2, "ratio": 0.93}]
        r = m.search_skip_reason(trials, 95.0, 0.9)
        self.assertEqual(r, "at VMAF 95 the AV1 copy would be 93% of the original size; the size limit is 90%")
        self.assertTrue(m.is_quality_reason(r))

    def test_never_reached_target(self):
        r = m.search_skip_reason([{"q": 22, "vmaf": 93.1, "ratio": 0.5}, {"q": 16, "vmaf": 94.4, "ratio": 0.8}],
                                 95.0, 0.9)
        self.assertEqual(r, "never reached VMAF 95: best was 94.4 at the highest quality tried (q 16)")

    def test_old_and_setting_reasons(self):
        self.assertTrue(m.is_quality_reason("no smaller encode at target quality"))
        self.assertTrue(m.is_quality_reason("encode not smaller"))
        self.assertFalse(m.is_quality_reason("HDR"))
        self.assertFalse(m.is_quality_reason(None))


class Requeue(unittest.TestCase):
    def setUp(self):
        self.conn = rx_db.connect(":memory:")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": "/m/A", "RX_MODE": "manual"})
        add(self.conn, "/m/A/Show/1.mkv", "skipped", "no smaller encode at target quality")
        add(self.conn, "/m/A/Show/2.mkv", "skipped", "encode not smaller")
        add(self.conn, "/m/A/Show/3.mkv", "skipped", "HDR")
        add(self.conn, "/m/A/Show/4.mkv", "skipped", "already AV1")

    def status(self, n):
        return rx_db.get_file(self.conn, f"/m/A/Show/{n}.mkv")["status"]

    def test_size_limit_change_measures_quality_skips_again(self):
        st.save(self.conn, self.cfg, {"max_ratio": 0.97})
        self.assertEqual([self.status(n) for n in (1, 2, 3, 4)], ["pending", "pending", "skipped", "skipped"])

    def test_same_value_or_other_setting_changes_nothing(self):
        st.save(self.conn, self.cfg, {"max_ratio": self.cfg["max_ratio"], "parallel": 3})
        self.assertEqual(self.status(1), "skipped")

    def test_size_limit_up_to_99_percent(self):
        self.assertEqual(st.save(self.conn, self.cfg, {"max_ratio": 0.99})["max_ratio"], 0.99)
        with self.assertRaises(ValueError):
            st.save(self.conn, self.cfg, {"max_ratio": 1.0})


class ScanLooksAgain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "Series")
        self.path = os.path.join(self.root, "Monarch", "Season 1", "e1.mkv")
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as f:
            f.write(b"\0" * 100)
        self.conn = rx_db.connect(":memory:")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": self.root})
        self.probes = 0

    def probe(self, path):
        self.probes += 1
        return {"codec": "hevc", "width": 3840, "height": 2160, "pix_fmt": "yuv420p10le", "ten_bit": True,
                "hdr": True, "fps": 24.0, "duration_s": 3000.0, "bitrate_kbps": 29000, "size": 100,
                "audio": 1, "subs": 1, "video_index": 0}

    def test_hdr_skip_is_lifted_when_the_setting_changes(self):
        rx_scan.scan(self.conn, [self.root], self.cfg, probe=self.probe)
        self.assertEqual(rx_db.get_file(self.conn, self.path)["reason"], "HDR")
        rx_scan.scan(self.conn, [self.root], self.cfg, probe=self.probe)
        self.assertEqual(rx_db.get_file(self.conn, self.path)["status"], "skipped")
        self.cfg["skip_hdr"] = False
        rx_scan.scan(self.conn, [self.root], self.cfg, probe=self.probe)
        row = rx_db.get_file(self.conn, self.path)
        self.assertEqual((row["status"], row["reason"]), ("pending", None))
        n = self.probes
        rx_scan.scan(self.conn, [self.root], self.cfg, probe=self.probe)
        self.assertEqual(self.probes, n)  # pending and unchanged: not probed again


class Polite(unittest.TestCase):
    def test_ffmpeg_gets_low_priority_and_thread_caps(self):
        c = rx_search.polite_cmd(["ffmpeg", "-nostdin", "-i", "a.mkv", "-i", "b.mkv", "-lavfi", "x", "-f", "null", "-"], 2)
        self.assertEqual(c[:6], ["nice", "-n", "19", "ionice", "-c", "3"])
        self.assertEqual(c[6:9], ["ffmpeg", "-filter_threads", "2"])
        self.assertEqual(c.count("-threads"), 3)  # each input and the output
        self.assertEqual(c[c.index("a.mkv") - 3:c.index("a.mkv")], ["-threads", "2", "-i"])
        self.assertEqual(c[-3:], ["-threads", "2", "-"])

    def test_other_commands_only_get_low_priority(self):
        self.assertEqual(rx_search.polite_cmd(["ffprobe", "x"]), ["nice", "-n", "19", "ionice", "-c", "3", "ffprobe", "x"])

    def test_errors_say_what_happened(self):
        self.assertIn("out of memory", rx_search.describe_rc(-9))
        err = "libva info: VA-API version 1.22.0\nlibva info: Trying to open /x\n[hevc] Invalid data found\n"
        self.assertEqual(rx_search.useful_tail(err), "[hevc] Invalid data found")

    def test_run_ok_message(self):
        with self.assertRaises(RuntimeError) as e:
            rx_search._run_ok(lambda cmd: (-9, "", "libva info: x\n"), ["ffmpeg", "-i", "a"])
        self.assertEqual(str(e.exception), "ffmpeg killed (-9), most likely the container ran out of memory")


class CachedSummary(unittest.TestCase):
    def test_folders_follow_every_write(self):
        conn = rx_db.connect(":memory:")
        add(conn, "/m/A/Show/1.mkv", "pending")
        self.assertEqual(rx_db.folders(conn)[0]["pending"], 1)
        rx_db.set_result(conn, "/m/A/Show/1.mkv", status="skipped", reason="HDR")
        f = rx_db.folders(conn)[0]
        self.assertEqual((f["pending"], f["skipped"]), (0, 1))
        f["skipped"] = 99  # callers get their own copy
        self.assertEqual(rx_db.folders(conn)[0]["skipped"], 1)


class ScanFolder(unittest.TestCase):
    def test_worker_scans_one_folder(self):
        seen = []
        wk = rx_pool.Worker(rx_db.connect(":memory:"), {"roots": ["/m/A"]},
                            scan=lambda conn, roots, cfg, probe=None, only=None: seen.append(only))
        wk.scan_folder("A", "Show")
        for _ in range(100):
            if seen:
                break
            import time
            time.sleep(0.01)
        self.assertEqual(seen, [("A", "Show")])


class ProgressNA(unittest.TestCase):
    def test_na_values_at_the_start_do_not_crash(self):
        p = m.parse_progress(["frame=N/A", "fps=N/A", "out_time_us=N/A", "speed=N/A", "progress=continue"], 100.0)
        self.assertEqual((p["frame"], p["fps"], p["percent"], p["eta_s"]), (0, 0.0, 0.0, None))
        p = m.parse_progress(["frame=240", "fps=48.0", "out_time_us=10000000", "speed=2x", "progress=continue"], 100.0)
        self.assertEqual((p["frame"], round(p["percent"]), round(p["eta_s"])), (240, 10, 45))


if __name__ == "__main__":
    unittest.main()
