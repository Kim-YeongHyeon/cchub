# cchub M4 (skill 통합 관리) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 여러 서버의 Claude skill(개인+프로젝트)을 조회하고, 개인 스킬을 pull/deploy/copy/delete하는 CLI와 TUI 조회 화면을 구축한다.

**Architecture:** `skills.py`(textual 무관 순수 모듈)가 스캔 sh 스크립트 생성·출력 파싱·쓰기 연산을 담당하고, CLI(`cchub skills ...`)와 TUI(`s` 화면)가 이를 공유한다. 조회는 개인 스킬 + 활성 tmux pane cwd의 프로젝트 스킬 + config `skill_paths`(스캔 방식 A, 실물 검증 완료), **쓰기는 개인 스킬(`~/.claude/skills/`)에만** 수행한다.

**Tech Stack:** Python 3.13, 기존 `Remote.run/fetch/push`, POSIX sh (sed 기반 frontmatter 추출 — 실제 envector-msa 스킬 9개로 검증 완료), Textual 8.2.8 DataTable.

## Global Constraints

- 런타임 의존성: stdlib + textual>=8,<9 (변경 없음). Claude API 호출 금지.
- **프로젝트 스킬 경로에는 어떤 쓰기 명령도 생성하지 않는다** (deploy/delete는 `~/.claude/skills/<name>`으로만).
- 스킬 이름은 `^[A-Za-z0-9_-]+$` 검증 통과 필수 (pull/deploy/copy/delete 전부) — rm -rf/rsync 경로 주입 차단.
- 원격 스크립트는 POSIX sh만. rsync `--delete` 금지 (deploy는 덮어쓰기 — 로컬에서 지운 파일이 서버에 남을 수 있음을 README에 문서화).
- 블로킹 호출은 TUI에서 `@work(thread=True, exit_on_error=False)` 워커 + `is_cancelled` 가드 + `call_from_thread` (M2~M3 확립 패턴).
- copy의 임시 디렉토리는 `root/relay/` 아래 mkdtemp + try/finally rmtree (M3 push 패턴).
- 테스트 `.venv/bin/pytest` (현재 117개), 브랜치 `m4-skills`, 커밋 메시지 한국어 짧은 제목·트레일러 없음, tests는 `from conftest import FakeRemote`.

## File Structure

```
src/cchub/
  skills.py        # SkillInfo, valid_skill_name, 스캔 스크립트/파싱, local_skills, pull/deploy/delete
  config.py        # (수정) ServerConfig.skill_paths
  cli.py           # (수정) skills 서브커맨드 그룹
  tui/screens.py   # (수정) SkillsScreen
  tui/app.py       # (수정) s 바인딩, action_skills, load_skills 워커
tests/
  test_skills.py, fixtures/sample_skill/SKILL.md (+기존 파일 수정)
```

---

### Task 1: skills.py 스캔 코어 + config skill_paths

**Files:**
- Create: `src/cchub/skills.py`, `tests/fixtures/sample_skill/SKILL.md`
- Modify: `src/cchub/config.py` (ServerConfig에 skill_paths)
- Test: `tests/test_skills.py` (Create), `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.ServerConfig.skill_paths: list[str]` (기본 `[]`, TOML `skill_paths = ["~/repo1"]`)
  - `skills.SkillInfo(server: str, name: str, scope: str, path: str, description: str)` — scope는 `"personal"|"project"|"local"`, path는 스킬 디렉토리 경로(SKILL.md 제외)
  - `skills.valid_skill_name(name: str) -> bool` — `^[A-Za-z0-9_-]+$`
  - `skills.scan_skills(remote: Remote, server: str, project_cwds: list[str], extra_paths: list[str]) -> list[SkillInfo]` — 서버당 sh 1회, 실패 시 `[]`
  - `skills.local_skills(lib_dir: Path) -> list[SkillInfo]` — server="local", scope="local"
  - (내부) `_render_root(path)` — `~/…`는 `"$HOME"/…`로 확장 가능하게, 그 외는 shlex 인용

- [ ] **Step 1: fixture 작성** — `tests/fixtures/sample_skill/SKILL.md`

