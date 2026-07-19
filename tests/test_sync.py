import shutil
from pathlib import Path

from cchub.index import SessionIndex
from cchub.ssh import Remote, RunResult
from cchub.sync import sync_server
from conftest import FakeRemote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


class FailingMirrorRemote(FakeRemote):
    def mirror(self, remote_dir, local_dir, timeout=120):
        return RunResult(255, "", "ssh: connect refused")


def test_sync_server_mirrors_then_indexes(tmp_path):
    fake = FakeRemote()
    idx = SessionIndex(tmp_path / "i.db")
    # mirror가 갖다놓았을 파일을 미리 배치 (FakeRemote.mirror는 기록만 함)
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")

    rep = sync_server(fake, "srv1", "~/.claude", tmp_path / "cache", idx)
    assert rep.ok
    assert rep.files == 1 and rep.events == 3
    assert fake.mirrors == [("~/.claude/projects", tmp_path / "cache" / "srv1" / "projects")]
    assert idx.get_session("srv1", "s-1") is not None
    # 두 번째 sync는 변화 없음
    rep2 = sync_server(fake, "srv1", "~/.claude", tmp_path / "cache", idx)
    assert rep2.ok and rep2.files == 0 and rep2.events == 0


def test_sync_server_reports_mirror_failure(tmp_path):
    idx = SessionIndex(tmp_path / "i.db")
    rep = sync_server(FailingMirrorRemote(), "srv1", "~/.claude", tmp_path / "cache", idx)
    assert not rep.ok
    assert "refused" in rep.error
