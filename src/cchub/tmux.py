from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from typing import Callable

from cchub.ssh import Remote, render_remote_path

_FMT = (
    "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}"
    "\t#{pane_current_path}\t#{pane_current_command}\t#{pane_pid}"
)

# claude는 실행 방식에 따라 claude 또는 node로 보인다 (sessions와 공유)
CLAUDE_COMMANDS = {"claude", "node"}


@dataclass
class Pane:
    pane_id: str    # 예: %0 (tmux 고유 ID, 순서 바뀌어도 불변)
    location: str   # 예: main:1.0 (사람이 읽는 위치)
    cwd: str
    command: str
    pid: str        # pane 최상위 프로세스 pid


def list_panes(remote: Remote) -> list[Pane]:
    r = remote.run(["tmux", "list-panes", "-a", "-F", _FMT])
    if r.rc != 0:
        return []
    panes = []
    for line in r.out.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            panes.append(Pane(*parts))
    return panes


def send_prompt(remote: Remote, pane_id: str, text: str) -> bool:
    # -l(literal): 키 이름 해석 금지. --: '-'로 시작하는 텍스트의 플래그 오인 방지.
    if remote.run(["tmux", "send-keys", "-t", pane_id, "-l", "--", text]).rc != 0:
        return False
    return remote.run(["tmux", "send-keys", "-t", pane_id, "Enter"]).rc == 0


def capture(remote: Remote, pane_id: str, lines: int = 100) -> str:
    r = remote.run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"])
    return r.out if r.rc == 0 else ""


def verify_pane(remote: Remote, pane_id: str) -> bool:
    """전송 직전: pane이 존재하고 claude 계열 프로세스인지 확인."""
    return any(
        p.pane_id == pane_id and p.command in CLAUDE_COMMANDS
        for p in list_panes(remote)
    )


def confirm_delivery(remote: Remote, pane_id: str, text: str) -> bool:
    """전송 후 1회: 텍스트 앞부분이 화면에 보이는지 (best-effort, 래핑 시 미검출 가능)."""
    probe = text.strip()[:20]
    if not probe:
        return True
    return probe in capture(remote, pane_id, lines=50)


@dataclass
class SpawnResult:
    ok: bool                         # tmux 세션 생성 성공 여부 (성공 기준)
    name: str = ""                   # 실제 세션명
    prompt_sent: bool | None = None  # None=프롬프트 미요청, True/False=전달 여부
    error: str = ""


def list_session_names(remote: Remote) -> list[str]:
    """tmux 세션명 목록. 서버 미기동/미설치 시 []."""
    r = remote.run(["tmux", "list-sessions", "-F", "#{session_name}"])
    if r.rc != 0:
        return []
    return [ln for ln in r.out.splitlines() if ln]


def spawn_session(
    remote: Remote,
    cwd: str,
    launch_cmd: str,
    name: str | None = None,
    prompt: str | None = None,
    poll_attempts: int = 20,
    poll_interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """detached tmux 세션 생성 + launch_cmd 기동 (+ 선택적 초기 프롬프트).

    성공 기준은 세션 생성. prompt는 pane command가 claude 계열이 될 때까지
    폴링한 뒤 전송하며, 타임아웃이어도 세션은 성공으로 본다 (prompt_sent=False).
    """
    if name is None:
        existing = set(list_session_names(remote))
        n = 1
        while f"cchub-{n}" in existing:
            n += 1
        name = f"cchub-{n}"
    r = remote.run([
        "sh", "-c",
        f"tmux new-session -d -s {shlex.quote(name)} -c {render_remote_path(cwd)}",
    ])
    if r.rc != 0:
        return SpawnResult(ok=False, name=name, error=r.err.strip())
    if (remote.run(["tmux", "send-keys", "-t", name, "-l", "--", launch_cmd]).rc != 0
            or remote.run(["tmux", "send-keys", "-t", name, "Enter"]).rc != 0):
        return SpawnResult(ok=True, name=name,
                           prompt_sent=False if prompt is not None else None,
                           error="claude 실행 명령 주입 실패")
    if prompt is None:
        return SpawnResult(ok=True, name=name)
    for _ in range(poll_attempts):
        pane = next(
            (p for p in list_panes(remote)
             if p.location.startswith(f"{name}:") and p.command in CLAUDE_COMMANDS),
            None,
        )
        if pane is not None:
            return SpawnResult(ok=True, name=name,
                               prompt_sent=send_prompt(remote, pane.pane_id, prompt))
        sleep(poll_interval)
    return SpawnResult(ok=True, name=name, prompt_sent=False)
