from __future__ import annotations

from dataclasses import dataclass

from cchub.ssh import Remote

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
