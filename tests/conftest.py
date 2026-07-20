from pathlib import Path

from cchub.ssh import Remote, RunResult


class FakeRemote(Remote):
    """argv를 기록하고 미리 정한 응답을 돌려주는 테스트용 Remote."""

    def __init__(self, responses: dict[str, RunResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.mirrors: list[tuple[str, Path]] = []
        self.fetches: list[tuple[str, Path]] = []
        self.pushes: list[tuple[Path, str]] = []

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        self.calls.append(list(argv))
        key2 = (argv[0], argv[1]) if len(argv) > 1 else None
        if key2 in self.responses:
            return self.responses[key2]
        return self.responses.get(argv[0], RunResult(0, "", ""))

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        self.mirrors.append((remote_dir, Path(local_dir)))
        return RunResult(0, "", "")

    def fetch(self, remote_path: str, local_dir, timeout: int = 300) -> RunResult:
        self.fetches.append((remote_path, Path(local_dir)))
        # Simulate fetching skill directories
        if "/.claude/skills/" in remote_path:
            skill_name = remote_path.rstrip("/").split("/")[-1]
            skill_dir = Path(local_dir) / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text("---\nname: " + skill_name + "\ndescription: 원격 스킬\n---\n")
        return RunResult(0, "", "")

    def push(self, local_path, remote_dir: str, timeout: int = 300) -> RunResult:
        self.pushes.append((Path(local_path), remote_dir))
        return RunResult(0, "", "")
