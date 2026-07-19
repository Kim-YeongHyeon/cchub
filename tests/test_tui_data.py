import shutil
from pathlib import Path

from cchub.config import Config, ServerConfig
from cchub.index import SessionIndex
from cchub.ssh import RunResult
from cchub.tui.data import collect_sessions
from conftest import FakeRemote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
PANES = "%5\tmain:0.0\t/home/u/proj\tclaude\n"


def make_env(tmp_path):
    cfg = Config(servers={"srv1": ServerConfig(name="srv1", host="u@h")})
    idx = SessionIndex(tmp_path / "i.db")
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    return cfg, idx


def test_collect_sessions_returns_snapshot(tmp_path):
    cfg, idx = make_env(tmp_path)
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    snaps = collect_sessions(cfg, tmp_path, idx, remote_factory=lambda h: fake)
    snap = snaps["srv1"]
    assert snap.error == ""
    assert len(snap.sessions) == 1
    assert snap.sessions[0].session_id == "s-1"
    assert snap.sessions[0].title == "NUMA 실험"  # sync가 인덱싱까지 수행했음


def test_collect_sessions_isolates_server_failure(tmp_path):
    cfg, idx = make_env(tmp_path)
    cfg.servers["down"] = ServerConfig(name="down", host="u@dead")

    class DownRemote(FakeRemote):
        def mirror(self, remote_dir, local_dir, timeout=120):
            return RunResult(255, "", "connect refused")

        def run(self, argv, timeout=15):
            return RunResult(255, "", "connect refused")

    up = FakeRemote({"tmux": RunResult(0, PANES, "")})
    factory = lambda h: DownRemote() if "dead" in h else up
    snaps = collect_sessions(cfg, tmp_path, idx, remote_factory=factory)
    assert snaps["srv1"].sessions and snaps["srv1"].error == ""
    assert "refused" in snaps["down"].error
    assert snaps["down"].sessions == []  # tmux도 죽었으니 빈 목록, 예외 없음
