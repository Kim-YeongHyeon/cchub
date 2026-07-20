from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cchub.config import Config
from cchub.ssh import SSHRemote
from cchub.tui.data import RemoteFactory


@dataclass
class FetchReport:
    server: str
    ok: bool
    failed: list[str] = field(default_factory=list)


def collect_results(
    cfg: Config,
    root: Path,
    server: str,
    remote_factory: RemoteFactory = SSHRemote,
) -> FetchReport:
    """서버의 results 패턴들을 root/results/<server>/로 수집. 패턴별 실패 격리."""
    s = cfg.servers[server]
    dest = root / "results" / server
    dest.mkdir(parents=True, exist_ok=True)
    remote = remote_factory(s.host)
    failed: list[str] = []
    for pattern in s.results:
        try:
            r = remote.fetch(pattern, dest)
        except Exception:  # noqa: BLE001
            failed.append(pattern)
            continue
        if r.rc != 0:
            failed.append(pattern)
    return FetchReport(server=server, ok=not failed, failed=failed)
