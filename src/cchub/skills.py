from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from cchub.ssh import Remote

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
PERSONAL_SKILLS = ".claude/skills"  # 원격 홈 기준 상대 경로 (쓰기 연산의 고정 프리픽스)


def valid_skill_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


@dataclass
class SkillInfo:
    server: str
    name: str
    scope: str        # "personal" | "project" | "local"
    path: str         # 스킬 디렉토리 경로 (SKILL.md 제외)
    description: str


def _render_root(path: str) -> str:
    """스캔 스크립트에 넣을 루트 경로 렌더링. ~는 원격 $HOME으로 확장되게."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        rest = path[2:]
        return '"$HOME"/' + shlex.quote(rest) if rest else '"$HOME"'
    return shlex.quote(path)


def _build_scan_script(tagged_roots: list[tuple[str, str]]) -> str:
    """(scope, 스킬부모디렉토리) 목록으로 스캔 스크립트 생성. 출력: scope\\t경로\\t설명"""
    parts = []
    for scope, root in tagged_roots:
        parts.append(
            f'for s in {root}/*/SKILL.md; do '
            f'[ -f "$s" ] || continue; '
            f'desc=$(sed -n "s/^description:[[:space:]]*//p" "$s" | head -1); '
            f'printf "%s\\t%s\\t%s\\n" {scope} "$s" "$desc"; '
            f'done; '
        )
    return "".join(parts)


def scan_skills(
    remote: Remote,
    server: str,
    project_cwds: list[str],
    extra_paths: list[str],
) -> list[SkillInfo]:
    """서버의 개인+프로젝트 스킬을 sh 1회 호출로 조회. 실패 시 []."""
    tagged: list[tuple[str, str]] = [("personal", _render_root("~/" + PERSONAL_SKILLS))]
    seen: set[str] = set()
    for cwd in list(project_cwds) + list(extra_paths):
        root = cwd.rstrip("/") + "/.claude/skills"
        if root in seen:
            continue
        seen.add(root)
        tagged.append(("project", _render_root(root)))
    r = remote.run(["sh", "-c", _build_scan_script(tagged)], timeout=15)
    if r.rc != 0:
        return []
    out: list[SkillInfo] = []
    for line in r.out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or not parts[1].endswith("/SKILL.md"):
            continue
        scope, md_path, desc = parts
        if scope not in ("personal", "project"):
            continue
        skill_dir = md_path[: -len("/SKILL.md")]
        out.append(SkillInfo(
            server=server,
            name=skill_dir.rsplit("/", 1)[-1],
            scope=scope,
            path=skill_dir,
            description=desc.strip(),
        ))
    return out


def _read_description(md: Path) -> str:
    try:
        for line in md.read_text(errors="replace").splitlines()[:30]:
            if line.startswith("description:"):
                return line[len("description:"):].strip()
    except OSError:
        pass
    return ""


def local_skills(lib_dir: Path) -> list[SkillInfo]:
    if not lib_dir.is_dir():
        return []
    out: list[SkillInfo] = []
    for d in sorted(lib_dir.iterdir()):
        md = d / "SKILL.md"
        if d.is_dir() and md.is_file():
            out.append(SkillInfo(
                server="local", name=d.name, scope="local",
                path=str(d), description=_read_description(md),
            ))
    return out
