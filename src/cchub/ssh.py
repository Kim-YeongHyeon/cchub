from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cchub.config import cchub_dir


def _control_socket_dir() -> Path:
    """ssh ControlPath용 소켓 디렉터리.

    AF_UNIX 소켓 경로는 보통 108바이트로 제한된다. CCHUB_DIR이 깊은 경로(예:
    스크래치패드 하위)에 있으면 그 아래에 소켓을 두는 것만으로 한도를 넘어설 수
    있으므로, 시스템 임시 디렉터리 아래 짧고 CCHUB_DIR별로 고유한 경로를 쓴다.
    """
    digest = hashlib.sha256(str(cchub_dir()).encode()).hexdigest()[:16]
    d = Path(tempfile.gettempdir()) / f"cchub-cm-{os.getuid()}-{digest}"
    d.mkdir(parents=True, exist_ok=True)
    return d


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

    def fetch(self, remote_path: str, local_dir: Path, timeout: int = 300) -> RunResult:
        raise NotImplementedError

    def push(self, local_path: Path, remote_dir: str, timeout: int = 300) -> RunResult:
        raise NotImplementedError


class SSHRemote(Remote):
    def __init__(self, host: str):
        self.host = host
        cm = _control_socket_dir()
        # host 문자열 자체가 길 수 있으므로(예: FQDN) %r@%h-%p 대신 짧은 해시를 쓴다.
        host_key = hashlib.sha256(host.encode()).hexdigest()[:16]
        self._opts = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=600",
            "-o", f"ControlPath={cm}/{host_key}",
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

    def _rsync(self, src: str, dst: str, timeout: int) -> RunResult:
        try:
            p = subprocess.run(
                ["rsync", "-az", "-e", "ssh " + " ".join(self._opts), src, dst],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        # --delete 금지: 서버 쪽 30일 정리가 로컬 이력을 지우지 않도록
        local_dir.mkdir(parents=True, exist_ok=True)
        return self._rsync(f"{self.host}:{remote_dir}/", str(local_dir) + "/", timeout)

    def fetch(self, remote_path: str, local_dir: Path, timeout: int = 300) -> RunResult:
        local_dir.mkdir(parents=True, exist_ok=True)
        return self._rsync(f"{self.host}:{remote_path}", str(local_dir) + "/", timeout)

    def push(self, local_path: Path, remote_dir: str, timeout: int = 300) -> RunResult:
        src = str(local_path) + ("/" if local_path.is_dir() else "")
        return self._rsync(src, f"{self.host}:{remote_dir}/", timeout)