```markdown
---
name: sample-skill
description: 테스트용 샘플 스킬 — 설명 추출 검증에 사용.
---

# 본문

내용.
```

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_config.py`에 추가:

```python
def test_skill_paths_parsed(tmp_path):
    p = write(tmp_path, '[servers.a]\nhost = "h"\nskill_paths = ["~/repo1", "/abs/repo2"]\n')
    cfg = load_config(p)
    assert cfg.servers["a"].skill_paths == ["~/repo1", "/abs/repo2"]
    p2 = write(tmp_path, '[servers.b]\nhost = "h"\n')
    assert load_config(p2).servers["b"].skill_paths == []
```

`tests/test_skills.py`:

```python
from pathlib import Path

from cchub import skills
from cchub.ssh import RunResult
from conftest import FakeRemote

FIXTURE_SKILL = Path(__file__).parent / "fixtures" / "sample_skill"

SCAN_OUT = (
    "personal\t/home/u/.claude/skills/my-skill/SKILL.md\t개인 스킬 설명\n"
    "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E 실행 스킬\n"
    "project\t/home/u/proj/.claude/skills/no-desc/SKILL.md\t\n"
    "이상한 줄은 무시\n"
)


def test_valid_skill_name():
    assert skills.valid_skill_name("e2e-run")
    assert skills.valid_skill_name("My_Skill2")
    for bad in ("", "a/b", "..", "a b", "a;rm", "한글"):
        assert not skills.valid_skill_name(bad)


def test_scan_skills_parses_output():
    fake = FakeRemote({"sh": RunResult(0, SCAN_OUT, "")})
    out = skills.scan_skills(fake, "srv1", ["/home/u/proj"], ["~/extra"])
    assert [s.name for s in out] == ["my-skill", "e2e-run", "no-desc"]
    assert out[0].scope == "personal" and out[0].server == "srv1"
    assert out[0].path == "/home/u/.claude/skills/my-skill"
    assert out[1].scope == "project" and out[1].description == "E2E 실행 스킬"
    assert out[2].description == ""
    # 스크립트에 세 루트가 전부 들어감 ($HOME 확장형 + 인용된 절대경로)
    script = fake.calls[0][2]
    assert '"$HOME"/.claude/skills' in script
    assert "/home/u/proj/.claude/skills" in script
    assert '"$HOME"/extra/.claude/skills' in script


def test_scan_skills_failure_returns_empty():
    fake = FakeRemote({"sh": RunResult(255, "", "down")})
    assert skills.scan_skills(fake, "srv1", [], []) == []


def test_scan_skills_dedupes_cwds():
    fake = FakeRemote({"sh": RunResult(0, "", "")})
    skills.scan_skills(fake, "srv1", ["/a", "/a", "/b"], [])
    script = fake.calls[0][2]
    assert script.count("/a/.claude/skills") == 1


def test_local_skills(tmp_path):
    lib = tmp_path / "skills"
    (lib / "sample-skill").mkdir(parents=True)
    import shutil
    shutil.copy(FIXTURE_SKILL / "SKILL.md", lib / "sample-skill" / "SKILL.md")
    (lib / "not-a-skill").mkdir()  # SKILL.md 없음 → 제외
    out = skills.local_skills(lib)
    assert len(out) == 1
    s = out[0]
    assert (s.server, s.name, s.scope) == ("local", "sample-skill", "local")
    assert "샘플 스킬" in s.description
    assert skills.local_skills(tmp_path / "없음") == []
```

- [ ] **Step 3: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_skills.py tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.skills'` / skill_paths 부재

- [ ] **Step 4: 구현**

`src/cchub/config.py` — ServerConfig에 필드 추가 + load_config 파싱:

```python
@dataclass
class ServerConfig:
    name: str
    host: str
    results: list[str] = field(default_factory=list)
    claude_dir: str = "~/.claude"
    skill_paths: list[str] = field(default_factory=list)
```

(load_config의 ServerConfig 생성에 `skill_paths=list(s.get("skill_paths", []))` 추가.)

`src/cchub/skills.py`:

```python
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
        parts = line.split("\t")
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
```

