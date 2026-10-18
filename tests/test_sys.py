"""Tests for app/rx_sys.py: CPU and memory of the container and of each ffmpeg, from fake /proc and cgroup files."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import rx_sys  # noqa: E402

STAT = "1234 (ffmpeg) S 1 1234 1234 0 -1 4194560 100 0 0 0 {ut} {st} 0 0 39 19 8 0 100 1000000 {rss} 18446744073709551615"


class Sys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proc = os.path.join(self.tmp, "proc")
        self.cg = os.path.join(self.tmp, "cg")
        os.makedirs(self.cg)
        self.set_cg(usage=1_000_000, mem=512 * 1024**2, limit=3 * 1024**3)
        self.set_proc(1234, ["ffmpeg", "-i", "/media/Anime/Show/e1.mkv", "out.mkv"], ut=100, st=0, rss=25600)
        self.set_proc(77, ["python3", "/app/server.py"], ut=5, st=5, rss=1000)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def set_cg(self, usage, mem, limit):
        with open(os.path.join(self.cg, "cpu.stat"), "w") as f:
            f.write(f"usage_usec {usage}\nuser_usec 1\nsystem_usec 1\n")
        with open(os.path.join(self.cg, "memory.current"), "w") as f:
            f.write(f"{mem}\n")
        with open(os.path.join(self.cg, "memory.max"), "w") as f:
            f.write("max\n" if limit is None else f"{limit}\n")

    def set_proc(self, pid, argv, ut, st, rss):
        d = os.path.join(self.proc, str(pid))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "stat"), "w") as f:
            f.write(STAT.format(ut=ut, st=st, rss=rss))
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_parse_stat(self):
        ticks, rss_pages = rx_sys.parse_stat(STAT.format(ut=100, st=20, rss=300))
        self.assertEqual(ticks, 120)
        self.assertEqual(rss_pages, 300)

    def test_parse_stat_with_spaces_in_name(self):
        line = "9 (my (odd) prog) R 1 9 9 0 -1 0 0 0 0 0 7 3 0 0 20 0 1 0 1 1 42 1"
        self.assertEqual(rx_sys.parse_stat(line), (10, 42))

    def test_cgroup(self):
        self.assertEqual(rx_sys.cgroup_cpu_usec(self.cg), 1_000_000)
        self.assertEqual(rx_sys.cgroup_memory(self.cg), (512 * 1024**2, 3 * 1024**3))
        self.set_cg(usage=1, mem=10, limit=None)
        self.assertEqual(rx_sys.cgroup_memory(self.cg), (10, None))
        self.assertIsNone(rx_sys.cgroup_cpu_usec(os.path.join(self.tmp, "nope")))

    def test_sampler(self):
        clock = [100.0]
        s = rx_sys.Sampler(proc_root=self.proc, cgroup_root=self.cg, clock=lambda: clock[0],
                           ticks_per_s=100, page_size=4096, cpus=4)
        first = s.sample(["/media/Anime/Show/e1.mkv"])
        self.assertIsNone(first["cpu_pct"])  # needs two samples
        self.assertEqual(first["mem_bytes"], 512 * 1024**2)
        self.assertEqual(first["mem_limit"], 3 * 1024**3)
        clock[0] = 102.0
        self.set_cg(usage=1_000_000 + 4_000_000, mem=600 * 1024**2, limit=3 * 1024**3)  # 4 cpu-seconds in 2 s
        self.set_proc(1234, ["ffmpeg", "-i", "/media/Anime/Show/e1.mkv", "out.mkv"], ut=400, st=0, rss=25600)
        two = s.sample(["/media/Anime/Show/e1.mkv"])
        self.assertAlmostEqual(two["cpu_pct"], 200.0)  # percent of one core
        self.assertAlmostEqual(two["cpu_share"], 50.0)  # percent of the 4 allowed cores
        job = two["jobs"]["/media/Anime/Show/e1.mkv"]
        self.assertAlmostEqual(job["cpu_pct"], 150.0)  # 300 ticks in 2 s at 100 ticks/s
        self.assertEqual(job["rss_bytes"], 25600 * 4096)
        self.assertEqual(job["procs"], 1)

    def test_job_without_process(self):
        s = rx_sys.Sampler(proc_root=self.proc, cgroup_root=self.cg, clock=lambda: 1.0,
                           ticks_per_s=100, page_size=4096, cpus=4)
        out = s.sample(["/media/Other/x.mkv"])
        self.assertEqual(out["jobs"]["/media/Other/x.mkv"], {"cpu_pct": None, "rss_bytes": 0, "procs": 0})


if __name__ == "__main__":
    unittest.main()
