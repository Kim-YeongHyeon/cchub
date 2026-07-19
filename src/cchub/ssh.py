from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cchub.config import cchub_dir


@dataclass
class RunResult:
    rc: int
    out: str
    err: str


class Remote:
    """원격 명령 실행 인터페이스. 테스트에서는 FakeRemote로 대체한다."""

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        raise NotImplementedError

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        raise NotImplementedError


class SSHRemote(Remote):
    def __init__(self, host: str):
        self.host = host
        cm = cchub_dir() / "cm"
        cm.mkdir(parents=True, exist_ok=True)
        self._opts = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=600",
            "-o", f"ControlPath={cm}/%r@%h-%p",
        ]

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        remote_cmd = " ".join(shlex.quote(a) for a in argv)
        try:
            p = subprocess.run(
                ["ssh", *self._opts, self.host, remote_cmd],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        # --delete 금지: 서버 쪽 30일 정리가 로컬 이력을 지우지 않도록
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(
                [
                    "rsync", "-az",
                    "-e", "ssh " + " ".join(self._opts),
                    f"{self.host}:{remote_dir}/",
                    str(local_dir) + "/",
                ],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)