- [ ] **Step 5: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_skills.py tests/test_config.py -v`
Expected: 신규 6건 포함 전부 pass

- [ ] **Step 6: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: skill 스캔 코어 및 skill_paths 설정"
```

---

### Task 2: skills.py 쓰기 연산 (pull / deploy / delete)

**Files:**
- Modify: `src/cchub/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `Remote.fetch/push/run`, Task 1의 `valid_skill_name`/`SkillInfo`/`PERSONAL_SKILLS`
- Produces:
  - `skills.pull_skill(remote, info: SkillInfo, lib_dir: Path) -> RunResult` — `remote.fetch(info.path, lib_dir)` (rsync가 `lib_dir/<name>/` 생성). 이름 검증 실패 시 `RunResult(1, "", "잘못된 스킬 이름")`
  - `skills.deploy_skill(remote, lib_dir: Path, name: str) -> RunResult` — 로컬 `lib_dir/name`(SKILL.md 필수) → 원격 `mkdir -p .claude/skills/<name>` 후 `remote.push(lib_dir/name, "~/.claude/skills/<name>")`
  - `skills.delete_skill(remote, name: str) -> RunResult` — `remote.run(["rm", "-rf", f".claude/skills/{name}"])` (홈 기준 상대 경로 — 프리픽스 고정)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_skills.py`에 추가

```python
def make_lib(tmp_path):
    lib = tmp_path / "skills"
    (lib / "my-skill").mkdir(parents=True)
    import shutil
    shutil.copy(FIXTURE_SKILL / "SKILL.md", lib / "my-skill" / "SKILL.md")
    return lib


def test_pull_skill_fetches_into_lib(tmp_path):
    fake = FakeRemote()
    info = skills.SkillInfo(server="srv1", name="e2e-run", scope="project",
                            path="/home/u/proj/.claude/skills/e2e-run", description="")
    r = skills.pull_skill(fake, info, tmp_path / "skills")
    assert r.rc == 0
    assert fake.fetches == [("/home/u/proj/.claude/skills/e2e-run", tmp_path / "skills")]


def test_pull_skill_rejects_bad_name(tmp_path):
    fake = FakeRemote()
    info = skills.SkillInfo(server="s", name="../evil", scope="personal",
                            path="/x/../evil", description="")
    r = skills.pull_skill(fake, info, tmp_path)
    assert r.rc != 0 and fake.fetches == []


def test_deploy_skill_mkdirs_then_pushes(tmp_path):
    fake = FakeRemote()
    lib = make_lib(tmp_path)
    r = skills.deploy_skill(fake, lib, "my-skill")
    assert r.rc == 0
    assert fake.calls[0] == ["mkdir", "-p", ".claude/skills/my-skill"]
    assert fake.pushes == [(lib / "my-skill", "~/.claude/skills/my-skill")]


def test_deploy_skill_requires_local_skill(tmp_path):
    fake = FakeRemote()
    r = skills.deploy_skill(fake, tmp_path, "없는스킬")   # 이름 검증도 실패
    assert r.rc != 0 and fake.pushes == []
    r2 = skills.deploy_skill(fake, tmp_path, "ghost")     # 이름은 유효, 디렉토리 없음
    assert r2.rc != 0 and fake.pushes == []


def test_delete_skill_uses_fixed_prefix(tmp_path):
    fake = FakeRemote()
    r = skills.delete_skill(fake, "my-skill")
    assert r.rc == 0
    assert fake.calls[0] == ["rm", "-rf", ".claude/skills/my-skill"]


def test_delete_skill_rejects_bad_name():
    fake = FakeRemote()
    r = skills.delete_skill(fake, "../../etc")
    assert r.rc != 0 and fake.calls == []
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_skills.py -v`
Expected: 신규 6건 FAIL (함수 부재)

- [ ] **Step 3: 구현** — `src/cchub/skills.py`에 추가 (`from cchub.ssh import Remote, RunResult`로 import 확장)

