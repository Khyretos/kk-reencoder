"""Failed files that kept failing after Retry (7 Nov): wrong container durations, cut-off downloads,
an unknown subtitle codec, a .ts that is Matroska inside. Uses the real ffmpeg/ffprobe where it can."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import rx_db  # noqa: E402
import rx_media  # noqa: E402
import rx_search  # noqa: E402
import rx_worker as w  # noqa: E402
from test_worker import CFG, FakeFfmpeg, fake_probe  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_json(fmt_dur, video_tags=None, subs=()):
    streams = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
                "avg_frame_rate": "24000/1001", "tags": video_tags or {}},
               {"codec_type": "audio", "codec_name": "aac", "tags": {"DURATION": "00:48:39.904000000"}}]
    streams += [{"codec_type": "subtitle", "codec_name": c} if c else {"codec_type": "subtitle"} for c in subs]
    return {"streams": streams, "format": {"duration": str(fmt_dur), "bit_rate": "3000000"}}


class Durations(unittest.TestCase):
    def test_container_saying_an_hour_for_a_24_minute_episode(self):
        info = rx_media.parse_probe(probe_json(3600.8, {"DURATION": "00:24:09.992000000"}), 10**9)
        self.assertAlmostEqual(info["duration_s"], 1449.992, places=2)
        # samples now land inside the episode (they were at 900/1800/2700 s of a 1450 s video)
        self.assertTrue(all(p + 20 <= info["duration_s"] for p in rx_media.sample_points(info["duration_s"], 3, 20)))

    def test_video_shorter_than_audio_goes_by_the_video(self):
        info = rx_media.parse_probe(probe_json(2919.904, {"DURATION": "00:09:33.042000000"}), 10**9)
        self.assertAlmostEqual(info["duration_s"], 573.042, places=2)

    def test_normal_file_keeps_the_container_duration(self):
        info = rx_media.parse_probe(probe_json(1420.016, {"DURATION": "00:23:39.962000000"}), 10**9)
        self.assertEqual(info["duration_s"], 1420.016)
        self.assertEqual(rx_media.parse_probe(probe_json(1420.0), 10**9)["duration_s"], 1420.0)


class Commands(unittest.TestCase):
    def test_unknown_subtitle_codec_is_left_out(self):
        info = rx_media.parse_probe(probe_json(1413, subs=("ass", None, "subrip")), 10**9)
        self.assertEqual(info["bad_subs"], [1])
        cmd = rx_media.encode_cmd("/m/a.mkv", "/m/b.mkv", "qsv", 24, "/dev/dri/renderD128", 0, bad_subs=info["bad_subs"])
        i = cmd.index("-0:s:1")
        self.assertEqual(cmd[i - 1], "-map")
        self.assertGreater(i, cmd.index("0:s?"))  # a negative map only removes what an earlier map added

    def test_ts_copy_is_matroska(self):
        cmd = rx_media.encode_cmd("/m/a.ts", "/m/.reencoder/a.part.ts", "qsv", 24, "/dev/dri/renderD128", 0)
        self.assertEqual(cmd[cmd.index("-f") + 1], "matroska")
        self.assertNotIn("-f", rx_media.encode_cmd("/m/a.mkv", "/m/b.mkv", "qsv", 24, "/dev/dri/renderD128", 0))


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class RealFiles(unittest.TestCase):
    """A 60 s file cut off at about half its size: its header still says 60 s."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)  # also when setUpClass fails half-way
        cls.full = os.path.join(cls.tmp, "full.mkv")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-t", "60",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-y", cls.full], check=True)
        cls.cut = os.path.join(cls.tmp, "cut.mkv")
        data = open(cls.full, "rb").read()
        with open(cls.cut, "wb") as f:
            f.write(data[: len(data) // 2])

    def test_video_packets_after(self):
        self.assertTrue(rx_media.video_packets_after(self.full, 50))
        self.assertTrue(rx_media.video_packets_after(self.cut, 10))
        self.assertFalse(rx_media.video_packets_after(self.cut, 50))
        self.assertTrue(rx_media.video_packets_after(os.path.join(self.tmp, "missing.mkv"), 50))  # unknown: True

    def test_sample_past_the_real_end_moves_earlier(self):
        info = rx_media.probe(self.cut)
        self.assertGreater(info["duration_s"], 55)  # the header lies, as in the failed files
        cfg = dict(CFG, work_dir=os.path.join(self.tmp, "work"), sample_len=5)
        os.makedirs(cfg["work_dir"], exist_ok=True)
        made = []
        out = rx_search._cut_samples(self.cut, info, [50], cfg, rx_search.default_run, made)
        start, sample, ref = out[0]
        self.assertLess(start, 30)
        self.assertGreater(os.path.getsize(ref), rx_search.EMPTY_REF_BYTES)
        rx_search._cleanup(made)

    def test_no_readable_video_anywhere_says_so(self):
        cfg = dict(CFG, work_dir=os.path.join(self.tmp, "work2"), sample_len=5)
        os.makedirs(cfg["work_dir"], exist_ok=True)
        info = rx_media.probe(self.full)
        with mock.patch.object(rx_search, "getsize", return_value=500):
            with self.assertRaisesRegex(RuntimeError, "no readable video"):
                rx_search._cut_samples(self.full, info, [40], cfg, rx_search.default_run, [])


class CutOffCopy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "Anime")
        p = os.path.join(self.lib, "Show/Season 1/e1.mkv")
        os.makedirs(os.path.dirname(p))
        open(p, "wb").write(b"\0" * 400 * 1024)
        self.cfg = dict(CFG, mode="stage", work_dir=os.path.join(self.tmp, "work"),
                        report_dir=os.path.join(self.tmp, "reports"), frames_dir=os.path.join(self.tmp, "frames"),
                        roots=[self.lib])
        self.c = rx_db.connect(":memory:")
        w.scan(self.c, [self.lib], self.cfg, probe=fake_probe)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @staticmethod
    def short_probe(path):
        info = fake_probe(path)
        if ".part" in path or "/.reencoder/" in path:
            info["duration_s"] = 1300.0  # 100 s short
        return info

    def test_copy_of_a_cut_off_original_is_kept_with_a_note(self):
        f = rx_db.next_file(self.c)
        with mock.patch.object(rx_media, "video_packets_after", return_value=False):
            w.process(self.c, f, self.cfg, FakeFfmpeg(), probe=self.short_probe)
        r = rx_db.get_file(self.c, f["path"])
        self.assertEqual(r["status"], "staged")
        self.assertIn("cut off", r["reason"])

    def test_really_short_copy_still_fails(self):
        f = rx_db.next_file(self.c)
        with mock.patch.object(rx_media, "video_packets_after", return_value=True):
            w.process(self.c, f, self.cfg, FakeFfmpeg(), probe=self.short_probe)
        r = rx_db.get_file(self.c, f["path"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("bad duration", r["error"])


if __name__ == "__main__":
    unittest.main()
