import shutil
from pathlib import Path

import pytest

from cchub import cli
from cchub.ssh import RunResult
from conftest import FakeRemote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
PANES = "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """CCHUB_DIR 격리 + config + 미러된 캐시 + FakeRemote 주입."""
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('[servers.srv1]\nhost = "u@h"\nresults = ["~/exp/*"]\n[servers.srv2]\nhost = "u@h2"\n')
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    monkeypatch.setattr(cli, "_make_remote", lambda host: fake)
    return tmp_path, fake


def test_init_creates_template(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path / "fresh"))
    assert cli.main(["init"]) == 0
    assert (tmp_path / "fresh" / "config.toml").exists()
    assert cli.main(["init"]) == 1  # 이미 있으면 거부


def test_sync_reports(env, capsys):
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "srv1" in out and "ok" in out


def test_list_shows_live_sessions(env, capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "srv1" in out and "1" in out and "-home-u-proj" in out


def test_send_resolves_number_and_sends(env, capsys):
    _, fake = env
    assert cli.main(["send", "srv1", "1", "실험 시작해줘"]) == 0
    sent = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    assert sent[0] == ["tmux", "send-keys", "-t", "%5", "-l", "--", "실험 시작해줘"]
    assert sent[1][-1] == "Enter"


def test_send_confirms_delivery(env, capsys):
    tmp, fake = env
    fake.responses[("tmux", "capture-pane")] = RunResult(0, "실험 시작해줘\n", "")
    assert cli.main(["send", "srv1", "1", "실험 시작해줘"]) == 0
    out = capsys.readouterr().out
    assert "전송됨" in out


def test_send_bad_number_fails(env, capsys):
    assert cli.main(["send", "srv1", "9", "x"]) == 1
    assert "세션 9" in capsys.readouterr().err


def test_tail_from_index(env, capsys):
    cli.main(["sync"])
    assert cli.main(["tail", "srv1", "1", "-n", "2"]) == 0
    out = capsys.readouterr().out
    assert "결과 요약해줘" in out


def test_search(env, capsys):
    cli.main(["sync"])
    assert cli.main(["search", "NUMA"]) == 0
    assert "s-1" in capsys.readouterr().out


def test_reindex(env, capsys):
    cli.main(["sync"])
    assert cli.main(["reindex"]) == 0
    assert "3" in capsys.readouterr().out  # 재인덱싱된 이벤트 수


def test_corrupt_index_db_is_handled(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('[servers.srv1]\nhost = "u@h"\n')
    (tmp_path / "index.db").write_bytes(b"this is not sqlite")
    rc = cli.main(["search", "x"])
    assert rc == 1
    assert "오류" in capsys.readouterr().err  # traceback이 아니라 한 줄 메시지


def test_results_command(env, capsys):
    tmp, fake = env
    assert cli.main(["results", "srv1"]) == 0
    assert "srv1" in capsys.readouterr().out


def test_brief_command(env, capsys):
    cli.main(["sync"])
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "briefing-" in out and "붙여넣" in out


def test_push_local_to_remote(env, capsys):
    tmp, fake = env
    f = tmp / "data.json"
    f.write_text("{}")
    assert cli.main(["push", str(f), "srv1:~/inbox"]) == 0
    assert fake.pushes and fake.pushes[0][1] == "~/inbox"


def test_push_remote_to_local(env, capsys, tmp_path):
    tmp, fake = env
    dest = tmp_path / "here"
    assert cli.main(["push", "srv1:~/exp/out.json", str(dest)]) == 0
    assert fake.fetches and fake.fetches[0][0] == "~/exp/out.json"


def test_push_remote_to_remote_relays_via_local(env, capsys):
    tmp, fake = env
    # env fixture에 두 번째 서버 srv2 추가됨 (config.toml에 [servers.srv2] host="u@h2")
    assert cli.main(["push", "srv1:~/exp/out.json", "srv2:~/inbox"]) == 0
    assert fake.fetches[0][0] == "~/exp/out.json"
    assert fake.pushes[0][1] == "~/inbox"
    # relay 경유 경로가 CCHUB_DIR/relay 아래
    assert str(tmp / "relay") in str(fake.fetches[0][1])


def test_push_both_local_is_error(env, capsys):
    assert cli.main(["push", "/a", "/b"]) == 1
    assert "서버" in capsys.readouterr().err


def test_push_remote_to_remote_cleans_relay_dir(env, capsys):
    tmp, fake = env
    assert cli.main(["push", "srv1:~/exp/out.json", "srv2:~/inbox"]) == 0
    relay = tmp / "relay"
    assert relay.exists()
    assert list(relay.iterdir()) == []   # 임시 디렉토리가 정리됨


def test_push_unknown_server_prefix_is_error(env, capsys):
    assert cli.main(["push", "srv3:~/x", "srv1:~/y"]) == 1
    err = capsys.readouterr().err
    assert "알 수 없는 서버" in err and "srv3" in err


SKILL_SCAN_OUT = (
    "personal\t/home/u/.claude/skills/my-skill/SKILL.md\t개인 스킬\n"
    "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E\n"
)


@pytest.fixture
def skills_env(env, tmp_path, monkeypatch):
    tmp, fake = env
    fake.responses["sh"] = RunResult(0, SKILL_SCAN_OUT, "")
    lib = tmp_path / "lib"
    monkeypatch.setattr(cli, "_local_lib", lambda: lib)
    from pathlib import Path as P
    (lib / "my-skill").mkdir(parents=True)
    (lib / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 로컬 스킬\n---\n")
    return tmp, fake, lib


def test_skills_list(skills_env, capsys):
    assert cli.main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "my-skill" in out and "e2e-run" in out and "local" in out


def test_skills_pull_personal_first(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "pull", "srv1", "my-skill", "--force"]) == 0
    assert fake.fetches[-1][0] == "/home/u/.claude/skills/my-skill"


def test_skills_pull_existing_needs_force(skills_env, capsys):
    assert cli.main(["skills", "pull", "srv1", "my-skill"]) == 1
    assert "--force" in capsys.readouterr().err


def test_skills_pull_unknown_skill(skills_env, capsys):
    assert cli.main(["skills", "pull", "srv1", "ghost"]) == 1
    assert "ghost" in capsys.readouterr().err


def test_skills_deploy(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "deploy", "my-skill", "srv1", "srv2"]) == 0
    assert len(fake.pushes) == 2
    assert fake.pushes[0] == (lib / "my-skill", "~/.claude/skills/my-skill")


def test_skills_copy_relays_and_cleans(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "copy", "srv1", "my-skill", "srv2"]) == 0
    assert fake.fetches and fake.pushes
    relay = tmp / "relay"
    assert relay.exists() and list(relay.iterdir()) == []


def test_skills_delete_confirms_name(skills_env, capsys, monkeypatch):
    tmp, fake, lib = skills_env
    monkeypatch.setattr("builtins.input", lambda prompt="": "my-skill")
    assert cli.main(["skills", "delete", "srv1", "my-skill"]) == 0
    assert ["rm", "-rf", ".claude/skills/my-skill"] in fake.calls


def test_skills_delete_mismatch_aborts(skills_env, capsys, monkeypatch):
    tmp, fake, lib = skills_env
    monkeypatch.setattr("builtins.input", lambda prompt="": "다른이름")
    assert cli.main(["skills", "delete", "srv1", "my-skill"]) == 1
    assert not any(c[:2] == ["rm", "-rf"] for c in fake.calls)


def test_skills_delete_yes_skips_prompt(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "delete", "srv1", "my-skill", "--yes"]) == 0
    assert ["rm", "-rf", ".claude/skills/my-skill"] in fake.calls
