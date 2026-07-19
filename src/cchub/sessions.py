from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cchub import tmux
from cchub.index import SessionIndex
from cchub.ssh import Remote

# claude는 실행 방식에 따라 pane_current_command가 claude 또는 node로 보인다
_CLAUDE_COMMANDS = {"claude", "node"}
_WORKING_WINDOW_SECS = 30


def _mtime(path: Path) -> float:
    """파일의 수정 시간을 반환. 파일이 없으면 0.0 반환 (race condition 대응)."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def encode_project(cwd: str) -> str:
    """Claude Code가 cwd를 ~/.claude/projects/ 디렉토리명으로 바꾸는 규칙."""
    return cwd.replace("/", "-")


@dataclass
class LiveSession:
    server: str
    number: int      # 사용자에게 보여주는 서버 내 번호 (1부터)
    pane_id: str
    location: str
    cwd: str
    project: str
    session_id: str  # 매칭 실패 시 ""
    title: str
    state: str       # "working" | "waiting" | "idle" | "unknown"


def discover(
    remote: Remote,
    server: str,
    cache_dir: Path,
    index: SessionIndex | None = None,
) -> list[LiveSession]:
    """서버 tmux에서 claude로 보이는 pane을 찾아 미러된 세션과 매칭하고 번호를 매긴다."""
    panes = [p for p in tmux.list_panes(remote) if p.command in _CLAUDE_COMMANDS]
    panes.sort(key=lambda p: p.location)
    now = time.time()
    out: list[LiveSession] = []
    for i, p in enumerate(panes, start=1):
        project = encode_project(p.cwd)
        proj_dir = cache_dir / "projects" / project
        session_id, title, state = "", "", "unknown"
        jsonls = (
            sorted(proj_dir.glob("*.jsonl"), key=lambda f: _mtime(f), reverse=True)
            if proj_dir.is_dir() else []
        )
        if jsonls:
            newest = jsonls[0]
            session_id = newest.stem
            if now - _mtime(newest) < _WORKING_WINDOW_SECS:
                state = "working"
            else:
                state = "idle"
            if index is not None:
                row = index.get_session(server, session_id)
                if row:
                    title = row.title
                    if state != "working" and row.last_role == "assistant":
                        state = "waiting"
        out.append(LiveSession(
            server=server, number=i, pane_id=p.pane_id, location=p.location,
            cwd=p.cwd, project=project, session_id=session_id,
            title=title, state=state,
        ))
    return out
