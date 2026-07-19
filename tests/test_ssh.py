import subprocess
from pathlib import Path

from cchub.ssh import RunResult, SSHRemote


def test_run_builds_quoted_ssh_command(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = SSHRemote("user@host").run(["echo", "hello world", "$HOME"])
    assert r == RunResult(0, "ok", "")
    cmd = captured["cmd"]
    assert cmd[0] == "ssh" and "user@host" in cmd
    # 원격에서 실행될 문자열: 공백/특수문자가 shlex 인용됨
    remote_cmd = cmd[-1]
    assert remote_cmd == "echo 'hello world' '$HOME'"
    assert "BatchMode=yes" in " ".join(cmd)
    assert "ControlMaster=auto" in " ".join(cmd)


def test_run_nonzero_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 15))

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = SSHRemote("h").run(["sleep", "99"], timeout=1)
    assert r.rc != 0 and "timeout" in r.err


def test_mirror_builds_rsync_without_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dst = tmp_path / "cache" / "projects"
    r = SSHRemote("user@host").mirror("~/.claude/projects", dst)
    assert r.rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == "rsync" and "-az" in cmd
    assert "--delete" not in cmd  # 이력 영구 보존
    assert cmd[-2] == "user@host:~/.claude/projects/"
    assert cmd[-1] == str(dst) + "/"
    assert dst.exists()  # 로컬 디렉토리 자동 생성
