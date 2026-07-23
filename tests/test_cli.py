import shutil
from pathlib import Path

import pytest

from cchub import cli
from cchub.config import Config, ServerConfig
from cchub.ssh import RunResult
from cchub.tmux import SpawnResult
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


def test_skills_deploy_unknown_server_blocks_all(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "deploy", "my-skill", "srv1", "srv-오타"]) == 1
    assert fake.pushes == []             # 부분 배포 없음
    assert "srv-오타" in capsys.readouterr().err


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


def test_cmd_doctor_all_ok(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="sudal")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[srv1]" in out and "ssh 접속" in out and "✓" in out


def test_cmd_doctor_fail_exit_1(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="cigar")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote",
                        lambda host: FakeRemote({"true": RunResult(255, "", "Connection refused")}))
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗" in out and "포트" in out


def test_cmd_doctor_warn_only_exit_0(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="sudal")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote",
                        lambda host: FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running")}))
    rc = cli.cmd_doctor(None)
    assert rc == 0
    assert "⚠" in capsys.readouterr().out


def test_cmd_doctor_no_servers(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config
    cfg = Config(sync_interval=30, stats_interval=2, servers={})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.cmd_doctor(None)
    assert rc == 1
    assert "servers" in capsys.readouterr().err


def test_cmd_sync_failure_suggests_doctor(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.sync import SyncReport
    monkeypatch.setattr(cli, "_ctx", lambda: (object(), tmp_path, object()))
    monkeypatch.setattr(cli, "_sync_all",
                        lambda cfg, root, index: [SyncReport(server="srv1", ok=False, error="boom")])
    rc = cli.cmd_sync(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "cchub doctor" in err


def test_cmd_sync_success_no_doctor_hint(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.sync import SyncReport
    monkeypatch.setattr(cli, "_ctx", lambda: (object(), tmp_path, object()))
    monkeypatch.setattr(cli, "_sync_all",
                        lambda cfg, root, index: [SyncReport(server="srv1", ok=True, files=1, events=2)])
    rc = cli.cmd_sync(None)
    err = capsys.readouterr().err
    assert rc == 0
    assert "cchub doctor" not in err


def _spawn_cfg():
    return Config(sync_interval=30, stats_interval=2,
                  servers={"srv1": ServerConfig(name="srv1", host="sudal")})


def test_cmd_spawn_default_flags(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    captured = {}

    def fake_spawn(remote, cwd, launch_cmd, name=None, prompt=None):
        captured.update(cwd=cwd, launch=launch_cmd, name=name, prompt=prompt)
        return SpawnResult(ok=True, name="cchub-1")

    monkeypatch.setattr(cli.tmux, "spawn_session", fake_spawn)
    rc = cli.main(["spawn", "srv1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured == {"cwd": "~", "launch": "claude --dangerously-skip-permissions",
                        "name": None, "prompt": None}
    assert "cchub-1" in out and "tmux attach -t cchub-1" in out


def test_cmd_spawn_safe_name_prompt(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    captured = {}

    def fake_spawn(remote, cwd, launch_cmd, name=None, prompt=None):
        captured.update(cwd=cwd, launch=launch_cmd, name=name, prompt=prompt)
        return SpawnResult(ok=True, name=name, prompt_sent=True)

    monkeypatch.setattr(cli.tmux, "spawn_session", fake_spawn)
    rc = cli.main(["spawn", "srv1", "~/proj", "--safe", "--name", "exp1",
                   "--prompt", "테스트 돌려줘"])
    assert rc == 0
    assert captured == {"cwd": "~/proj", "launch": "claude",
                        "name": "exp1", "prompt": "테스트 돌려줘"}


def test_cmd_spawn_prompt_not_delivered_warns(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    monkeypatch.setattr(cli.tmux, "spawn_session",
                        lambda *a, **k: SpawnResult(ok=True, name="cchub-1",
                                                    prompt_sent=False))
    rc = cli.main(["spawn", "srv1", "--prompt", "x"])
    err = capsys.readouterr().err
    assert rc == 0                       # 세션 생성이 성공 기준
    assert "미전달" in err


def test_cmd_spawn_create_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    monkeypatch.setattr(cli.tmux, "spawn_session",
                        lambda *a, **k: SpawnResult(ok=False, name="cchub-1",
                                                    error="boom"))
    rc = cli.main(["spawn", "srv1"])
    err = capsys.readouterr().err
    assert rc == 1 and "boom" in err


def test_cmd_spawn_rejects_bad_or_taken_name(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "exp1\n", "")})
    monkeypatch.setattr(cli, "_make_remote", lambda host: fake)
    assert cli.main(["spawn", "srv1", "--name", "bad name!"]) == 1
    assert "세션명" in capsys.readouterr().err
    assert cli.main(["spawn", "srv1", "--name", "exp1"]) == 1
    assert "이미 존재" in capsys.readouterr().err


def test_cmd_spawn_unknown_server(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    rc = cli.main(["spawn", "srv9"])
    assert rc == 1
    assert "알 수 없는 서버" in capsys.readouterr().err
