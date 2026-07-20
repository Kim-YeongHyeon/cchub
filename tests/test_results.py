from pathlib import Path

from cchub.config import Config, ServerConfig
from cchub.results import collect_results
from cchub.ssh import RunResult
from conftest import FakeRemote


def make_cfg():
    return Config(servers={"srv1": ServerConfig(
        name="srv1", host="u@h", results=["~/exp/**", "~/bench/*.json"])})


def test_collect_results_fetches_each_pattern(tmp_path):
    fake = FakeRemote()
    rep = collect_results(make_cfg(), tmp_path, "srv1", remote_factory=lambda h: fake)
    assert rep.ok and rep.failed == []
    assert [f[0] for f in fake.fetches] == ["~/exp/**", "~/bench/*.json"]
    assert all(f[1] == tmp_path / "results" / "srv1" for f in fake.fetches)


def test_collect_results_isolates_pattern_failure(tmp_path):
    class Flaky(FakeRemote):
        def fetch(self, remote_path, local_dir, timeout=300):
            super().fetch(remote_path, local_dir, timeout)
            if "bench" in remote_path:
                return RunResult(23, "", "rsync 에러")
            return RunResult(0, "", "")

    fake = Flaky()
    rep = collect_results(make_cfg(), tmp_path, "srv1", remote_factory=lambda h: fake)
    assert not rep.ok
    assert rep.failed == ["~/bench/*.json"]
    assert len(fake.fetches) == 2   # 실패해도 다음 패턴 계속


def test_collect_results_no_patterns(tmp_path):
    cfg = Config(servers={"s": ServerConfig(name="s", host="h")})
    rep = collect_results(cfg, tmp_path, "s", remote_factory=lambda h: FakeRemote())
    assert rep.ok and rep.failed == []
