import subprocess
import tempfile
from pathlib import Path

from cchub.ssh import RunResult, SSHRemote, render_remote_path


def _control_path(remote: SSHRemote) -> str:
    for opt in remote._opts:
        if opt.startswith("ControlPath="):
            return opt[len("ControlPath="):]
    raise AssertionError("ControlPath 옵션을 찾지 못함")


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


def test_control_path_stays_under_unix_socket_limit_for_long_cchub_dir(monkeypatch, tmp_path):
    # AF_UNIX 소켓 경로는 보통 108바이트 제한 — CCHUB_DIR이 깊은 경로(예: 스크래치패드)에
    # 있으면 ControlPath={cm}/%r@%h-%p 가 이 한도를 넘어 ssh/rsync가
    # "ControlPath too long"으로 실패한다.
    long_dir = tmp_path / ("nested_dir_segment_" * 6)
    monkeypatch.setenv("CCHUB_DIR", str(long_dir))
    remote = SSHRemote("user@some-very-descriptive-hostname.example.internal")
    control_path = _control_path(remote)
    assert len(control_path.encode()) < 100


def test_control_path_fits_macos_budget(monkeypatch, tmp_path):
    # macOS 실측(sync 실패 재현): sun_path 한도는 104바이트(Linux 108),
    # TMPDIR은 /var/folders/XX/<30자>/T 로 48바이트, 그리고 ssh unix_listener는
    # 소켓 생성 시 ".{랜덤16자}" 임시 이름(+17바이트)을 먼저 쓴다.
    # → cchub가 통제하는 부분(/{디렉터리명}/{소켓명})의 예산:
    #   104 - 1(NUL) - 17(ssh 임시 서픽스) - 48(macOS TMPDIR) = 38바이트
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    remote = SSHRemote("user@some-very-descriptive-hostname.example.internal")
    control_path = _control_path(remote)
    tmpdir = tempfile.gettempdir()
    assert control_path.startswith(tmpdir + "/")
    ours = len(control_path.encode()) - len(tmpdir.encode())
    assert ours <= 38, f"tempdir 이후 {ours}바이트 — macOS에서 ControlPath too long"


def test_fetch_builds_rsync(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "results" / "srv1"
    r = SSHRemote("u@h").fetch("~/exp/*.json", dest)
    assert r.rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == "rsync" and "--delete" not in cmd
    assert cmd[-2] == "u@h:~/exp/*.json"      # 원격 셸이 glob 확장
    assert cmd[-1] == str(dest) + "/"
    assert dest.exists()


def test_push_dir_sends_contents(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = tmp_path / "payload"
    d.mkdir()
    SSHRemote("u@h").push(d, "~/inbox")
    cmd = captured["cmd"]
    assert cmd[-2] == str(d) + "/"            # 디렉토리 → 내용물 전송
    assert cmd[-1] == "u@h:~/inbox/"
    f = tmp_path / "one.txt"
    f.write_text("x")
    SSHRemote("u@h").push(f, "~/inbox")
    assert captured["cmd"][-2] == str(f)      # 파일 → 그대로


def test_render_remote_path_variants():
    assert render_remote_path("~") == '"$HOME"'
    assert render_remote_path("~/proj") == '"$HOME"/proj'
    assert render_remote_path("~/") == '"$HOME"'
    assert render_remote_path("/abs/path") == "/abs/path"
    assert render_remote_path("~/my dir") == '"$HOME"/' + "'my dir'"
