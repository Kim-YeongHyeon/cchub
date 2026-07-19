from __future__ import annotations

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
    for path in sorted(cache.glob("*/*.jsonl")):
        n = index.index_file(server, path.parent.name, path)
        if n:
            files += 1
            events += n
    return SyncReport(server=server, ok=True, files=files, events=events)
