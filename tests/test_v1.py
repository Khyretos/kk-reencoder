"""Tests for 1.0: measured files queued again, Retry on skipped files, bulk actions, hardware detection."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_hw  # noqa: E402
import rx_stage  # noqa: E402
from rx_app import App  # noqa: E402


def add(conn, path, status, folder="Show", reason=None, codec="h264", height=1080, new=None, quality=None):
    rx_db.upsert_file(conn, {"path": path, "library": "Anime", "folder": folder, "size": 1000, "mtime": 1,
                             "codec": codec, "height": height, "bitrate_kbps": 4000, "duration_s": 100,
                             "status": status, "reason": reason})
    if new is not None or quality is not None:
        rx_db.set_result(conn, path, new_bytes=new, quality=quality)


class V1(unittest.TestCase):
    def setUp(self):
        self.conn = rx_db.connect(":memory:")
        self.cfg = rx_config.config_from_env({"RX_ROOTS": "/m/Anime", "RX_MODE": "manual"})
        self.app = App(self.conn, self.cfg, None)

    def st(self, path):
        return rx_db.get_file(self.conn, path)["status"]

    # --- measured-only files ---------------------------------------------------------------------------------
    def test_measured_files_wait_in_report_and_unticked_manual(self):
        add(self.conn, "/m/Anime/Show/1.mkv", "predicted", new=400, quality=24)
        self.assertEqual(rx_db.queue_measured(self.conn, "report"), 0)
        self.assertEqual(rx_db.queue_measured(self.conn, "manual"), 0)  # series not ticked
        self.assertEqual(self.st("/m/Anime/Show/1.mkv"), "predicted")

    def test_automatic_queues_every_measured_file(self):
        add(self.conn, "/m/Anime/Show/1.mkv", "predicted", new=400, quality=24)
        add(self.conn, "/m/Anime/Other/2.mkv", "predicted", folder="Other", new=400, quality=24)
        self.app.set_settings({"mode": "stage"})
        self.assertEqual(self.st("/m/Anime/Show/1.mkv"), "pending")
        self.assertEqual(self.st("/m/Anime/Other/2.mkv"), "pending")
        self.assertEqual(rx_db.next_file(self.conn)["status"], "pending")  # the worker sees work again

    def test_ticking_a_series_in_manual_queues_its_measured_files(self):
        add(self.conn, "/m/Anime/Show/1.mkv", "predicted", new=400)
        add(self.conn, "/m/Anime/Other/2.mkv", "predicted", folder="Other", new=400)
        self.app.set_folder("Anime", "Show", encode=True)
        self.assertEqual(self.st("/m/Anime/Show/1.mkv"), "pending")
        self.assertEqual(self.st("/m/Anime/Other/2.mkv"), "predicted")

    # --- Retry on skipped files --------------------------------------------------------------------------------
    def test_retry_skipped_from_quality_search(self):
        add(self.conn, "/m/Anime/Show/q.mkv", "skipped", reason="never reached VMAF 95: best was 93.0 at q 11")
        row = self.app.file_action("/m/Anime/Show/q.mkv", "retry")
        self.assertEqual((row["status"], row["reason"]), ("pending", rx_db.RETRY_REASON))

    def test_retry_skipped_refuses_what_settings_still_exclude(self):
        add(self.conn, "/m/Anime/Show/av1.mkv", "skipped", reason="already AV1", codec="av1")
        add(self.conn, "/m/Anime/Show/hdr.mkv", "skipped", reason="HDR")
        for p in ("/m/Anime/Show/av1.mkv", "/m/Anime/Show/hdr.mkv"):
            with self.assertRaises(rx_stage.StageError):
                self.app.file_action(p, "retry")
            self.assertEqual(self.st(p), "skipped")
        self.app.set_settings({"skip_hdr": False})  # puts HDR skips back by itself
        self.assertEqual(self.st("/m/Anime/Show/hdr.mkv"), "pending")

    def test_folder_retry_skipped_counts_what_stays(self):
        add(self.conn, "/m/Anime/Show/a.mkv", "skipped", reason="the whole AV1 file came out at 104% of the original size, so nothing to gain")
        add(self.conn, "/m/Anime/Show/b.mkv", "skipped", reason="already AV1", codec="av1")
        add(self.conn, "/m/Anime/Show/c.mkv", "skipped", reason="codec not selected", codec="vp9")
        r = self.app.folder_action("Anime", "Show", "retry_skipped")
        self.assertEqual((r["done"], r["kept"], r["failed"]), (1, 2, []))

    # --- bulk --------------------------------------------------------------------------------------------------
    def test_bulk_over_several_series(self):
        add(self.conn, "/m/Anime/Show/f.mkv", "failed")
        add(self.conn, "/m/Anime/Other/g.mkv", "failed", folder="Other")
        add(self.conn, "/m/Anime/Other/h.mkv", "skipped", folder="Other", reason="at VMAF 95 the AV1 copy would be 120% of the original size; the size limit is 99%")
        items = [{"library": "Anime", "folder": "Show"}, {"library": "Anime", "folder": "Other"}]
        self.assertEqual(self.app.bulk("retry", items)["done"], 2)
        self.assertEqual(self.app.bulk("retry_skipped", items)["done"], 1)
        self.assertEqual(self.app.bulk("encode", items)["done"], 2)
        self.assertTrue(all(f["encode"] for f in rx_db.folders(self.conn)))
        with self.assertRaises(ValueError):
            self.app.bulk("delete_everything", items)

    def test_bulk_confirm_and_finalize_real_files(self):
        d = os.path.join(tempfile.mkdtemp(), "Anime")  # the library root is named after the library
        paths = []
        for i in range(2):
            p = os.path.join(d, "Show", f"e{i}.mkv")
            os.makedirs(os.path.dirname(rx_stage.staged_path(p)), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(b"o" * 1000)
            with open(rx_stage.staged_path(p), "wb") as fh:
                fh.write(b"n" * 300)
            rx_db.upsert_file(self.conn, {"path": p, "library": "Anime", "folder": "Show", "size": 1000, "mtime": 1,
                                          "status": "staged"})
            rx_db.set_result(self.conn, p, new_bytes=300)
            paths.append(p)
        self.cfg["report_dir"] = os.path.join(d, "reports")
        self.cfg["roots"] = [d]
        items = [{"library": "Anime", "folder": "Show"}]
        self.assertEqual(self.app.bulk("confirm", items)["done"], 2)
        self.assertEqual([self.st(p) for p in paths], ["confirmed"] * 2)
        self.assertEqual(self.app.bulk("finalize", items)["done"], 2)
        self.assertEqual([self.st(p) for p in paths], ["encoded"] * 2)
        self.assertEqual(os.path.getsize(paths[0]), 300)


class Hardware(unittest.TestCase):
    def fake(self, works):
        tried = []

        def run(cmd, env):
            enc = next(a for a in cmd if a.startswith(("av1_", "libsvtav1")))
            tried.append((enc, env.get("LIBVA_DRIVER_NAME")))
            return (0, "") if enc in works else (1, "Error: no device")
        return run, tried

    def test_intel_arc_picks_qsv(self):
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a"})
        run, tried = self.fake({"av1_qsv"})
        info = rx_hw.detect(cfg, run, [{"node": "/dev/dri/renderD128", "vendor": "intel"}])
        self.assertEqual((cfg["encoder"], cfg["device"], info["vendor"]), ("qsv", "/dev/dri/renderD128", "intel"))
        self.assertEqual(tried, [("av1_qsv", "iHD")])

    def test_amd_uses_vaapi_with_radeonsi(self):
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a"})
        run, tried = self.fake({"av1_vaapi"})
        rx_hw.detect(cfg, run, [{"node": "/dev/dri/renderD129", "vendor": "amd"}])
        self.assertEqual((cfg["encoder"], cfg["device"]), ("vaapi", "/dev/dri/renderD129"))
        self.assertEqual(tried[0][1], "radeonsi")

    def test_nvidia_then_cpu_fallback(self):
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a"})
        run, tried = self.fake({"libsvtav1"})  # an older NVIDIA card without AV1 encode
        info = rx_hw.detect(cfg, run, [{"node": None, "vendor": "nvidia"}])
        self.assertEqual(cfg["encoder"], "svt")
        self.assertEqual([t["encoder"] for t in info["tried"]], ["nvenc", "svt"])
        self.assertFalse(info["tried"][0]["ok"])

    def test_no_gpu_means_cpu(self):
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a"})
        run, _ = self.fake({"libsvtav1"})
        rx_hw.detect(cfg, run, [])
        self.assertEqual(cfg["encoder"], "svt")

    def test_explicit_encoder_is_not_tested(self):
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a", "RX_ENCODER": "qsv"})
        run, tried = self.fake(set())
        rx_hw.detect(cfg, run, [{"node": "/dev/dri/renderD128", "vendor": "intel"}])
        self.assertEqual((cfg["encoder"], tried), ("qsv", []))

    def test_gpus_reads_vendor_from_sysfs(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "dev", "dri"))
        open(os.path.join(root, "dev", "dri", "renderD128"), "w").close()
        os.makedirs(os.path.join(root, "sys", "class", "drm", "renderD128", "device"))
        with open(os.path.join(root, "sys", "class", "drm", "renderD128", "device", "vendor"), "w") as fh:
            fh.write("0x8086\n")
        open(os.path.join(root, "dev", "nvidiactl"), "w").close()
        found = rx_hw.gpus(os.path.join(root, "dev"), os.path.join(root, "sys"))
        self.assertEqual([g["vendor"] for g in found], ["intel", "nvidia"])

    def test_real_cpu_encoder_works_here(self):
        # the test machine's ffmpeg: libsvtav1 should encode the generated picture (skipped when not built in)
        import shutil
        import subprocess
        if not shutil.which("ffmpeg") or "libsvtav1" not in subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout:
            self.skipTest("no ffmpeg with libsvtav1")
        cfg = rx_config.config_from_env({"RX_ROOTS": "/a"})
        rx_hw.detect(cfg, None, [])
        self.assertEqual(cfg["encoder"], "svt")
        self.assertTrue(cfg["hardware"]["tried"][0]["ok"], cfg["hardware"]["tried"])


if __name__ == "__main__":
    unittest.main()