```python
def pull_skill(remote: Remote, info: SkillInfo, lib_dir: Path) -> RunResult:
    """원격 스킬 디렉토리를 로컬 라이브러리로 가져온다 (lib_dir/<name>/ 생성)."""
    if not valid_skill_name(info.name):
        return RunResult(1, "", f"잘못된 스킬 이름: {info.name}")
    return remote.fetch(info.path, lib_dir)


def deploy_skill(remote: Remote, lib_dir: Path, name: str) -> RunResult:
    """로컬 라이브러리 스킬을 원격 개인 스킬로 배포 (개인 스킬 경로 고정)."""
    if not valid_skill_name(name):
        return RunResult(1, "", f"잘못된 스킬 이름: {name}")
    src = lib_dir / name
    if not (src / "SKILL.md").is_file():
        return RunResult(1, "", f"로컬 라이브러리에 없음: {src}")
    r = remote.run(["mkdir", "-p", f"{PERSONAL_SKILLS}/{name}"])
    if r.rc != 0:
        return r
    return remote.push(src, f"~/{PERSONAL_SKILLS}/{name}")


def delete_skill(remote: Remote, name: str) -> RunResult:
    """원격 개인 스킬 삭제. 경로 프리픽스 고정 + 이름 검증으로 탈출 불가."""
    if not valid_skill_name(name):
        return RunResult(1, "", f"잘못된 스킬 이름: {name}")
    return remote.run(["rm", "-rf", f"{PERSONAL_SKILLS}/{name}"])
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_skills.py -v`
Expected: 12건 전부 pass

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: skill pull/deploy/delete 연산"
```

---

### Task 3: CLI skills 서브커맨드 그룹

**Files:**
- Modify: `src/cchub/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1-2 전부, `tmux.list_panes`/`CLAUDE_COMMANDS`(pane cwd 수집), `_make_remote`, `cchub_dir`
- Produces (명령 — 로컬 라이브러리는 `Path.home()/".claude"/"skills"`, 테스트는 `cli._local_lib`를 monkeypatch):
  - `cchub skills list [server]` — 로컬 라이브러리 + 서버별 스캔 표 출력, 스캔 빈 서버는 `(스킬 없음/접속 불가)`
  - `cchub skills pull <srv> <name> [--force]` — personal 우선, 프로젝트 유일 매칭 허용, 복수 매칭·미존재·기존재(비 --force)는 rc 1
  - `cchub skills deploy <name> <srv...>` — 서버별 성공/실패 표시, 하나라도 실패 시 rc 1
  - `cchub skills copy <src-srv> <name> <dst-srv...>` — relay 임시 경유, try/finally 정리
  - `cchub skills delete <srv> <name> [--yes]` — `--yes` 없으면 `input()`으로 이름 재입력 확인 (불일치 시 취소 rc 1)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cli.py`에 추가

```python
SKILL_SCAN_OUT = (
    "personal\t/home/u/.claude/skills/my-skill/SKILL.md\t개인 스킬\n"
    "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E\n"
)


@pytest.fixture
def skills_env(env, tmp_path, monkeypatch):
    tmp, fake = env
    fake.responses["sh"] = RunResult(0, SKILL_SCAN_OUT, "")
    lib = tmp_path / "lib"
    monkeypatch.setattr(cli, "_local_lib", lambda: lib)
    from pathlib import Path as P
    (lib / "my-skill").mkdir(parents=True)
    (lib / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 로컬 스킬\n---\n")
    return tmp, fake, lib


def test_skills_list(skills_env, capsys):
    assert cli.main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "my-skill" in out and "e2e-run" in out and "local" in out


def test_skills_pull_personal_first(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "pull", "srv1", "my-skill", "--force"]) == 0
    assert fake.fetches[-1][0] == "/home/u/.claude/skills/my-skill"


def test_skills_pull_existing_needs_force(skills_env, capsys):
    assert cli.main(["skills", "pull", "srv1", "my-skill"]) == 1
    assert "--force" in capsys.readouterr().err


def test_skills_pull_unknown_skill(skills_env, capsys):
    assert cli.main(["skills", "pull", "srv1", "ghost"]) == 1
    assert "ghost" in capsys.readouterr().err


