"""
rx_sys: Live CPU and memory numbers for the page, for the whole container (cgroup v2)
and for each running encode (its ffmpeg processes).

Standard library only (os, time).
"""

import os
import time


def parse_stat(text: str) -> tuple[int, int]:
    """
    Parse one /proc/<pid>/stat line.

    Returns (utime + stime, rss) as ints.
    The process name in parentheses can contain spaces and parentheses, so split after the LAST ')'.
    After it the fields are state, ppid, ... with utime as field 14 and stime 15 and rss 24 of the whole line (1-based, counting pid as 1 and the name as 2).
    """
    # Split by space, but handle the parenthesized name correctly.
    # Strategy: find the last ')' to separate name from rest.
    last_close = text.rfind(")")
    if last_close == -1:
        raise ValueError("No closing parenthesis found in stat line")

    # Everything before last_close is "PID NAME"
    prefix = text[:last_close]
    suffix = text[last_close + 1:]

    # PID is first token
    parts = prefix.split()
    if len(parts) < 2:
        raise ValueError("Invalid stat line: missing name")

    pid = int(parts[0])

    # Now split suffix into fields. They are space-separated.
    fields = suffix.split()
    # We need fields at indices 13 (utime), 14 (stime), 23 (rss) because suffix starts after PID and Name.
    # Wait: the comment says "counting pid as 1 and the name as 2".
    # So in the FULL line (including PID and Name):
    # Field 1: PID
    # Field 2: Name (in parens)
    # Field 3: State
    # ...
    # Field 14: utime
    # Field 15: stime
    # Field 24: rss

    # Our 'fields' list corresponds to fields 3..end of the full line.
    # So index i in 'fields' corresponds to field i+3 in the full line.
    # We need field 14 -> index 11
    # We need field 15 -> index 12
    # We need field 24 -> index 21

    if len(fields) < 22:
        raise ValueError("Stat line too short")

    utime = int(fields[11])
    stime = int(fields[12])
    rss_pages = int(fields[21])

    return (utime + stime, rss_pages)


def cgroup_cpu_usec(root: str) -> int | None:
    """
    Return the usage_usec value from <root>/cpu.stat, None when unreadable.
    """
    path = os.path.join(root, "cpu.stat")
    try:
        with open(path, "r") as f:
            for line in f:
                if line.startswith("usage_usec "):
                    return int(line.split()[1])
    except (OSError, IOError, ValueError):
        pass
    return None


def cgroup_memory(root: str) -> tuple[int, int | None]:
    """
    Return (memory.current, memory.max) from <root> as ints; 'max' -> None; (None, None) when unreadable.
    """
    current_path = os.path.join(root, "memory.current")
    max_path = os.path.join(root, "memory.max")

    try:
        with open(current_path, "r") as f:
            current = int(f.read().strip())
    except (OSError, IOError, ValueError):
        return (None, None)

    try:
        with open(max_path, "r") as f:
            content = f.read().strip()
            if content == "max":
                max_val = None
            else:
                max_val = int(content)
    except (OSError, IOError, ValueError):
        return (current, None)

    return (current, max_val)


