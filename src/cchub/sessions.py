from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cchub import tmux
from cchub.index import SessionIndex
from cchub.ssh import Remote
from cchub.tmux import CLAUDE_COMMANDS

_WORKING_WINDOW_SECS = 30

# /proc/<pid>/stat: comm에 공백이 올 수 있어 ')' 뒤를 잘라 22번째(잘린 후 20번째) 필드를 읽는다
_STARTTIME_SCRIPT = (
    'for p in {pids}; do s=$(cat /proc/$p/stat 2>/dev/null) || continue; '
    's=${{s##*) }}; set -- $s; printf "%s %s\\n" "$p" "${{20:-0}}"; done'
)


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


def _pane_start_times(remote: Remote, pids: list[str]) -> dict[str, int]:
    clean = [p for p in pids if p.isdigit()]
    if not clean:
        return {}
    r = remote.run(["sh", "-c", _STARTTIME_SCRIPT.format(pids=" ".join(clean))])
    if r.rc != 0:
        return {}
    out: dict[str, int] = {}
    for line in r.out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _pair_same_cwd(
    remote: Remote,
    server: str,
    group: list,          # 같은 프로젝트의 Pane들
    jsonls: list[Path],   # mtime 내림차순
    index: SessionIndex | None,
) -> dict[str, Path]:
    """pane 생성순 ↔ 세션 시작순 페어링. starttime 조회 실패 시 기존 휴리스틱."""
    starts = _pane_start_times(remote, [p.pid for p in group])
    if len(starts) < len(group):
        return {p.pane_id: jsonls[0] for p in group}
    panes_sorted = sorted(group, key=lambda p: starts.get(p.pid, 0))
    candidates = jsonls[: len(group)]  # 최신 N개가 활성 세션 후보

    def first_ts(f: Path) -> str:
        row = index.get_session(server, f.stem) if index else None
        return row.first_ts if row and row.first_ts else "9999"

    cands_sorted = sorted(candidates, key=first_ts)
    out = {p.pane_id: f for p, f in zip(panes_sorted, cands_sorted)}
    for p in panes_sorted[len(cands_sorted):]:
        out[p.pane_id] = jsonls[0]
    return out


def discover(
    remote: Remote,
    server: str,
    cache_dir: Path,
    index: SessionIndex | None = None,
) -> list[LiveSession]:
    """서버 tmux에서 claude로 보이는 pane을 찾아 미러된 세션과 매칭하고 번호를 매긴다."""
    panes = [p for p in tmux.list_panes(remote) if p.command in CLAUDE_COMMANDS]
    panes.sort(key=lambda p: p.location)
    by_project: dict[str, list] = {}
    for p in panes:
        by_project.setdefault(encode_project(p.cwd), []).append(p)

    assign: dict[str, Path | None] = {}
    for project, group in by_project.items():
        proj_dir = cache_dir / "projects" / project
        jsonls = (
            sorted(proj_dir.glob("*.jsonl"), key=_mtime, reverse=True)
            if proj_dir.is_dir() else []
        )
        if not jsonls:
            for p in group:
                assign[p.pane_id] = None
        elif len(group) == 1:
            assign[group[0].pane_id] = jsonls[0]
        else:
            assign.update(_pair_same_cwd(remote, server, group, jsonls, index))

    now = time.time()
    out: list[LiveSession] = []
    for i, p in enumerate(panes, start=1):
        f = assign.get(p.pane_id)
        session_id, title, state = "", "", "unknown"
        if f is not None:
            session_id = f.stem
            state = "working" if now - _mtime(f) < _WORKING_WINDOW_SECS else "idle"
            if index is not None:
                row = index.get_session(server, session_id)
                if row:
                    title = row.title
                    if state != "working" and row.last_role == "assistant":
                        state = "waiting"
        out.append(LiveSession(
            server=server, number=i, pane_id=p.pane_id, location=p.location,
            cwd=p.cwd, project=encode_project(p.cwd), session_id=session_id,
            title=title, state=state,
        ))
    return out
