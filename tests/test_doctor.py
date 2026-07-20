from cchub.doctor import CheckResult, diagnose_server, _classify_ssh_error
from cchub.ssh import RunResult
from conftest import FakeRemote


def test_classify_ssh_error_permission_denied():
    hint = _classify_ssh_error("Permission denied (publickey,password).")
    assert "ssh-copy-id" in hint


def test_classify_ssh_error_connection_refused():
    hint = _classify_ssh_error("ssh: connect to host x port 22: Connection refused")
    assert "포트" in hint


def test_classify_ssh_error_timed_out():
    hint = _classify_ssh_error("ssh: connect to host x port 22: Operation timed out")
    assert "포트" in hint


def test_classify_ssh_error_resolve():
    hint = _classify_ssh_error("ssh: Could not resolve hostname foo: nodename nor servname")
    assert "host" in hint or "alias" in hint


def test_classify_ssh_error_generic():
    hint = _classify_ssh_error("some unexpected error")
    assert "BatchMode" in hint


def _names_status(results):
    return {r.name: r.status for r in results}


def test_diagnose_all_ok():
    remote = FakeRemote()  # 모든 run rc=0
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "ok"
    assert st["원격 rsync"] == "ok"
    assert st["projects 디렉터리"] == "ok"
    assert st["tmux 서버"] == "ok"


def test_diagnose_ssh_fail_skips_rest():
    remote = FakeRemote({"true": RunResult(255, "", "Connection refused")})
    results = diagnose_server(remote, "srv1", "cigar", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "fail"
    assert st["원격 rsync"] == "skip"
    assert st["projects 디렉터리"] == "skip"
    assert st["tmux 서버"] == "skip"
    ssh = next(r for r in results if r.name == "ssh 접속")
    assert "포트" in ssh.hint  # Connection refused → 포트 힌트


def test_diagnose_rsync_missing():
    remote = FakeRemote({("rsync", "--version"): RunResult(127, "", "command not found")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "ok"
    assert st["원격 rsync"] == "fail"


def test_diagnose_projects_missing():
    # sh -c 로 test -d 를 실행 → rc=1 이면 projects 없음
    remote = FakeRemote({"sh": RunResult(1, "", "")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["projects 디렉터리"] == "fail"


def test_diagnose_tmux_missing_is_warn():
    remote = FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running on ...")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    tmux = next(r for r in results if r.name == "tmux 서버")
    assert tmux.status == "warn"
    assert "전송" in tmux.hint or "감지" in tmux.hint
