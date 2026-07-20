import os
import time
from pathlib import Path

from cchub import sessions
from cchub.index import SessionIndex
from cchub.ssh import RunResult
from conftest import FakeRemote

PANES = (
    "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"   # claude pane
    "%1\tmain:1.0\t/home/u/other\tnode\t200\n"    # claude는 node로 보일 수 있음
    "%2\tmain:2.0\t/home/u/x\tbash\t300\n"        # claude 아님 → 제외
)

SAME_CWD_PANES = (
    "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"   # 먼저 만든 pane (starttime 1000)
    "%6\tmain:1.0\t/home/u/proj\tclaude\t200\n"   # 나중 pane (starttime 2000)
)


def setup_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache" / "srv1"
    d = cache / "projects" / "-home-u-proj"
    d.mkdir(parents=True)
    old = d / "old-session.jsonl"
    old.write_text("{}\n")
    os.utime(old, (time.time() - 9999, time.time() - 9999))
    new = d / "new-session.jsonl"
    new.write_text("{}\n")  # 방금 생성 → mtime 최신 → working
    return cache


def test_encode_project():
    assert sessions.encode_project("/home/u/my-proj") == "-home-u-my-proj"


def test_discover_filters_numbers_and_matches(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    cache = setup_cache(tmp_path)
    live = sessions.discover(fake, "srv1", cache)
    assert len(live) == 2  # bash pane 제외
    assert [s.number for s in live] == [1, 2]
    first = live[0]
    assert first.pane_id == "%5"
    assert first.session_id == "new-session"  # 최신 mtime 파일이 매칭
    assert first.state == "working"           # mtime이 최근 30초 이내


def test_discover_without_cache_is_unknown(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    live = sessions.discover(fake, "srv1", tmp_path / "empty")
    assert live[0].session_id == ""
    assert live[0].state == "unknown"


def test_discover_waiting_state_from_index(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    cache = setup_cache(tmp_path)
    new = cache / "projects" / "-home-u-proj" / "new-session.jsonl"
    os.utime(new, (time.time() - 300, time.time() - 300))  # 5분 전 → not working
    idx = SessionIndex(tmp_path / "i.db")
    new.write_text(
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"text","text":"끝났습니다"}]},'
        '"timestamp":"2026-07-19T00:00:00.000Z","sessionId":"new-session"}\n'
    )
    os.utime(new, (time.time() - 300, time.time() - 300))
    idx.index_file("srv1", "-home-u-proj", new)
    live = sessions.discover(fake, "srv1", cache, idx)
    assert live[0].state == "waiting"  # 마지막 메시지가 assistant → 입력 대기


def test_mtime_of_missing_file_is_zero(tmp_path):
    assert sessions._mtime(tmp_path / "ghost.jsonl") == 0.0


def test_discover_survives_vanishing_file(tmp_path, monkeypatch):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    cache = setup_cache(tmp_path)
    real_stat = Path.stat
    target = cache / "projects" / "-home-u-proj" / "new-session.jsonl"

    def flaky_stat(self, **kw):
        if self == target:
            raise FileNotFoundError(self)
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    live = sessions.discover(fake, "srv1", cache)  # 예외 없이 동작해야 함
    assert len(live) == 2


def make_two_sessions(tmp_path):
    """같은 프로젝트에 세션 2개: old-session(first_ts 이름), new-session."""
    from cchub.index import SessionIndex
    cache = tmp_path / "cache" / "srv1"
    d = cache / "projects" / "-home-u-proj"
    d.mkdir(parents=True)
    idx = SessionIndex(tmp_path / "i.db")
    for name, ts in [("old-session", "2026-07-01T09:00:00.000Z"),
                     ("new-session", "2026-07-02T09:00:00.000Z")]:
        f = d / f"{name}.jsonl"
        f.write_text(
            '{"type":"user","message":{"role":"user","content":"시작"},'
            f'"sessionId":"{name}","timestamp":"{ts}"}}' + '\n'
        )
        idx.index_file("srv1", "-home-u-proj", f)
    return cache, idx


def test_same_cwd_panes_get_distinct_sessions(tmp_path):
    fake = FakeRemote({
        "tmux": RunResult(0, SAME_CWD_PANES, ""),
        "sh": RunResult(0, "100 1000\n200 2000\n", ""),  # pid starttime
    })
    cache, idx = make_two_sessions(tmp_path)
    live = sessions.discover(fake, "srv1", cache, idx)
    by_pane = {s.pane_id: s.session_id for s in live}
    assert by_pane["%5"] == "old-session"   # 먼저 만든 pane ↔ 먼저 시작한 세션
    assert by_pane["%6"] == "new-session"
    assert len(set(by_pane.values())) == 2  # 중복 배정 없음


def test_same_cwd_falls_back_when_starttime_unavailable(tmp_path):
    fake = FakeRemote({
        "tmux": RunResult(0, SAME_CWD_PANES, ""),
        "sh": RunResult(255, "", "권한 없음"),
    })
    cache, idx = make_two_sessions(tmp_path)
    live = sessions.discover(fake, "srv1", cache, idx)
    # 폴백: 기존 휴리스틱(모두 최신 세션) — 크래시 없이 동작하는 것이 핵심
    assert all(s.session_id for s in live)
