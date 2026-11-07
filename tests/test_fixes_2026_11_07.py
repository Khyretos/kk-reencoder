"""Retry runs next, a non-UTF-8 byte on stderr no longer kills ffmpeg, long names fit, rows that say
staged/confirmed are brought in line with the disk (Finalize "expected 'confirmed', got 'none'")."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import rx_app  # noqa: E402
import rx_db  # noqa: E402
import rx_search  # noqa: E402
import rx_stage  # noqa: E402
import rx_worker as w  # noqa: E402
from test_worker import CFG, FakeFfmpeg, fake_probe  # noqa: E402


def rec(path, folder="Show", size=1000, status="pending"):
    return {"path": path, "library": "Anime", "folder": folder, "size": size, "mtime": 1.0, "status": status}


class Retry(unittest.TestCase):
    def setUp(self):
        self.c = rx_db.connect(":memory:")
        for i in range(3):
            rx_db.upsert_file(self.c, rec(f"/m/Anime/Aaa/e{i}.mkv", "Aaa"))
        rx_db.upsert_file(self.c, rec("/m/Anime/Zzz/e1.mkv", "Zzz", status="failed"))
        rx_db.set_result(self.c, "/m/Anime/Zzz/e1.mkv", status="failed", error="encode exit code 1")
        self.app = rx_app.App(self.c, {"mode": "stage", "report_dir": tempfile.mkdtemp()}, sampler=object())

    def test_retried_file_goes_first(self):
        self.assertEqual(rx_db.next_file(self.c)["folder"], "Aaa")
        row = self.app.file_action("/m/Anime/Zzz/e1.mkv", "retry")
        self.assertEqual((row["status"], row["error"], row["reason"]), ("pending", None, "retry"))
        self.assertEqual(rx_db.next_file(self.c)["path"], "/m/Anime/Zzz/e1.mkv")

    def test_retry_respects_a_paused_series(self):
        self.app.file_action("/m/Anime/Zzz/e1.mkv", "retry")
        rx_db.set_folder(self.c, "Anime", "Zzz", paused=True)
        self.assertEqual(rx_db.next_file(self.c)["folder"], "Aaa")

    def test_folder_retry(self):
        r = self.app.folder_action("Anime", "Zzz", "retry")
        self.assertEqual(r, {"done": 1, "failed": []})
        self.assertEqual(rx_db.next_file(self.c)["path"], "/m/Anime/Zzz/e1.mkv")


class StderrBytes(unittest.TestCase):
    def test_non_utf8_stderr_does_not_kill_the_command(self):
        code = ("import os\nfor i in range(20000): os.write(2, b'\\x81\\xfe tag %d\\n' % i)\nprint('done')")
        rc, out, err = rx_search.default_run([sys.executable, "-c", code])
        self.assertEqual((rc, out), (0, "done"))
        self.assertIn("tag 19999", err)


class LongNames(unittest.TestCase):
    def test_aside_names_fit_and_stay_unique(self):
        stem = "I'm Living with an Otaku NEET Kunoichi!! - S01E01-E04 - " + "Somehow a Kunoichi Showed Up + " * 8
        a, b = f"/m/S/{stem}A.mkv", f"/m/S/{stem}B.mkv"
        for p in (a, b):
            for q in (rx_stage.staged_path(p), rx_stage.kept_path(p)):
                name = os.path.basename(q)
                self.assertLessEqual(len(name.encode()) + len(".part"), 255)
                self.assertTrue(name.endswith(".mkv"))
        self.assertNotEqual(rx_stage.staged_path(a), rx_stage.staged_path(b))
        # short names are unchanged
        self.assertEqual(os.path.basename(rx_stage.staged_path("/m/S/e1.mkv")), "[reencoded] e1.mkv")

    def test_long_name_confirm_finalize_on_disk(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "x" * 240 + ".mkv")
            open(p, "wb").write(b"o" * 100)
            os.makedirs(os.path.dirname(rx_stage.staged_path(p)))
            open(rx_stage.staged_path(p), "wb").write(b"n" * 10)
            self.assertEqual(rx_stage.state(p), "staged")
            rx_stage.confirm(p)
            self.assertEqual(rx_stage.state(p), "confirmed")
            rx_stage.finalize(p)
            self.assertEqual(rx_stage.state(p), "final")
            self.assertEqual(os.path.getsize(p), 10)
        finally:
            shutil.rmtree(d)


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = os.path.join(self.tmp, "Anime", "Show", "e1.mkv")
        os.makedirs(os.path.dirname(self.p))
        self.c = rx_db.connect(":memory:")
        self.app = rx_app.App(self.c, {"mode": "stage", "report_dir": os.path.join(self.tmp, "rep"),
                                         "roots": [os.path.join(self.tmp, "Anime")]}, sampler=object())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def row(self, status, on_disk, size=1000, new=300):
        open(self.p, "wb").write(b"x" * on_disk)
        rx_db.upsert_file(self.c, rec(self.p, size=size))
        rx_db.set_result(self.c, self.p, status=status, new_bytes=new, quality=20, codec_after="av1")

    def test_confirmed_whose_original_is_gone_finalizes(self):
        # the AV1 copy is in place and the kept original is already gone: Finalize just records it
        self.row("confirmed", on_disk=300)
        os.makedirs(os.path.join(self.tmp, "Anime", "Show", ".reencoder"))  # an empty leftover folder
        r = self.app.file_action(self.p, "finalize")
        self.assertEqual(r["status"], "encoded")
        self.assertEqual(rx_stage.state(self.p), "final")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Anime", "Show", ".reencoder")))

    def test_folder_finalize_counts_it_done(self):
        self.row("confirmed", on_disk=300)
        self.assertEqual(self.app.folder_action("Anime", "Show", "finalize"), {"done": 1, "failed": []})

    def test_confirmed_with_the_original_in_place_goes_back_to_the_queue(self):
        self.row("confirmed", on_disk=1000)
        self.assertEqual(self.app.heal_all(), 1)
        r = rx_db.get_file(self.c, self.p)
        self.assertEqual((r["status"], r["new_bytes"], r["quality"]), ("pending", None, None))
        self.assertEqual(self.app.folder_action("Anime", "Show", "finalize"), {"done": 0, "failed": []})

    def test_staged_whose_copy_is_gone(self):
        self.row("staged", on_disk=1000)
        with self.assertRaises(rx_stage.StageError):
            self.app.file_action(self.p, "confirm")
        self.assertEqual(rx_db.get_file(self.c, self.p)["status"], "pending")

    def test_consistent_rows_are_left_alone(self):
        self.row("staged", on_disk=1000)
        os.makedirs(os.path.dirname(rx_stage.staged_path(self.p)))
        open(rx_stage.staged_path(self.p), "wb").write(b"n" * 300)
        self.assertEqual(self.app.heal_all(), 0)
        self.assertEqual(self.app.file_action(self.p, "confirm")["status"], "confirmed")
        self.assertEqual(self.app.heal_all(), 0)
        self.assertEqual(self.app.file_action(self.p, "finalize")["status"], "encoded")

    def test_unmounted_library_is_not_judged(self):
        self.row("confirmed", on_disk=300)
        shutil.rmtree(os.path.join(self.tmp, "Anime", "Show"))
        self.assertEqual(self.app.heal_all(), 0)
        self.assertEqual(rx_db.get_file(self.c, self.p)["status"], "confirmed")


class WorkerFallbacks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "Anime")
        for i in (1, 2, 3):
            p = os.path.join(self.lib, f"Show/Season 1/e{i}.mkv")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(b"\0" * 400 * 1024)
        self.cfg = dict(CFG, mode="stage", work_dir=os.path.join(self.tmp, "work"),
                        report_dir=os.path.join(self.tmp, "reports"), frames_dir=os.path.join(self.tmp, "frames"),
                        roots=[self.lib], hw_decode=True, encoder="qsv", device="/dev/dri/renderD128")
        self.c = rx_db.connect(":memory:")
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_broken_quick_check_falls_back_to_a_full_search(self):
        run = FakeFfmpeg()
        w.process(self.c, rx_db.next_file(self.c), self.cfg, run, probe=fake_probe)
        f = rx_db.next_file(self.c)

        def broken(cmd, timeout=None, on_line=None):
            if "-q" in os.path.basename(cmd[-1]) and not getattr(broken, "done", False):
                broken.done = True  # the first sample encode (the quick check) fails like the EBML errors
                return 187, "", "Error opening input: End of file"
            return run(cmd, timeout, on_line)
        w.process(self.c, f, self.cfg, broken, probe=fake_probe)
        self.assertEqual(rx_db.get_file(self.c, f["path"])["status"], "staged")

    def test_short_gpu_copy_is_encoded_again_with_the_cpu_decode(self):
        run = FakeFfmpeg()
        encodes = []

        def probe(path):
            info = fake_probe(path)
            if ".part" in path and len(encodes) == 1:
                info["duration_s"] = 1390.0  # the GPU decode dropped 10 s
            return info

        def counting(cmd, timeout=None, on_line=None):
            if any(".part" in a for a in cmd) and "-progress" in cmd:
                encodes.append("-hwaccel" in cmd)
            return run(cmd, timeout, on_line)
        f = rx_db.next_file(self.c)
        w.process(self.c, f, self.cfg, counting, probe=probe)
        self.assertEqual(encodes, [True, False])
        self.assertEqual(rx_db.get_file(self.c, f["path"])["status"], "staged")


if __name__ == "__main__":
    unittest.main()
