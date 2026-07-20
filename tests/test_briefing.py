from datetime import datetime
from pathlib import Path

from cchub.briefing import generate_briefing
from cchub.config import Config, ServerConfig
from cchub.index import SessionIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def make_env(tmp_path):
    cfg = Config(servers={"srv1": ServerConfig(name="srv1", host="u@h")})
    idx = SessionIndex(tmp_path / "i.db")
    import shutil
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    idx.index_file("srv1", "-home-u-proj", proj / "s-1.jsonl")
    rdir = tmp_path / "results" / "srv1"
    rdir.mkdir(parents=True)
    (rdir / "bench.json").write_text("{}")
    return cfg, idx


def test_generate_briefing_writes_file_and_prompt(tmp_path):
    cfg, idx = make_env(tmp_path)
    now = datetime(2026, 7, 20, 14, 30)
    path, prompt = generate_briefing(cfg, tmp_path, idx, now=now)
    assert path.name == "briefing-20260720-1430.md"
    body = path.read_text()
    assert "srv1" in body
    assert "bench.json" in body            # 수집 파일 목록
    assert "NUMA 실험" in body              # 세션 제목
    assert str(path) in prompt              # 프롬프트가 파일을 가리킴
    assert "리포트" in prompt


def test_generate_briefing_without_results(tmp_path):
    cfg = Config(servers={"s": ServerConfig(name="s", host="h")})
    idx = SessionIndex(tmp_path / "i.db")
    path, _ = generate_briefing(cfg, tmp_path, idx,
                                now=datetime(2026, 7, 20, 0, 0))
    assert "수집된 결과 없음" in path.read_text()
