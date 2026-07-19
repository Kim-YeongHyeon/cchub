from cchub import stats
from cchub.ssh import RunResult
from conftest import FakeRemote

PROC = """cpu  100 0 100 700 100 0 0 0 0 0
cpu0 50 0 50 350 50 0 0 0 0 0
MemTotal:       65536000 kB
MemFree:         1000000 kB
MemAvailable:   32768000 kB
"""

PROC_LATER = """cpu  200 0 200 750 150 0 0 0 0 0
MemTotal:       65536000 kB
MemAvailable:   16384000 kB
"""


def test_parse_proc():
    r = stats.parse_proc(PROC)
    assert r.cpu_total == 1000
    assert r.cpu_idle == 800  # idle(700) + iowait(100)
    assert r.mem_total_kb == 65536000
    assert r.mem_avail_kb == 32768000


def test_parse_proc_garbage_returns_none():
    assert stats.parse_proc("no cpu line here") is None
    assert stats.parse_proc("cpu  abc def\nMemTotal: 1 kB\nMemAvailable: 1 kB") is None


def test_read_stats_uses_remote_and_handles_failure():
    ok = FakeRemote({"cat": RunResult(0, PROC, "")})
    assert stats.read_stats(ok).cpu_total == 1000
    assert ok.calls[0] == ["cat", "/proc/stat", "/proc/meminfo"]
    down = FakeRemote({"cat": RunResult(255, "", "unreachable")})
    assert stats.read_stats(down) is None


def test_cpu_percent():
    a, b = stats.parse_proc(PROC), stats.parse_proc(PROC_LATER)
    # dt=300, d_idle=100 → 사용 200/300 ≈ 66.7%
    assert abs(stats.cpu_percent(a, b) - 66.7) < 0.1
    assert stats.cpu_percent(a, a) == 0.0  # dt<=0 방어


def test_sparkline():
    assert stats.sparkline([0.0, 50.0, 100.0]) == "▁▅█"


def test_server_stats_lifecycle():
    s = stats.ServerStats()
    assert "offline" in s.label("srv1")
    s.update(stats.parse_proc(PROC))
    assert s.online and s.history == []       # 첫 샘플은 diff 불가
    s.update(stats.parse_proc(PROC_LATER))
    assert len(s.history) == 1
    lbl = s.label("srv1")
    assert lbl.startswith("srv1 ") and "67%" in lbl and "47G/62G" in lbl
    s.update(None)                            # 단절
    assert not s.online and "offline" in s.label("srv1")


def test_server_stats_history_capped():
    s = stats.ServerStats()
    s.update(stats.parse_proc(PROC))
    cur = stats.parse_proc(PROC)
    for i in range(40):
        cur = stats.RawStats(cur.cpu_total + 100, cur.cpu_idle + 50,
                             cur.mem_total_kb, cur.mem_avail_kb)
        s.update(cur)
    assert len(s.history) == 30


def test_parse_proc_short_or_malformed_lines_return_none():
    assert stats.parse_proc("cpu  1 2 3\nMemTotal: 1 kB\nMemAvailable: 1 kB") is None
    assert stats.parse_proc("cpu  1 2 3 4 5\nMemTotal: abc kB\nMemAvailable: 1 kB") is None
    assert stats.parse_proc("cpu  1 2 3 4 5\nMemTotal:\nMemAvailable: 1 kB") is None