class Sampler:
    """
    Sample CPU and memory usage for the container and for each job.

    Args:
        proc_root: Root of /proc-like directory (default '/proc').
        cgroup_root: Root of cgroup files (default '/sys/fs/cgroup').
        clock: Callable returning monotonic time (default time.monotonic).
        ticks_per_s: Clock ticks per second (default os.sysconf('SC_CLK_TCK')).
        page_size: Page size in bytes (default os.sysconf('SC_PAGE_SIZE')).
        cpus: Number of CPUs (default quota from <cgroup_root>/cpu.max).
    """

    def __init__(
        self,
        proc_root: str = "/proc",
        cgroup_root: str = "/sys/fs/cgroup",
        clock: callable = time.monotonic,
        ticks_per_s: int = os.sysconf("SC_CLK_TCK"),
        page_size: int = os.sysconf("SC_PAGE_SIZE"),
        cpus: int | None = None,
    ):
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.clock = clock
        self.ticks_per_s = ticks_per_s
        self.page_size = page_size

        # Determine cpus
        if cpus is not None:
            self.cpus = cpus
        else:
            cpu_max_path = os.path.join(cgroup_root, "cpu.max")
            try:
                with open(cpu_max_path, "r") as f:
                    content = f.read().strip()
                    parts = content.split()
                    if len(parts) >= 2:
                        # Format: "quota period" or "max period"
                        if parts[0] != "max":
                            quota = int(parts[0])
                            period = int(parts[1])
                            self.cpus = quota / period
                        else:
                            self.cpus = os.cpu_count() or 1
                    else:
                        self.cpus = os.cpu_count() or 1
            except (OSError, IOError, ValueError):
                self.cpus = os.cpu_count() or 1

        # Per-pid tick storage: pid -> total_ticks (utime + stime)
        self._pid_ticks: dict[int, int] = {}
        # Previous sample data for delta calculation
        self._prev_clock: float | None = None
        self._prev_cpu_usec: int | None = None
        self._prev_job_tick_sums: dict[str, int] = {}

    def _get_pid_ticks(self, pid: int) -> int:
        """Get accumulated ticks for a PID, initializing to 0 if new."""
        if pid not in self._pid_ticks:
            self._pid_ticks[pid] = 0
        return self._pid_ticks[pid]

    def _save_pid_ticks(self, pid: int, ticks: int):
        """Save accumulated ticks for a PID."""
        self._pid_ticks[pid] = ticks

    def _procs(self):
        """(pid, args, ticks, rss_pages) of every readable process; vanished ones are skipped."""
        try:
            entries = os.listdir(self.proc_root)
        except OSError:
            return
        for e in entries:
            if not e.isdigit():
                continue
            d = os.path.join(self.proc_root, e)
            try:
                with open(os.path.join(d, "stat")) as f:
                    ticks, rss = parse_stat(f.read())
                with open(os.path.join(d, "cmdline"), "rb") as f:
                    args = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            except (OSError, ValueError, IndexError):
                continue
            yield int(e), args, ticks, rss

    def sample(self, job_paths: list[str]) -> dict:
        """Container CPU/memory since the previous call, and per job its ffmpeg processes."""
        now = self.clock()
        elapsed = None if self._prev_clock is None else now - self._prev_clock
        cpu_usec = cgroup_cpu_usec(self.cgroup_root)
        mem_current, mem_limit = cgroup_memory(self.cgroup_root)
        cpu_pct = cpu_share = None
        if elapsed and cpu_usec is not None and self._prev_cpu_usec is not None:
            cpu_pct = 100.0 * (cpu_usec - self._prev_cpu_usec) / 1_000_000 / elapsed
            cpu_share = cpu_pct / self.cpus if self.cpus else None

        jobs = {jp: {"cpu_pct": None, "rss_bytes": 0, "procs": 0} for jp in job_paths}
        delta = {jp: 0 for jp in job_paths}
        known = {jp: False for jp in job_paths}  # a pid of this job was seen at the previous sample
        ticks_now: dict[int, int] = {}
        for pid, args, ticks, rss in self._procs():
            ticks_now[pid] = ticks
            jp = next((j for j in job_paths if any(j == a or j in a for a in args)), None)
            if jp is None:
                continue
            jobs[jp]["procs"] += 1
            jobs[jp]["rss_bytes"] += rss * self.page_size
            if pid in self._pid_ticks:
                known[jp] = True
            delta[jp] += ticks - self._pid_ticks.get(pid, 0)
        for jp in job_paths:
            if elapsed and known[jp]:
                jobs[jp]["cpu_pct"] = 100.0 * delta[jp] / self.ticks_per_s / elapsed

        self._pid_ticks = ticks_now
        self._prev_clock = now
        self._prev_cpu_usec = cpu_usec
        return {"cpu_pct": cpu_pct, "cpu_share": cpu_share, "cpus": self.cpus,
                "mem_bytes": mem_current, "mem_limit": mem_limit, "jobs": jobs}
