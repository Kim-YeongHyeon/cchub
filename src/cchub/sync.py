from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cchub.index import SessionIndex
from cchub.ssh import Remote


@dataclass
class SyncReport:
    server: str
    ok: bool
    files: int = 0    # 새 이벤트가 반영된 파일 수
    events: int = 0   # 반영된 message 수
    error: str = ""


def sync_server(
    remote: Remote,
    server: str,
    claude_dir: str,
    cache_root: Path,
    index: SessionIndex,
) -> SyncReport:
    cache = cache_root / server / "projects"
    r = remote.mirror(f"{claude_dir}/projects", cache)
    if r.rc != 0:
        return SyncReport(server=server, ok=False, error=r.err.strip())
    files = events = 0
    failed: list[str] = []
    for path in sorted(cache.glob("*/*.jsonl")):
        try:
            n = index.index_file(server, path.parent.name, path)
        except (OSError, sqlite3.Error):
            failed.append(path.name)
            continue
        if n:
            files += 1
            events += n
    error = f"index 실패 {len(failed)}건: {', '.join(failed)}" if failed else ""
    return SyncReport(server=server, ok=True, files=files, events=events, error=error)
