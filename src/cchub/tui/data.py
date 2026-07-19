from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.sessions import LiveSession, discover
from cchub.ssh import Remote, SSHRemote
from cchub.sync import sync_server

RemoteFactory = Callable[[str], Remote]


@dataclass
class ServerSnapshot:
    server: str
    sessions: list[LiveSession] = field(default_factory=list)
    error: str = ""


def collect_sessions(
    cfg: Config,
    root: Path,
    index: SessionIndex,
    remote_factory: RemoteFactory = SSHRemote,
) -> dict[str, ServerSnapshot]:
    """서버별 sync 후 discover. 서버 하나의 실패가 다른 서버에 전파되지 않는다."""
    out: dict[str, ServerSnapshot] = {}
    for name, s in cfg.servers.items():
        remote = remote_factory(s.host)
        rep = sync_server(remote, name, s.claude_dir, root / "cache", index)
        sessions = discover(remote, name, root / "cache" / name, index)
        out[name] = ServerSnapshot(
            server=name,
            sessions=sessions,
            error="" if rep.ok else rep.error,
        )
    return out