def test_skills_deploy(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "deploy", "my-skill", "srv1", "srv2"]) == 0
    assert len(fake.pushes) == 2
    assert fake.pushes[0] == (lib / "my-skill", "~/.claude/skills/my-skill")


def test_skills_copy_relays_and_cleans(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "copy", "srv1", "my-skill", "srv2"]) == 0
    assert fake.fetches and fake.pushes
    relay = tmp / "relay"
    assert relay.exists() and list(relay.iterdir()) == []


def test_skills_delete_confirms_name(skills_env, capsys, monkeypatch):
    tmp, fake, lib = skills_env
    monkeypatch.setattr("builtins.input", lambda prompt="": "my-skill")
    assert cli.main(["skills", "delete", "srv1", "my-skill"]) == 0
    assert ["rm", "-rf", ".claude/skills/my-skill"] in fake.calls


def test_skills_delete_mismatch_aborts(skills_env, capsys, monkeypatch):
    tmp, fake, lib = skills_env
    monkeypatch.setattr("builtins.input", lambda prompt="": "다른이름")
    assert cli.main(["skills", "delete", "srv1", "my-skill"]) == 1
    assert not any(c[:2] == ["rm", "-rf"] for c in fake.calls)


def test_skills_delete_yes_skips_prompt(skills_env, capsys):
    tmp, fake, lib = skills_env
    assert cli.main(["skills", "delete", "srv1", "my-skill", "--yes"]) == 0
    assert ["rm", "-rf", ".claude/skills/my-skill"] in fake.calls
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_cli.py -v -k skills`
Expected: FAIL — skills 서브커맨드 없음

- [ ] **Step 3: 구현** — `src/cchub/cli.py`

import 추가:

```python
from cchub import skills as skills_mod
from cchub.tmux import CLAUDE_COMMANDS
```

서브파서 (main의 sub에 추가):

```python
    ps = sub.add_parser("skills", help="skill 통합 관리")
    ssub = ps.add_subparsers(dest="skills_cmd", required=True)
    q = ssub.add_parser("list", help="서버별 스킬 조회")
    q.add_argument("server", nargs="?")
    q = ssub.add_parser("pull", help="서버 스킬 → 로컬 라이브러리")
    q.add_argument("server"); q.add_argument("name"); q.add_argument("--force", action="store_true")
    q = ssub.add_parser("deploy", help="로컬 스킬 → 서버들(개인 스킬)")
    q.add_argument("name"); q.add_argument("servers", nargs="+")
    q = ssub.add_parser("copy", help="서버 간 스킬 복사 (로컬 경유)")
    q.add_argument("src_server"); q.add_argument("name"); q.add_argument("dst_servers", nargs="+")
    q = ssub.add_parser("delete", help="서버 개인 스킬 삭제")
    q.add_argument("server"); q.add_argument("name"); q.add_argument("--yes", action="store_true")
```

핸들러 dict에 `"skills": cmd_skills` 추가, 구현:

```python
def _local_lib() -> Path:
    return Path.home() / ".claude" / "skills"


def _scan_server(cfg: Config, name: str) -> list:
    """서버 하나의 스킬 스캔 (pane cwd + skill_paths). 실패 격리."""
    s = cfg.servers[name]
    remote = _make_remote(s.host)
    try:
        cwds = [p.cwd for p in tmux.list_panes(remote) if p.command in CLAUDE_COMMANDS]
        return skills_mod.scan_skills(remote, name, cwds, s.skill_paths)
    except Exception:  # noqa: BLE001
        return []


def _resolve_skill(cfg: Config, server: str, name: str):
    """서버에서 스킬 참조 해석: personal 우선, 프로젝트 유일 매칭. 실패 시 None+메시지 출력."""
    if server not in cfg.servers:
        print(f"알 수 없는 서버: {server}", file=sys.stderr)
        return None
    found = [i for i in _scan_server(cfg, server) if i.name == name]
    personal = [i for i in found if i.scope == "personal"]
    if personal:
        return personal[0]
    if len(found) == 1:
        return found[0]
    if not found:
        print(f"{server}에서 스킬을 찾지 못함: {name}", file=sys.stderr)
    else:
        paths = ", ".join(i.path for i in found)
        print(f"{server}에 동명 스킬이 여러 개: {paths}", file=sys.stderr)
    return None


