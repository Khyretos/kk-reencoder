"""Tests for app/rx_stage.py: staged copies, confirm / undo / finalize, comparison frames."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_stage as st  # noqa: E402


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"o" * size)


class Paths(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(st.staged_path("/m/Show/Season 1/e1.mkv"), "/m/Show/Season 1/.reencoder/[reencoded] e1.mkv")
        self.assertEqual(st.kept_path("/m/Show/Season 1/e1.mkv"), "/m/Show/Season 1/.reencoder/[original] e1.mkv")
        self.assertEqual(st.frames_dir("/data/frames", "/m/Show/e1.mkv"), st.frames_dir("/data/frames", "/m/Show/e1.mkv"))
        self.assertNotEqual(st.frames_dir("/data/frames", "/m/a.mkv"), st.frames_dir("/data/frames", "/m/b.mkv"))


class Swap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig = os.path.join(self.tmp, "Show", "e1.mkv")
        write(self.orig, 1000)
        write(st.staged_path(self.orig), 300)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_confirm_undo_confirm_finalize(self):
        self.assertEqual(st.state(self.orig), "staged")
        st.confirm(self.orig)
        self.assertEqual(os.path.getsize(self.orig), 300)
        self.assertEqual(os.path.getsize(st.kept_path(self.orig)), 1000)
        self.assertFalse(os.path.exists(st.staged_path(self.orig)))
        self.assertEqual(st.state(self.orig), "confirmed")
        st.undo(self.orig)
        self.assertEqual(os.path.getsize(self.orig), 1000)
        self.assertEqual(os.path.getsize(st.staged_path(self.orig)), 300)
        self.assertFalse(os.path.exists(st.kept_path(self.orig)))
        self.assertEqual(st.state(self.orig), "staged")
        st.confirm(self.orig)
        st.finalize(self.orig)
        self.assertEqual(os.path.getsize(self.orig), 300)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Show", ".reencoder")))
        self.assertEqual(st.state(self.orig), "final")

    def test_discard(self):
        st.discard(self.orig)
        self.assertEqual(os.path.getsize(self.orig), 1000)
        self.assertEqual(st.state(self.orig), "none")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Show", ".reencoder")))

    def test_refuses_wrong_state(self):
        with self.assertRaises(st.StageError):
            st.undo(self.orig)  # nothing confirmed yet
        with self.assertRaises(st.StageError):
            st.finalize(self.orig)  # finalize only after confirm
        st.confirm(self.orig)
        with self.assertRaises(st.StageError):
            st.confirm(self.orig)  # no staged copy any more
        with self.assertRaises(st.StageError):
            st.discard(self.orig)

    def test_confirm_rolls_back_when_second_move_fails(self):
        real = os.replace
        calls = []

        def flaky(a, b):
            calls.append((a, b))
            if len(calls) == 2:
                raise OSError("disk gone")
            return real(a, b)

        st.os.replace = flaky
        try:
            with self.assertRaises(OSError):
                st.confirm(self.orig)
        finally:
            st.os.replace = real
        self.assertEqual(os.path.getsize(self.orig), 1000)
        self.assertEqual(os.path.getsize(st.staged_path(self.orig)), 300)
        self.assertFalse(os.path.exists(st.kept_path(self.orig)))


class Frames(unittest.TestCase):
    def test_frame_times_spread_and_worst(self):
        t = st.frame_times(3000.0, [700.0, 1500.0], 5)
        self.assertEqual(len(t), 5)
        self.assertEqual(t, sorted(t))
        self.assertIn(700.0, t)
        self.assertIn(1500.0, t)
        thirds = [sum(1 for x in t if k * 1000 <= x < (k + 1) * 1000) for k in range(3)]
        self.assertTrue(all(n >= 1 for n in thirds))

    def test_frame_times_no_worst_and_dedupe(self):
        t = st.frame_times(3000.0, [], 5)
        self.assertEqual(len(t), 5)
        self.assertTrue(all(b - a >= 5 for a, b in zip(t, t[1:])))
        t2 = st.frame_times(3000.0, [500.0, 501.0], 5)
        self.assertEqual(len(t2), 5)
        self.assertTrue(all(b - a >= 5 for a, b in zip(t2, t2[1:])))

    def test_short_video(self):
        t = st.frame_times(12.0, [], 5)
        self.assertTrue(1 <= len(t) <= 5)
        self.assertTrue(all(0 <= x < 12.0 for x in t))

    def test_frame_cmd(self):
        c = st.frame_cmd("/m/e1.mkv", 123.5, "/data/frames/x/3-a.jpg")
        self.assertEqual(c[0], "ffmpeg")
        self.assertLess(c.index("-ss"), c.index("-i"))
        self.assertEqual(c[c.index("-ss") + 1], "123.500")
        self.assertEqual(c[c.index("-frames:v") + 1], "1")
        self.assertEqual(c[-1], "/data/frames/x/3-a.jpg")

    def test_worst_times_from_vmaf_log(self):
        # libvmaf csv log; Frame is the frame number inside the sample (also with n_subsample)
        csv = "Frame,adm2,vmaf\n0,0.9,97.0\n4,0.9,91.5\n8,0.9,96.0\n"
        self.assertAlmostEqual(st.worst_time(csv, 600.0, 24.0), 600.0 + 4 / 24.0, places=3)
        self.assertIsNone(st.worst_time("Frame,vmaf\n", 0.0, 24.0))
        self.assertIsNone(st.worst_time("", 0.0, 24.0))


if __name__ == "__main__":
    unittest.main()
