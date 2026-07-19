from __future__ import annotations

from dataclasses import dataclass, field

from cchub.ssh import Remote

_BLOCKS = "▁▂▃▄▅▆▇█"


@dataclass
class RawStats:
    cpu_total: int
    cpu_idle: int
    mem_total_kb: int
    mem_avail_kb: int


def parse_proc(text: str) -> RawStats | None:
    """`cat /proc/stat /proc/meminfo` 출력에서 필요한 값만 추출. 실패 시 None."""
    cpu_total = cpu_idle = mem_total = mem_avail = None
    try:
        for line in text.splitlines():
            if line.startswith("cpu "):
                nums = [int(x) for x in line.split()[1:]]
                cpu_total = sum(nums)
                cpu_idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle+iowait
            elif line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1])
    except (ValueError, IndexError):
        return None
    if None in (cpu_total, cpu_idle, mem_total, mem_avail):
        return None
    return RawStats(cpu_total, cpu_idle, mem_total, mem_avail)


def read_stats(remote: Remote) -> RawStats | None:
    r = remote.run(["cat", "/proc/stat", "/proc/meminfo"], timeout=5)
    if r.rc != 0:
        return None
    return parse_proc(r.out)


def cpu_percent(prev: RawStats, cur: RawStats) -> float:
    dt = cur.cpu_total - prev.cpu_total
    if dt <= 0:
        return 0.0
    di = cur.cpu_idle - prev.cpu_idle
    return max(0.0, min(100.0, 100.0 * (dt - di) / dt))


def sparkline(values: list[float]) -> str:
    return "".join(_BLOCKS[min(7, int(v * 8 / 100.0))] for v in values)


@dataclass
class ServerStats:
    history: list[float] = field(default_factory=list)
    last_raw: RawStats | None = None
    online: bool = False

    def update(self, raw: RawStats | None, keep: int = 30) -> None:
        if raw is None:
            self.online = False
            self.last_raw = None
            return
        if self.last_raw is not None:
            self.history.append(cpu_percent(self.last_raw, raw))
            del self.history[:-keep]
        self.last_raw = raw
        self.online = True

    def label(self, name: str) -> str:
        if not self.online or self.last_raw is None:
            return f"{name} ⨯offline"
        cpu = self.history[-1] if self.history else 0.0
        used_g = (self.last_raw.mem_total_kb - self.last_raw.mem_avail_kb) / 1048576
        total_g = self.last_raw.mem_total_kb / 1048576
        return f"{name} {sparkline(self.history[-8:])} {cpu:.0f}% {used_g:.0f}G/{total_g:.0f}G"
