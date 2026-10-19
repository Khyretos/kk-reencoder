"""Tests for app/rx_worker.py with a fake ffmpeg (no real media, no GPU)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_db  # noqa: E402
import rx_stage  # noqa: E402
import rx_worker as w  # noqa: E402


def fake_probe(path):
    codec = "av1" if "av1" in os.path.basename(path) or "/.reencoder/" in path else "h264"
    return {"codec": codec, "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "ten_bit": False,
            "hdr": False, "fps": 23.976, "duration_s": 1400.0, "bitrate_kbps": 8000,
            "size": os.path.getsize(path), "audio": 1, "subs": 1, "video_index": 0}


class FakeFfmpeg:
    """Writes the output file of each command; sample encodes get 100 - 2.5*q KB, the VMAF
    command answers 100 - q/4 (so q <= 20 passes a target of 95)."""

    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, timeout=None, on_line=None):
        self.cmds.append(cmd)
        if "-lavfi" in cmd:
            q = int(self._q_of(cmd[cmd.index("-i") + 1]))
            return 0, "", f"[libvmaf] VMAF score: {100 - q / 4:.3f}\n"
        out = cmd[-1]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if "-q" in os.path.basename(out):
            size = int((100 - 2.5 * int(self._q_of(out))) * 1024)
        else:
            size = 100 * 1024
        with open(out, "wb") as f:
            f.write(b"\0" * size)
        if on_line:
            for line in ("out_time_us=700000000", "speed=4x", "progress=continue", "progress=end"):
                on_line(line)
        return 0, "", ""

    @staticmethod
    def _q_of(path):
        return os.path.basename(path).rsplit("-q", 1)[1].split(".")[0]


CFG = {
    "codecs": ["h264", "hevc"], "skip_hdr": True, "max_height": 2160,
    "min_kbps": {"sd": 900, "720": 1500, "1080": 2500, "2160": 8000},
    "encoder": "svt", "device": None, "target_vmaf": 95.0, "max_ratio": 0.8,
    "q_min": 10, "q_max": 40, "q_start": 24, "samples": 2, "sample_len": 20,
    "vmaf_threads": 2, "search_per_folder": 1,
}


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "Anime")
        for rel in ("Show/Season 1/e1.mkv", "Show/Season 1/e2.mkv", "Old/av1-e1.mkv"):
            p = os.path.join(self.lib, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"\0" * 4096)
        with open(os.path.join(self.lib, "Show", "notes.txt"), "w") as f:
            f.write("x")
        self.cfg = dict(CFG, work_dir=os.path.join(self.tmp, "work"), report_dir=os.path.join(self.tmp, "reports"),
                        roots=[os.path.join(self.tmp, "Anime")])
        self.c = rx_db.connect(":memory:")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_scan(self):
        n = w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        self.assertEqual(n, 3)
        e1 = rx_db.get_file(self.c, os.path.join(self.lib, "Show/Season 1/e1.mkv"))
        self.assertEqual((e1["library"], e1["folder"], e1["status"]), ("Anime", "Show", "pending"))
        old = rx_db.get_file(self.c, os.path.join(self.lib, "Old/av1-e1.mkv"))
        self.assertEqual((old["status"], old["reason"]), ("skipped", "already AV1"))

    def test_search_finds_highest_passing_q(self):
        run = FakeFfmpeg()
        res = w.search_file(os.path.join(self.lib, "Show/Season 1/e1.mkv"), fake_probe(os.path.join(self.lib, "Show/Season 1/e1.mkv")), self.cfg, run)
        self.assertEqual(res["q"], 20)
        self.assertAlmostEqual(res["vmaf"], 95.0, places=2)
        self.assertAlmostEqual(res["ratio"], 0.5, places=2)
        self.assertTrue(all(t["q"] != 20 or t["vmaf"] >= 95 for t in res["trials"]))
        # work files are cleaned up
        self.assertEqual(os.listdir(self.cfg["work_dir"]), [])

    def test_process_report_mode_predicts_and_writes_report(self):
        run = FakeFfmpeg()
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        f = rx_db.next_file(self.c)
        w.process(self.c, f, dict(self.cfg, mode="report"), run, probe=fake_probe)
        got = rx_db.get_file(self.c, f["path"])
        self.assertEqual(got["status"], "predicted")
        self.assertEqual(got["quality"], 20)
        self.assertEqual(got["new_bytes"], 2048)  # 4096 * ratio 0.5
        rep = os.path.join(self.cfg["report_dir"], "Anime", "Show", "encoding_report.json")
        data = json.load(open(rep))
        self.assertEqual(data["folder"], "Show")
        self.assertEqual(data["totals"]["predicted"], 1)
        # the media folder itself is not touched in report mode
        self.assertFalse(os.path.exists(os.path.join(self.lib, "Show", "encoding_report.json")))
        self.assertEqual(sorted(os.listdir(os.path.join(self.lib, "Show", "Season 1"))), ["e1.mkv", "e2.mkv"])

    def test_second_file_reuses_folder_quality(self):
        run = FakeFfmpeg()
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        w.process(self.c, rx_db.next_file(self.c), dict(self.cfg, mode="report"), run, probe=fake_probe)
        before = len(run.cmds)
        f2 = rx_db.next_file(self.c)
        w.process(self.c, f2, dict(self.cfg, mode="report"), run, probe=fake_probe)
        got = rx_db.get_file(self.c, f2["path"])
        self.assertEqual(got["status"], "predicted")
        self.assertEqual(got["quality"], 20)
        # one check sample (cut, reference, encode, vmaf) instead of a full search
        self.assertLessEqual(len(run.cmds) - before, 4)

    def test_process_stage_mode_keeps_original_and_makes_frames(self):
        run = FakeFfmpeg()
        orig = os.path.join(self.lib, "Show/Season 1/e1.mkv")
        with open(orig, "wb") as fh:
            fh.write(b"\0" * 400 * 1024)
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        f = rx_db.next_file(self.c)
        cfg = dict(self.cfg, mode="stage", frames_dir=os.path.join(self.tmp, "frames"), roots=[self.lib])
        w.process(self.c, f, cfg, run, probe=fake_probe)
        got = rx_db.get_file(self.c, f["path"])
        self.assertEqual(got["status"], "staged")
        self.assertEqual(got["codec_after"], "av1")
        self.assertEqual(got["size"], 400 * 1024)
        self.assertEqual(got["new_bytes"], 100 * 1024)
        # the original is untouched, the copy sits in the hidden folder
        self.assertEqual(os.path.getsize(orig), 400 * 1024)
        self.assertEqual(os.path.getsize(rx_stage.staged_path(orig)), 100 * 1024)
        # comparison frames: pairs <i>-a.jpg (original) and <i>-b.jpg (re-encode), at most 5
        fd = rx_stage.frames_dir(cfg["frames_dir"], orig)
        names = sorted(n for n in os.listdir(fd) if n.endswith(".jpg"))
        self.assertTrue(2 <= len(names) <= 10)
        self.assertEqual(len([n for n in names if n.endswith("-a.jpg")]), len([n for n in names if n.endswith("-b.jpg")]))
        self.assertTrue(os.path.exists(os.path.join(cfg["report_dir"], "Anime", "Show", "encoding_report.json")))

    def test_manual_mode_stages_only_marked_series(self):
        run = FakeFfmpeg()
        orig = os.path.join(self.lib, "Show/Season 1/e1.mkv")
        with open(orig, "wb") as fh:
            fh.write(b"\0" * 400 * 1024)
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        cfg = dict(self.cfg, mode="manual", frames_dir=os.path.join(self.tmp, "frames"), roots=[self.lib])
        f = rx_db.next_file(self.c)
        w.process(self.c, f, cfg, run, probe=fake_probe)
        self.assertEqual(rx_db.get_file(self.c, orig)["status"], "predicted")  # not marked: estimate only
        self.assertFalse(os.path.exists(rx_stage.staged_path(orig)))
        rx_db.set_folder(self.c, "Anime", "Show", encode=True)
        w.process(self.c, rx_db.next_file(self.c), cfg, run, probe=fake_probe)
        self.assertEqual(rx_db.get_file(self.c, orig)["status"], "staged")
        self.assertTrue(os.path.exists(rx_stage.staged_path(orig)))

    def test_stage_refuses_when_not_smaller(self):
        run = FakeFfmpeg()
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)
        f = rx_db.next_file(self.c)
        # fake full encode is 100 KB, bigger than the 4 KB "original": the copy is thrown away
        cfg = dict(self.cfg, mode="stage", frames_dir=os.path.join(self.tmp, "frames"), roots=[self.lib])
        w.process(self.c, f, cfg, run, probe=fake_probe)
        self.assertEqual(os.path.getsize(f["path"]), 4096)
        self.assertFalse(os.path.exists(rx_stage.staged_path(f["path"])))
        got = rx_db.get_file(self.c, f["path"])
        self.assertEqual((got["status"], got["reason"]), ("skipped", "encode not smaller"))

if __name__ == "__main__":
    unittest.main()
