"""Tests for app/rx_model.py (pure functions, no ffmpeg, no disk)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_model as m  # noqa: E402

CFG = {
    "codecs": ["h264", "hevc", "mpeg4", "vc1", "mpeg2video"],
    "skip_hdr": True,
    "max_height": 2160,
    "min_kbps": {"sd": 900, "720": 1500, "1080": 2500, "2160": 8000},
}


def info(**kw):
    base = {"codec": "h264", "height": 1080, "bitrate_kbps": 8000, "hdr": False}
    base.update(kw)
    return base


class FolderKey(unittest.TestCase):
    def test_series_season_goes_up(self):
        self.assertEqual(
            m.folder_key("/media/Anime", "/media/Anime/Show (2024)/Season 1/Show - S01E01.mkv"),
            "Show (2024)",
        )

    def test_specials_goes_up(self):
        self.assertEqual(m.folder_key("/media/Series", "/media/Series/Show/Specials/x.mkv"), "Show")

    def test_movie_in_category(self):
        self.assertEqual(
            m.folder_key("/media/Movies", "/media/Movies/Regular/Spirited Away (2001)/Spirited Away (2001).mp4"),
            "Regular/Spirited Away (2001)",
        )

    def test_file_in_root(self):
        self.assertEqual(m.folder_key("/media/Movies", "/media/Movies/Film.mkv"), "Film")


class SkipReason(unittest.TestCase):
    def test_candidate(self):
        self.assertIsNone(m.skip_reason(info(), CFG))

    def test_av1(self):
        self.assertEqual(m.skip_reason(info(codec="av1"), CFG), "already AV1")

    def test_hdr(self):
        self.assertEqual(m.skip_reason(info(codec="hevc", hdr=True, height=2160, bitrate_kbps=20000), CFG), "HDR")

    def test_low_bitrate(self):
        self.assertEqual(m.skip_reason(info(bitrate_kbps=2000), CFG), "bitrate already low")
        self.assertEqual(m.skip_reason(info(height=480, bitrate_kbps=800), CFG), "bitrate already low")
        self.assertIsNone(m.skip_reason(info(height=480, bitrate_kbps=1200), CFG))

    def test_codec_not_selected(self):
        self.assertEqual(m.skip_reason(info(codec="vp9"), CFG), "codec not selected")

    def test_too_big(self):
        self.assertEqual(m.skip_reason(info(height=4320, bitrate_kbps=90000), CFG), "resolution too high")

    def test_unknown_bitrate_is_candidate(self):
        self.assertIsNone(m.skip_reason(info(bitrate_kbps=None), CFG))


class OrderFolders(unittest.TestCase):
    def test_priority_then_alphabet_paused_out(self):
        folders = [
            {"name": "beta", "priority": 0, "paused": False, "pending": 3},
            {"name": "Alpha", "priority": 0, "paused": False, "pending": 1},
            {"name": "zulu", "priority": 5, "paused": False, "pending": 2},
            {"name": "aaa", "priority": 9, "paused": True, "pending": 2},
            {"name": "done", "priority": 9, "paused": False, "pending": 0},
        ]
        self.assertEqual([f["name"] for f in m.order_folders(folders)], ["zulu", "Alpha", "beta"])


class Search(unittest.TestCase):
    def test_first_try_is_start(self):
        self.assertEqual(m.next_quality([], 95, 10, 40, start=24), 24)

    def test_goes_down_when_too_low(self):
        q = m.next_quality([{"q": 24, "vmaf": 92.0}], 95, 10, 40, start=24)
        self.assertTrue(10 <= q < 24)

    def test_goes_up_when_good(self):
        q = m.next_quality([{"q": 24, "vmaf": 97.0}], 95, 10, 40, start=24)
        self.assertTrue(24 < q <= 40)

    def test_bracket(self):
        q = m.next_quality([{"q": 24, "vmaf": 97.0}, {"q": 32, "vmaf": 93.0}], 95, 10, 40, start=24)
        self.assertTrue(24 < q < 32)

    def test_done_when_adjacent(self):
        self.assertIsNone(m.next_quality([{"q": 24, "vmaf": 96.0}, {"q": 25, "vmaf": 94.0}], 95, 10, 40, start=24))

    def test_done_at_bounds(self):
        self.assertIsNone(m.next_quality([{"q": 10, "vmaf": 90.0}], 95, 10, 40, start=10))
        self.assertIsNone(m.next_quality([{"q": 40, "vmaf": 99.0}], 95, 10, 40, start=40))

    def test_never_repeats(self):
        trials = [{"q": 24, "vmaf": 97.0}, {"q": 26, "vmaf": 93.0}]
        q = m.next_quality(trials, 95, 10, 40, start=24)
        self.assertIn(q, (25, None))


class PickResult(unittest.TestCase):
    def test_highest_q_that_passes(self):
        trials = [
            {"q": 20, "vmaf": 97.5, "ratio": 0.40},
            {"q": 24, "vmaf": 95.4, "ratio": 0.30},
            {"q": 28, "vmaf": 93.0, "ratio": 0.22},
        ]
        self.assertEqual(m.pick_result(trials, 95, 0.8)["q"], 24)

    def test_none_when_not_smaller_enough(self):
        self.assertIsNone(m.pick_result([{"q": 20, "vmaf": 96.0, "ratio": 0.9}], 95, 0.8))

    def test_none_when_quality_never_reached(self):
        self.assertIsNone(m.pick_result([{"q": 10, "vmaf": 90.0, "ratio": 0.5}], 95, 0.8))


class Report(unittest.TestCase):
    def files(self):
        return [
            {"name": "b.mkv", "status": "encoded", "codec_before": "h264", "codec_after": "av1",
             "original_bytes": 1000, "new_bytes": 400, "vmaf": 95.6, "quality": 24, "reason": None,
             "encoded_at": "2026-10-18T10:00:00Z"},
            {"name": "a.mkv", "status": "skipped", "codec_before": "av1", "codec_after": None,
             "original_bytes": 500, "new_bytes": None, "vmaf": None, "quality": None,
             "reason": "already AV1", "encoded_at": None},
            {"name": "c.mkv", "status": "predicted", "codec_before": "h264", "codec_after": None,
             "original_bytes": 2000, "new_bytes": 700, "vmaf": 95.1, "quality": 26, "reason": None,
             "encoded_at": None},
        ]

    def test_totals(self):
        r = m.folder_report("Anime", "Show", self.files(), "2026-10-18T12:00:00Z")
        self.assertEqual(r["folder"], "Show")
        self.assertEqual(r["library"], "Anime")
        self.assertEqual(r["generated"], "2026-10-18T12:00:00Z")
        t = r["totals"]
        self.assertEqual(t["files"], 3)
        self.assertEqual(t["encoded"], 1)
        self.assertEqual(t["skipped"], 1)
        self.assertEqual(t["predicted"], 1)
        self.assertEqual(t["original_bytes"], 3500)
        # current size: encoded files count at their new size, the rest at their original size
        self.assertEqual(t["current_bytes"], 400 + 500 + 2000)
        self.assertEqual(t["saved_bytes"], 600)
        self.assertAlmostEqual(t["saved_pct"], 600 / 3500 * 100, places=1)
        # projected: also counts predicted files at their predicted size
        self.assertEqual(t["projected_bytes"], 400 + 500 + 700)

    def test_files_sorted_and_saved_pct(self):
        r = m.folder_report("Anime", "Show", self.files(), "x")
        self.assertEqual([f["name"] for f in r["files"]], ["a.mkv", "b.mkv", "c.mkv"])
        self.assertAlmostEqual(r["files"][1]["saved_pct"], 60.0, places=1)
        self.assertIsNone(r["files"][0]["saved_pct"])

    def test_staged_and_confirmed(self):
        files = [
            {"name": "s.mkv", "status": "staged", "codec_before": "h264", "codec_after": "av1",
             "original_bytes": 1000, "new_bytes": 300, "vmaf": 95.5, "quality": 22, "reason": None, "encoded_at": "x"},
            {"name": "c.mkv", "status": "confirmed", "codec_before": "h264", "codec_after": "av1",
             "original_bytes": 1000, "new_bytes": 200, "vmaf": 95.5, "quality": 22, "reason": None, "encoded_at": "x"},
        ]
        t = m.folder_report("Anime", "Show", files, "x")["totals"]
        self.assertEqual((t["staged"], t["confirmed"]), (1, 1))
        # in place: the staged file is still the original, the confirmed one is the re-encode
        self.assertEqual(t["current_bytes"], 1000 + 200)
        # kept on the side until finalize: the staged copy and the confirmed file's original
        self.assertEqual(t["extra_bytes"], 300 + 1000)
        # net saving only counts what is really freed
        self.assertEqual(t["saved_bytes"], 2000 - 1200 - 1300)
        self.assertEqual(t["projected_bytes"], 500)

    def test_empty(self):
        t = m.folder_report("Anime", "Empty", [], "x")["totals"]
        self.assertEqual(t["files"], 0)
        self.assertEqual(t["saved_pct"], 0.0)


class Progress(unittest.TestCase):
    def test_parse(self):
        p = m.parse_progress(["frame=240", "fps=96.5", "out_time_us=10000000", "speed=4.01x", "progress=continue"], 40.0)
        self.assertEqual(p["frame"], 240)
        self.assertAlmostEqual(p["fps"], 96.5)
        self.assertAlmostEqual(p["percent"], 25.0)
        self.assertAlmostEqual(p["eta_s"], 30.0 / 4.01, places=1)
        self.assertFalse(p["done"])

    def test_end(self):
        p = m.parse_progress(["out_time_us=40000000", "speed=N/A", "progress=end"], 40.0)
        self.assertTrue(p["done"])
        self.assertEqual(p["percent"], 100.0)
        self.assertIsNone(p["eta_s"])


if __name__ == "__main__":
    unittest.main()
