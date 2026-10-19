"""Tests for app/rx_media.py (ffprobe parsing and ffmpeg command builders; nothing is run)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_media as x  # noqa: E402

PROBE = {
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "pix_fmt": "yuv420p", "color_transfer": "bt709", "avg_frame_rate": "24000/1001",
         "disposition": {"attached_pic": 0}},
        {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        {"index": 2, "codec_type": "subtitle", "codec_name": "ass"},
        {"index": 3, "codec_type": "attachment", "codec_name": "ttf"},
    ],
    "format": {"duration": "1420.5", "bit_rate": "8500000", "format_name": "matroska,webm"},
}


class ParseProbe(unittest.TestCase):
    def test_basic(self):
        i = x.parse_probe(PROBE, 1_500_000_000)
        self.assertEqual(i["codec"], "h264")
        self.assertEqual(i["width"], 1920)
        self.assertEqual(i["height"], 1080)
        self.assertFalse(i["hdr"])
        self.assertAlmostEqual(i["duration_s"], 1420.5)
        self.assertEqual(i["bitrate_kbps"], 8500)
        self.assertEqual(i["size"], 1_500_000_000)
        self.assertEqual(i["audio"], 1)
        self.assertEqual(i["subs"], 1)
        self.assertAlmostEqual(i["fps"], 23.976, places=2)
        self.assertFalse(i["ten_bit"])

    def test_hdr_and_ten_bit(self):
        p = {"streams": [dict(PROBE["streams"][0], codec_name="hevc", pix_fmt="yuv420p10le",
                              color_transfer="smpte2084")], "format": PROBE["format"]}
        i = x.parse_probe(p, 1)
        self.assertTrue(i["hdr"])
        self.assertTrue(i["ten_bit"])

    def test_bitrate_from_size_when_missing(self):
        p = {"streams": PROBE["streams"], "format": {"duration": "100"}}
        self.assertEqual(x.parse_probe(p, 12_500_000)["bitrate_kbps"], 1000)

    def test_skips_cover_art(self):
        cover = {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600,
                 "disposition": {"attached_pic": 1}}
        p = {"streams": [cover] + PROBE["streams"], "format": PROBE["format"]}
        i = x.parse_probe(p, 1)
        self.assertEqual(i["codec"], "h264")
        self.assertEqual(i["video_index"], 0)  # index among video streams, cover art excluded

    def test_no_video(self):
        self.assertIsNone(x.parse_probe({"streams": [PROBE["streams"][1]], "format": {}}, 1))


class Commands(unittest.TestCase):
    def test_encoder_args(self):
        qsv = x.encoder_args("qsv", 24, "/dev/dri/renderD128")
        self.assertIn("av1_qsv", qsv)
        self.assertEqual(qsv[qsv.index("-global_quality") + 1], "24")
        svt = x.encoder_args("svt", 30, None)
        self.assertIn("libsvtav1", svt)
        self.assertEqual(svt[svt.index("-crf") + 1], "30")
        self.assertIn("av1_nvenc", x.encoder_args("nvenc", 30, None))
        self.assertIn("av1_vaapi", x.encoder_args("vaapi", 30, "/dev/dri/renderD128"))
        with self.assertRaises(ValueError):
            x.encoder_args("x265", 30, None)

    def test_hw_init(self):
        self.assertEqual(x.hw_init_args("qsv", "/dev/dri/renderD129"), ["-init_hw_device", "qsv=hw:/dev/dri/renderD129"])
        self.assertEqual(x.hw_init_args("svt", None), [])

    def test_sample_cmd(self):
        c = x.sample_cmd("/m/a.mkv", "/w/s.mkv", 300, 20, 0)
        self.assertEqual(c[0], "ffmpeg")
        self.assertLess(c.index("-ss"), c.index("-i"))
        self.assertIn("/m/a.mkv", c)
        self.assertEqual(c[c.index("-map") + 1], "0:v:0")
        self.assertEqual(c[c.index("-c") + 1], "copy")
        self.assertEqual(c[-1], "/w/s.mkv")

    def test_reference_cmd_is_lossless(self):
        # VMAF needs a lossless decoded reference: a stream-copied h264 sample decodes with timing
        # differences that cap the score near 94 even for files larger than the source
        c = x.reference_cmd("/w/s.mkv", "/w/s-ref.mkv")
        self.assertEqual(c[c.index("-i") + 1], "/w/s.mkv")
        self.assertEqual(c[c.index("-c:v") + 1], "ffv1")
        self.assertEqual(c[-1], "/w/s-ref.mkv")

    def test_source_reference_cmd(self):
        c = x.source_reference_cmd("/m/a.mkv", "/w/r.mkv", 300, 20, 1)
        self.assertEqual(c[0], "ffmpeg")
        self.assertLess(c.index("-ss"), c.index("-i"))
        self.assertEqual(c[c.index("-map") + 1], "0:v:1")
        self.assertEqual(c[c.index("-c:v") + 1], "ffv1")
        self.assertEqual(c[-1], "/w/r.mkv")

    def test_full_encode_cmd_mkv_keeps_everything(self):
        c = x.encode_cmd("/m/a.mkv", "/w/a.mkv", "qsv", 24, "/dev/dri/renderD128", 0)
        joined = " ".join(c)
        for part in ("-map 0:v:0", "-map 0:a?", "-map 0:s?", "-map 0:t?", "-map_chapters 0", "-c copy",
                     "-c:v:0 av1_qsv", "-progress pipe:1"):
            self.assertIn(part, joined)
        self.assertEqual(c[-1], "/w/a.mkv")

    def test_full_gpu_path(self):
        c = x.encode_cmd("/m/a.mkv", "/w/a.mkv", "qsv", 24, "/dev/dri/renderD128", 0, None, hw_decode=True)
        self.assertLess(c.index("-hwaccel"), c.index("-i"))
        self.assertEqual(c[c.index("-hwaccel_output_format") + 1], "qsv")
        self.assertEqual(c[c.index("-vf") + 1], "vpp_qsv=format=p010")
        self.assertNotIn("-pix_fmt:v:0", c)
        # a CPU filter (deband) or another encoder keeps the CPU path
        self.assertNotIn("-hwaccel", x.encode_cmd("/m/a.mkv", "/w/a.mkv", "qsv", 24, "/dev/dri/renderD128", 0, "deband", hw_decode=True))
        self.assertNotIn("-hwaccel", x.encode_cmd("/m/a.mkv", "/w/a.mkv", "svt", 24, None, 0, None, hw_decode=True))

    def test_full_encode_cmd_mp4_no_attachments(self):
        c = " ".join(x.encode_cmd("/m/a.mp4", "/w/a.mp4", "svt", 30, None, 0))
        self.assertNotIn("0:t?", c)
        self.assertIn("-c:s mov_text", c)

    def test_filters(self):
        self.assertEqual(x.filter_args(None), [])
        self.assertEqual(x.filter_args("deband"), ["-vf", "deband=1thr=0.02:2thr=0.02:3thr=0.02:4thr=0.02:range=16"])
        with self.assertRaises(ValueError):
            x.filter_args("sharpen-everything")
        # qsv/svt keep their pixel format, the filter goes in front
        c = x.sample_encode_cmd("/w/r.mkv", "/w/o.mkv", "qsv", 22, "/dev/dri/renderD128", "deband")
        self.assertEqual(c[0], "ffmpeg")
        self.assertIn("deband=", " ".join(c))
        self.assertEqual(c.count("-vf"), 1)
        c = x.encode_cmd("/m/a.mkv", "/w/a.mkv", "qsv", 22, "/dev/dri/renderD128", 0, "deband")
        self.assertEqual(c.count("-vf"), 1)
        self.assertEqual(c.count("-c:v:0"), 1)
        self.assertEqual(c[0], "ffmpeg")
        v = x.sample_encode_cmd("/w/r.mkv", "/w/o.mkv", "vaapi", 22, "/dev/dri/renderD128", "deband")
        self.assertTrue(v[v.index("-vf") + 1].startswith("deband=") and v[v.index("-vf") + 1].endswith("hwupload"))

    def test_vmaf_cmd(self):
        c = x.vmaf_cmd("/w/enc.mkv", "/w/ref.mkv", 4)
        self.assertEqual(c[c.index("-i") + 1], "/w/enc.mkv")
        lavfi = c[c.index("-lavfi") + 1]
        self.assertIn("libvmaf", lavfi)
        self.assertIn("n_threads=4", lavfi)
        self.assertIn("setpts=PTS-STARTPTS", lavfi)

    def test_vmaf_cmd_with_log(self):
        c = x.vmaf_cmd("/w/enc.mkv", "/w/ref.mkv", 4, "/w/v.csv")
        self.assertIn("log_fmt=csv:log_path=/w/v.csv", c[c.index("-lavfi") + 1])
        self.assertNotIn("log_path", x.vmaf_cmd("/w/enc.mkv", "/w/ref.mkv", 4)[c.index("-lavfi") + 1])

    def test_parse_vmaf(self):
        self.assertAlmostEqual(x.parse_vmaf("blah\n[Parsed_libvmaf_4 @ 0x1] VMAF score: 95.512\n"), 95.512)
        self.assertIsNone(x.parse_vmaf("nothing here"))

    def test_sample_points(self):
        self.assertEqual(x.sample_points(1000, 3, 20), [240, 490, 740])
        self.assertEqual(x.sample_points(30, 3, 20), [0])


if __name__ == "__main__":
    unittest.main()