def cmd_skills(args) -> int:
    cfg, root, _index = _ctx()
    if args.skills_cmd == "list":
        rows = skills_mod.local_skills(_local_lib())
        servers = [args.server] if args.server else list(cfg.servers)
        for name in servers:
            if name not in cfg.servers:
                print(f"알 수 없는 서버: {name}", file=sys.stderr)
                return 1
            found = _scan_server(cfg, name)
            if not found:
                print(f"[{name}] (스킬 없음/접속 불가)")
            rows += found
        for i in rows:
            desc = i.description[:60]
            print(f"{i.server:10s} {i.scope:8s} {i.name:24s} {desc}  ({i.path})")
        return 0

    if args.skills_cmd == "pull":
        info = _resolve_skill(cfg, args.server, args.name)
        if info is None:
            return 1
        dest = _local_lib() / info.name
        if dest.exists() and not args.force:
            print(f"이미 존재: {dest} (덮어쓰려면 --force)", file=sys.stderr)
            return 1
        r = skills_mod.pull_skill(_make_remote(cfg.servers[args.server].host), info, _local_lib())
        if r.rc != 0:
            print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
            return 1
        print(f"가져옴: {dest}")
        return 0

    if args.skills_cmd == "deploy":
        ok = True
        for name in args.servers:
            if name not in cfg.servers:
                print(f"알 수 없는 서버: {name}", file=sys.stderr)
                return 1
            r = skills_mod.deploy_skill(_make_remote(cfg.servers[name].host), _local_lib(), args.name)
            if r.rc == 0:
                print(f"{name}: 배포됨 (~/.claude/skills/{args.name})")
            else:
                ok = False
                print(f"{name}: 실패 — {r.err.strip()}", file=sys.stderr)
        return 0 if ok else 1

    if args.skills_cmd == "copy":
        import tempfile
        info = _resolve_skill(cfg, args.src_server, args.name)
        if info is None:
            return 1
        relay_root = root / "relay"
        relay_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=relay_root))
        try:
            r = skills_mod.pull_skill(_make_remote(cfg.servers[args.src_server].host), info, tmp)
            if r.rc != 0:
                print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
                return 1
            ok = True
            for name in args.dst_servers:
                if name not in cfg.servers:
                    print(f"알 수 없는 서버: {name}", file=sys.stderr)
                    return 1
                r = skills_mod.deploy_skill(_make_remote(cfg.servers[name].host), tmp, info.name)
                if r.rc == 0:
                    print(f"{name}: 복사됨")
                else:
                    ok = False
                    print(f"{name}: 실패 — {r.err.strip()}", file=sys.stderr)
            return 0 if ok else 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if args.skills_cmd == "delete":
        if args.server not in cfg.servers:
            print(f"알 수 없는 서버: {args.server}", file=sys.stderr)
            return 1
        if not skills_mod.valid_skill_name(args.name):
            print(f"잘못된 스킬 이름: {args.name}", file=sys.stderr)
            return 1
        if not args.yes:
            print(f"{args.server}의 개인 스킬 ~/.claude/skills/{args.name} 을(를) 삭제합니다.")
            if input(f"확인을 위해 스킬 이름을 다시 입력하세요: ").strip() != args.name:
                print("취소됨", file=sys.stderr)
                return 1
        r = skills_mod.delete_skill(_make_remote(cfg.servers[args.server].host), args.name)
        if r.rc != 0:
            print(f"삭제 실패: {r.err.strip()}", file=sys.stderr)
            return 1
        print("삭제됨")
        return 0
    return 1
```

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 129)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: cchub skills CLI (list/pull/deploy/copy/delete)"
```

---

### Task 4: TUI SkillsScreen (s 키, 조회 전용)

**Files:**
- Modify: `src/cchub/tui/screens.py`, `src/cchub/tui/app.py`
- Test: `tests/test_screens.py`

