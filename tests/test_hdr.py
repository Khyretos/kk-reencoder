"""Tests for HDR10 re-encoding: colour tags kept, Dolby Vision record dropped, profile 5 skipped, the setting."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_config  # noqa: E402
import rx_db  # noqa: E402
import rx_media as x  # noqa: E402
import rx_model as m  # noqa: E402
import rx_settings as st  # noqa: E402
import rx_stage  # noqa: E402

MONARCH = {  # what ffprobe says about Monarch S01E01 (HDR10 + Dolby Vision profile 8)
    "streams": [{"index": 0, "codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
                 "pix_fmt": "yuv420p10le", "color_primaries": "bt2020", "color_transfer": "smpte2084",
                 "color_space": "bt2020nc", "avg_frame_rate": "24000/1001",
                 "side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": 8}]},
                {"index": 1, "codec_type": "audio", "codec_name": "eac3"}],
    "format": {"duration": "2988.0", "bit_rate": "29700000"},
}
CFG = {"codecs": ["hevc"], "skip_hdr": False, "max_height": 2160, "min_kbps": {"2160": 8000}}


class Probe(unittest.TestCase):
    def test_monarch(self):
        i = x.parse_probe(MONARCH, 11_000_000_000)
        self.assertTrue(i["hdr"])
        self.assertEqual(i["colour"], {"primaries": "bt2020", "transfer": "smpte2084", "space": "bt2020nc"})
        self.assertEqual(i["dv_profile"], 8)
        self.assertIsNone(m.skip_reason(i, CFG))
        self.assertEqual(m.skip_reason(i, dict(CFG, skip_hdr=True)), "HDR")

    def test_profile_5_always_skipped(self):
        p = {"streams": [dict(MONARCH["streams"][0], side_data_list=[{"side_data_type": "DOVI configuration record",
                                                                     "dv_profile": 5}])], "format": MONARCH["format"]}
        self.assertEqual(m.skip_reason(x.parse_probe(p, 1), CFG), "Dolby Vision profile 5")

    def test_sdr_has_no_colour(self):
        p = {"streams": [dict(MONARCH["streams"][0], color_transfer="bt709", side_data_list=[])],
             "format": MONARCH["format"]}
        i = x.parse_probe(p, 1)
        self.assertIsNone(i["colour"])
        self.assertIsNone(i["dv_profile"])


class Encode(unittest.TestCase):
    def test_hdr_tags_and_dolby_vision_strip(self):
        i = x.parse_probe(MONARCH, 1)
        c = x.encode_cmd("/m/e1.mkv", "/w/e1.mkv", "qsv", 26, "/dev/dri/renderD128", 0, None, hw_decode=True,
                         colour=i["colour"], dv_profile=i["dv_profile"])
        self.assertEqual(c[c.index("-color_trc:v:0") + 1], "smpte2084")
        self.assertEqual(c[c.index("-color_primaries:v:0") + 1], "bt2020")
        self.assertEqual(c[c.index("-colorspace:v:0") + 1], "bt2020nc")
        self.assertEqual(c[c.index("-bsf:v:0") + 1], "dovi_rpu=strip=1")
        self.assertLess(c.index("-bsf:v:0"), c.index("/w/e1.mkv"))

    def test_sdr_command_unchanged(self):
        c = x.encode_cmd("/m/a.mkv", "/w/a.mkv", "qsv", 24, "/dev/dri/renderD128", 0)
        self.assertNotIn("-color_trc:v:0", c)
        self.assertNotIn("-bsf:v:0", c)

    def test_frames_tone_mapped(self):
        self.assertIn("tonemap=hable", " ".join(rx_stage.frame_cmd("/m/e1.mkv", 10.0, "/f/0-a.jpg", hdr=True)))
        self.assertNotIn("tonemap", " ".join(rx_stage.frame_cmd("/m/e1.mkv", 10.0, "/f/0-a.jpg")))


class Setting(unittest.TestCase):
    def test_turning_skip_hdr_off_queues_hdr_files(self):
        conn = rx_db.connect(":memory:")
        cfg = rx_config.config_from_env({"RX_ROOTS": "/m/A"})
        for n, reason in ((1, "HDR"), (2, "already AV1")):
            rx_db.upsert_file(conn, {"path": f"/m/A/S/{n}.mkv", "library": "A", "folder": "S", "size": 1,
                                     "mtime": 1.0, "status": "skipped", "reason": reason})
        self.assertTrue(st.current(cfg)["skip_hdr"])
        st.save(conn, cfg, {"skip_hdr": False})
        self.assertFalse(cfg["skip_hdr"])
        self.assertEqual(rx_db.get_file(conn, "/m/A/S/1.mkv")["status"], "pending")
        self.assertEqual(rx_db.get_file(conn, "/m/A/S/2.mkv")["status"], "skipped")


class VmafMemory(unittest.TestCase):
    def test_4k_is_scored_at_1080p(self):
        lavfi = x.vmaf_cmd("/w/e.mkv", "/w/r.mkv", 3, None, 2160)
        lavfi = lavfi[lavfi.index("-lavfi") + 1]
        self.assertEqual(lavfi.count("scale=-2:1080:flags=bicubic,"), 2)
        self.assertNotIn("scale=", x.vmaf_cmd("/w/e.mkv", "/w/r.mkv", 3, None, 1080)[-4])
        self.assertIn("-nostats", x.vmaf_cmd("/w/e.mkv", "/w/r.mkv", 3))

    def test_error_text_drops_progress_lines(self):
        import rx_search
        err = ("frame=  289 fps=6.6 q=-0.0 size=N/A time=00:00:12.05 bitrate=N/A speed=0.277x elapsed=0:00:43.52\n"
               "me=  298 fps=6.6 q=-0.0 size=N/A time=00:00:12.42 speed=0.276x elapsed=0:00:45.02\n")
        self.assertEqual(rx_search.useful_tail(err), "")
        with self.assertRaises(RuntimeError) as e:
            rx_search._run_ok(lambda cmd: (-9, "", err), ["ffmpeg", "-i", "a"])
        self.assertEqual(str(e.exception), "ffmpeg killed (-9), most likely the container ran out of memory")


if __name__ == "__main__":
    unittest.main()
