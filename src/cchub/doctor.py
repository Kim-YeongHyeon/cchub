from __future__ import annotations

import shlex
from dataclasses import dataclass

from cchub.ssh import Remote


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "fail" | "warn" | "skip"
    detail: str = ""
    hint: str = ""


def _classify_ssh_error(stderr: str) -> str:
    s = stderr.lower()
    if "permission denied" in s:
        return ("키 기반(비밀번호 없는) 로그인이 필요합니다: "
                "ssh-copy-id <host> 로 공개키를 등록하세요")
    if "connection refused" in s or "timed out" in s or "timeout" in s:
        return ("호스트/포트를 확인하세요 — 사내 서버는 22가 아닐 수 있습니다. "
                "~/.ssh/config에 Port를 지정한 Host alias를 만들어 host에 쓰세요")
    if "could not resolve" in s or "name or service not known" in s:
        return ("host 문자열 또는 ~/.ssh/config alias를 확인하세요")
    return "직접 확인: ssh -o BatchMode=yes <host> true"


def _projects_check_script(claude_dir: str) -> str:
    # claude_dir 의 ~ 는 원격 $HOME 으로 확장
    if claude_dir == "~":
        base = '"$HOME"'
    elif claude_dir.startswith("~/"):
        base = '"$HOME"/' + shlex.quote(claude_dir[2:])
    else:
        base = shlex.quote(claude_dir)
    return f'test -d {base}/projects'


def diagnose_server(remote: Remote, name: str, host: str,
                    claude_dir: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    ssh = remote.run(["true"], timeout=8)
    if ssh.rc != 0:
        detail = (ssh.err.strip().splitlines() or [""])[0][:120]
        results.append(CheckResult("ssh 접속", "fail", detail,
                                   _classify_ssh_error(ssh.err)))
        for n in ("원격 rsync", "projects 디렉터리", "tmux 서버"):
            results.append(CheckResult(n, "skip", "ssh 실패로 건너뜀"))
        return results
    results.append(CheckResult("ssh 접속", "ok"))

    rsync = remote.run(["rsync", "--version"], timeout=8)
    if rsync.rc != 0:
        results.append(CheckResult("원격 rsync", "fail",
                                   (rsync.err.strip().splitlines() or [""])[0][:120],
                                   "서버에 rsync를 설치하세요"))
    else:
        results.append(CheckResult("원격 rsync", "ok"))

    proj = remote.run(["sh", "-c", _projects_check_script(claude_dir)], timeout=8)
    if proj.rc != 0:
        results.append(CheckResult("projects 디렉터리", "fail",
                                   f"{claude_dir}/projects 없음",
                                   "이 서버에서 Claude Code 실행 이력이 없거나 "
                                   "claude_dir 설정이 실제 경로와 다릅니다"))
    else:
        results.append(CheckResult("projects 디렉터리", "ok"))

    tmux = remote.run(["tmux", "list-sessions"], timeout=8)
    if tmux.rc != 0:
        err = (tmux.err + tmux.out).lower()
        if "command not found" in err or "not found" in err:
            hint = "tmux 미설치 — 세션 전송/상태 감지에만 필요합니다"
        else:
            hint = "tmux 서버가 떠 있지 않음 — 세션 전송/상태 감지에만 필요합니다"
        results.append(CheckResult("tmux 서버", "warn",
                                   (tmux.err.strip().splitlines() or [""])[0][:120], hint))
    else:
        results.append(CheckResult("tmux 서버", "ok"))

    return results