**Interfaces:**
- Consumes: `skills.scan_skills/local_skills/SkillInfo`, `tmux.list_panes`/`CLAUDE_COMMANDS`
- Produces: `screens.SkillsScreen(ModalScreen[None])` — `show_rows(rows: list[SkillInfo])`로 채움, escape 닫기, `#skills-table` DataTable(서버/scope/이름/설명); `CchubApp.action_skills`(s 바인딩) + `load_skills(screen)` 워커(`group="skills"`, thread=True, exclusive=True, exit_on_error=False, is_cancelled 가드, 서버별 예외 격리)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_screens.py`에 추가

```python
async def test_s_opens_skills_screen_with_rows(tmp_path):
    from cchub.config import ServerConfig
    from cchub.ssh import RunResult
    from cchub.tui.screens import SkillsScreen
    from conftest import FakeRemote

    fake = FakeRemote({
        ("tmux", "list-panes"): RunResult(0, "%5\tm:0.0\t/home/u/proj\tclaude\t100\n", ""),
        "sh": RunResult(0, "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E\n", ""),
    })
    app = make_indexed_app(tmp_path)
    app.remote_factory = lambda h: fake
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        await pilot.press("s")
        assert isinstance(app.screen, SkillsScreen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.screen.query_one("#skills-table", DataTable)
        assert table.row_count >= 1
        await pilot.press("escape")
        assert not isinstance(app.screen, SkillsScreen)
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_screens.py -v -k skills`
Expected: FAIL — SkillsScreen 부재

- [ ] **Step 3: 구현**

`src/cchub/tui/screens.py`에 추가 (import에 `from cchub.skills import SkillInfo`):

```python
class SkillsScreen(ModalScreen[None]):
    """전 서버 skill 조회 (읽기 전용). 배포/삭제는 CLI로."""

    CSS = """
    SkillsScreen { align: center middle; }
    #skills-box { width: 95%; height: 85%; background: $panel; border: solid $primary; }
    #skills-table { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "닫기")]

    def compose(self) -> ComposeResult:
        with Vertical(id="skills-box"):
            yield DataTable(id="skills-table")

    def on_mount(self) -> None:
        table = self.query_one("#skills-table", DataTable)
        table.add_columns("서버", "scope", "이름", "설명")
        table.cursor_type = "row"
        table.loading = True

    def show_rows(self, rows: list[SkillInfo]) -> None:
        table = self.query_one("#skills-table", DataTable)
        table.loading = False
        table.clear()
        for i in rows:
            table.add_row(i.server, i.scope, i.name, i.description[:80])

    def action_close(self) -> None:
        self.dismiss(None)
```

`src/cchub/tui/app.py` — import에 `from pathlib import Path`(이미 있음), `from cchub.skills import local_skills, scan_skills`, `from cchub.tmux import CLAUDE_COMMANDS`, `SkillsScreen` 추가. BINDINGS에 `Binding("s", "skills", "스킬")`. 메서드:

```python
    def action_skills(self) -> None:
        screen = SkillsScreen()
        self.push_screen(screen)
        self.load_skills(screen)

    @work(thread=True, exclusive=True, group="skills", exit_on_error=False)
    def load_skills(self, screen: SkillsScreen) -> None:
        worker = get_current_worker()
        rows = local_skills(Path.home() / ".claude" / "skills")
        for name, s in self.cfg.servers.items():
            if worker.is_cancelled:
                return
            try:
                remote = self.remote_factory(s.host)
                cwds = [p.cwd for p in tmux.list_panes(remote)
                        if p.command in CLAUDE_COMMANDS]
                rows += scan_skills(remote, name, cwds, s.skill_paths)
            except Exception:  # noqa: BLE001 - 서버별 격리
                continue
        if worker.is_cancelled:
            return
        self.call_from_thread(screen.show_rows, rows)
```

주의: SkillsScreen의 Input이 없으므로 Enter 버블링 이슈 없음 (M3 최종 리뷰의 id 가드가 이미 앱에 있음).

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 130)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: TUI 스킬 조회 화면 (s)"
```

---

### Task 5: localhost 실물 스모크 + README + 버전 0.4.0

**Files:** Modify: `README.md`, `pyproject.toml`, `tests/test_tui_app.py` 또는 `tests/test_skills.py` (통합 테스트)
전제: `cchub-smoke` alias 동작, `~/envector-msa/.claude/skills`에 실제 스킬 9개 존재, 로컬 `~/.claude/skills`는 비어 있음.

- [ ] **Step 1: 실물 통합 테스트** — `tests/test_skills.py`에 추가 (기존 `requires_smoke_ssh` 가드를 test_tui_app에서 import 하거나 동일 패턴 재정의)

```python
import subprocess

import pytest

requires_smoke_ssh = pytest.mark.skipif(
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
                    "cchub-smoke", "true"], capture_output=True).returncode != 0,
    reason="cchub-smoke ssh alias 불가",
)


@requires_smoke_ssh
def test_real_scan_and_deploy_roundtrip(tmp_path):
    """실제 SSH로 스캔 + 스크래치 스킬 deploy→재스캔→delete 왕복 (실제 스킬은 불변)."""
    from cchub.ssh import SSHRemote

    remote = SSHRemote("cchub-smoke")
    # 1) 프로젝트 스킬 스캔 (skill_paths로 envector-msa 지정 — pane cwd 무관하게 보장)
    found = skills.scan_skills(remote, "local", [], ["~/envector-msa"])
    assert any(s.name == "e2e-run" and s.scope == "project" for s in found)
    assert all(s.description for s in found if s.name == "e2e-run")

    # 2) 스크래치 스킬 왕복 (고유 이름으로 실제 개인 스킬과 충돌 방지)
    name = "cchub-smoke-roundtrip"
    lib = tmp_path / "lib"
    (lib / name).mkdir(parents=True)
    (lib / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: cchub M4 스모크 테스트용 임시 스킬\n---\n")
    try:
        assert skills.deploy_skill(remote, lib, name).rc == 0
        found = skills.scan_skills(remote, "local", [], [])
        assert any(s.name == name and s.scope == "personal" for s in found)
    finally:
        assert skills.delete_skill(remote, name).rc == 0
    found = skills.scan_skills(remote, "local", [], [])
    assert not any(s.name == name for s in found)
```

Run: `cd ~/cchub && .venv/bin/pytest tests/test_skills.py -v -k real_scan`
Expected: PASS (실패 시 실제 결함 — TDD로 수정)

- [ ] **Step 2: README + 버전**

`pyproject.toml`: `version = "0.4.0"`.
`README.md`: 명령어 표에 `skills list/pull/deploy/copy/delete` 추가; TUI 키맵에 `s` 행; "skill 통합 관리" 섹션 — 조회는 개인+프로젝트(활성 pane cwd + `skill_paths`), **쓰기는 개인 스킬만**(프로젝트 스킬은 git 관리라 불변), deploy는 `--delete` 없는 덮어쓰기(로컬에서 지운 파일이 서버에 남을 수 있음), delete는 이름 재입력 확인; config 예시에 `skill_paths` 추가.

- [ ] **Step 3: 전체 테스트 + 커밋**

```bash
cd ~/cchub && .venv/bin/pytest -q   # 전체 pass 확인 (약 131)
git add -A && git commit -m "docs: skill 관리 README 반영 및 버전 0.4.0"
```

---

## Self-Review 결과 (계획 작성 시 수행)

- 스펙 커버리지: 조회(개인+프로젝트+skill_paths, 스캔 A)·pull·deploy·copy·delete·TUI s 화면·안전장치(이름 검증/경로 고정/확인 입력/relay 정리)·에러 격리 — 스펙 전 항목이 태스크 1~5에 매핑. 범위 외(플러그인/버전 관리/TUI 쓰기) 미포함 확인.
- 타입 일관성: SkillInfo/scan_skills/local_skills/pull_skill/deploy_skill/delete_skill/PERSONAL_SKILLS/_local_lib — 태스크 간 시그니처 일치. FakeRemote fetch/push 기록 형식은 M3 Task 4와 동일((remote_path, Path), (Path, remote_dir)).
- 스캔 스크립트·sed·frontmatter는 실제 envector-msa 스킬 9개로 실행 검증 완료. 플레이스홀더 없음.
