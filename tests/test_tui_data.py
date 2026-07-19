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


def test_collect_sessions_survives_raising_server(tmp_path):
    cfg, idx = make_env(tmp_path)
    cfg.servers["boom"] = ServerConfig(name="boom", host="u@boom")

    class RaisingRemote(FakeRemote):
        def mirror(self, remote_dir, local_dir, timeout=120):
            raise OSError("rsync 바이너리 없음")

    up = FakeRemote({"tmux": RunResult(0, PANES, "")})
    factory = lambda h: RaisingRemote() if "boom" in h else up
    snaps = collect_sessions(cfg, tmp_path, idx, remote_factory=factory)
    assert snaps["srv1"].sessions and snaps["srv1"].error == ""  # 다른 서버는 무사
    assert "rsync" in snaps["boom"].error
    assert snaps["boom"].sessions == []


def test_collect_sessions_passes_partial_failure_error(tmp_path):
    cfg, idx = make_env(tmp_path)
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    import cchub.tui.data as data_mod
    from cchub.sync import SyncReport
    real = data_mod.sync_server
    data_mod_sync = lambda *a, **k: SyncReport(server="srv1", ok=True, files=1, events=3,
                                               error="index 실패 1건: bad.jsonl")
    try:
        data_mod.sync_server = data_mod_sync
        snaps = collect_sessions(cfg, tmp_path, idx, remote_factory=lambda h: fake)
    finally:
        data_mod.sync_server = real
    assert "bad.jsonl" in snaps["srv1"].error   # ok=True여도 error 전달
    assert snaps["srv1"].sessions               # discover는 정상 수행
