from pathlib import Path

from cchub.ssh import Remote, RunResult


class FakeRemote(Remote):
    """argv를 기록하고 미리 정한 응답을 돌려주는 테스트용 Remote."""

    def __init__(self, responses: dict[str, RunResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.mirrors: list[tuple[str, Path]] = []

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        self.calls.append(list(argv))
        return self.responses.get(argv[0], RunResult(0, "", ""))

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        self.mirrors.append((remote_dir, Path(local_dir)))
        return RunResult(0, "", "")
