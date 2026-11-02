"""VMAF on the GPU (libvmaf_sycl from the kk-vmafx base image): every frame, checked once against the CPU."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_media  # noqa: E402
import rx_search  # noqa: E402

CFG = {"vmaf_threads": 3, "vmaf_gpu": "auto"}
INFO = {"height": 1080}


class FakeRun:
    """gpu / cpu: the score each side answers (None = the command fails)."""

    def __init__(self, gpu, cpu):
        self.gpu, self.cpu, self.cmds = gpu, cpu, []

    def __call__(self, cmd, timeout=None, on_line=None):
        self.cmds.append(cmd)
        score = self.gpu if "libvmaf_sycl" in cmd[cmd.index("-lavfi") + 1] else self.cpu
        if score is None:
            return 1, "", "No device of requested type available"
        return 0, "", f"VMAF score: {score}\n"


class GpuVmaf(unittest.TestCase):
    def setUp(self):
        self.saved = rx_media.VMAF_GPU_FFMPEG
        rx_media.VMAF_GPU_FFMPEG = sys.executable  # any file that exists stands in for the kk-vmafx ffmpeg
        rx_search._gpu_vmaf["ok"] = None

    def tearDown(self):
        rx_media.VMAF_GPU_FFMPEG = self.saved
        rx_search._gpu_vmaf["ok"] = None

    def lavfis(self, run):
        return [c[c.index("-lavfi") + 1] for c in run.cmds]

    def test_cmd(self):
        c = rx_media.vmaf_cmd("/w/e.mkv", "/w/r.mkv", 3, "/w/l.csv", 1080, gpu=True)
        self.assertEqual(c[0], rx_media.VMAF_GPU_FFMPEG)
        lavfi = c[c.index("-lavfi") + 1]
        self.assertIn("libvmaf_sycl=", lavfi)
        self.assertIn("n_subsample=1", lavfi)  # its n_subsample > 1 is broken
        self.assertIn("log_path=/w/l.csv", lavfi)
        self.assertEqual(rx_media.vmaf_cmd("/w/e.mkv", "/w/r.mkv", 3)[0], "ffmpeg")

    def test_matching_first_score_switches_to_gpu(self):
        run = FakeRun(94.703496, 94.703496)
        self.assertEqual(rx_search._vmaf_score("e", "r", "l", INFO, CFG, run), 94.703496)
        self.assertTrue(rx_search.gpu_vmaf_state())
        self.assertIn("n_subsample=1", self.lavfis(run)[1])  # the check scores every frame on the CPU too
        run.cmds.clear()
        rx_search._vmaf_score("e", "r", "l", INFO, CFG, run)
        self.assertEqual(len(run.cmds), 1)  # after the check: GPU only
        self.assertIn("libvmaf_sycl", self.lavfis(run)[0])

    def test_mismatch_stays_on_cpu(self):
        run = FakeRun(99.96, 94.70)
        self.assertEqual(rx_search._vmaf_score("e", "r", "l", INFO, CFG, run), 94.70)
        self.assertFalse(rx_search.gpu_vmaf_state())
        run.cmds.clear()
        rx_search._vmaf_score("e", "r", "l", INFO, CFG, run)
        self.assertEqual(["libvmaf_sycl" in l for l in self.lavfis(run)], [False])
        self.assertIn("n_subsample=4", self.lavfis(run)[0])

    def test_gpu_error_stays_on_cpu(self):
        run = FakeRun(None, 95.1)
        self.assertEqual(rx_search._vmaf_score("e", "r", "l", INFO, CFG, run), 95.1)
        self.assertFalse(rx_search.gpu_vmaf_state())

    def test_small_videos_stay_on_the_cpu(self):
        run = FakeRun(95.0, 95.0)
        rx_search._vmaf_score("e", "r", "l", {"height": 480}, CFG, run)
        self.assertEqual(["libvmaf_sycl" in l for l in self.lavfis(run)], [False])

    def test_off_or_missing_never_tries_the_gpu(self):
        run = FakeRun(95.0, 95.0)
        rx_search._vmaf_score("e", "r", "l", INFO, dict(CFG, vmaf_gpu="off"), run)
        rx_media.VMAF_GPU_FFMPEG = "/nonexistent/ffmpeg"
        rx_search._vmaf_score("e", "r", "l", INFO, CFG, run)
        self.assertFalse(any("libvmaf_sycl" in l for l in self.lavfis(run)))
        self.assertIsNone(rx_search.gpu_vmaf_state())


if __name__ == "__main__":
    unittest.main()
