# cchub spawn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cchub spawn <server> [cwd]`와 TUI `N` 키로 원격 서버에 detached tmux 세션을 만들고 claude를 기동하며, 선택적 초기 프롬프트를 부팅 폴링 후 주입한다.

**Architecture:** 경로 렌더 헬퍼를 `ssh.render_remote_path`로 추출(선행 리팩터, skills·doctor 공유)한 뒤, `tmux.py`에 순수 `spawn_session`(Remote 주입, `sleep` 주입으로 테스트 가능)을 추가하고, CLI `cmd_spawn`과 TUI(`SpawnScreen` 모달 + thread 워커)가 이를 소비한다. 성공 기준은 tmux 세션 생성이며 프롬프트 전달은 best-effort.

**Tech Stack:** Python 3.13, argparse, Textual 8.x, pytest(+asyncio auto). 기존 `Remote`/`FakeRemote`, send-keys `-l --` 안전 패턴 재사용.

## Global Constraints

- Python ≥ 3.13, 외부 의존성 추가 금지 (stdlib + 기존 Textual만).
- 원격 명령은 `remote.run(argv)`로만; 틸드 확장은 `["sh","-c",...]` + `"$HOME"` (render_remote_path).
- send-keys는 항상 `-l --` (리터럴 + 플래그 차단).
- 모든 사용자 대면 문자열은 한국어.
- launch 명령: 기본 `claude --dangerously-skip-permissions`, `--safe` 시 `claude` (정확히 이 문자열).
- 세션명 형식: `[A-Za-z0-9_-]+` (진입부 검증), 자동 이름은 `cchub-<n>` (미사용 최소 정수 n≥1).
- 성공 기준 = tmux 세션 생성. 프롬프트 미전달은 exit 0 유지 + 경고.
- TUI 워커 규약: `thread=True, exclusive=True(그룹), exit_on_error=False, is_cancelled 가드, UI는 call_from_thread`.
- 테스트 실행: `.venv/bin/python -m pytest` (작업 디렉토리 /Users/kim-0h/Desktop/cchub). 커밋 전 전체 스위트 1회 (현재 기준 ~169 passed, 4 skipped — 전체 수치로 보고).

---

### Task 1: `render_remote_path` 추출 (ssh.py) + skills/doctor 리팩터

**Files:**
- Modify: `src/cchub/ssh.py` (함수 추가)
- Modify: `src/cchub/skills.py:32-39` (`_render_root` 제거, 호출부 교체)
- Modify: `src/cchub/doctor.py:30-38` (`_projects_check_script` 단순화)
- Test: `tests/test_ssh.py`

**Interfaces:**
- Consumes: 없음 (기존 `shlex`).
- Produces: `cchub.ssh.render_remote_path(path: str) -> str` — `"~"`→`'"$HOME"'`, `"~/x"`→`'"$HOME"/' + shlex.quote("x")`, `"~/"`→`'"$HOME"'`, 그 외→`shlex.quote(path)`. Task 2가 cwd 렌더에 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ssh.py`에 추가 (모듈 상단 import에 `render_remote_path` 추가: `from cchub.ssh import RunResult, SSHRemote, render_remote_path`):

```python
def test_render_remote_path_variants():
    assert render_remote_path("~") == '"$HOME"'
    assert render_remote_path("~/proj") == '"$HOME"/proj'
    assert render_remote_path("~/") == '"$HOME"'
    assert render_remote_path("/abs/path") == "/abs/path"
    assert render_remote_path("~/my dir") == '"$HOME"/' + "'my dir'"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_ssh.py -k render -q`
Expected: FAIL — ImportError (`render_remote_path` 없음).

- [ ] **Step 3: ssh.py에 구현 (skills._render_root 이동)**

`src/cchub/ssh.py`의 `_control_socket_dir` 아래에 추가:

```python
def render_remote_path(path: str) -> str:
    """원격 셸 명령에 넣을 경로 렌더링. ~는 원격 $HOME으로 확장되게, 그 외는 인용."""
    if path == "~":
        return '"$HOME"'
    if path.startswith("~/"):
        rest = path[2:]
        return '"$HOME"/' + shlex.quote(rest) if rest else '"$HOME"'
    return shlex.quote(path)
```

(`shlex`는 ssh.py에 이미 import되어 있음.)

- [ ] **Step 4: skills.py 교체**

`src/cchub/skills.py`에서:
- `from cchub.ssh import Remote, RunResult` → `from cchub.ssh import Remote, RunResult, render_remote_path`
- `_render_root` 함수(32-39행) 전체 삭제.
- 63행: `[("personal", _render_root("~/" + PERSONAL_SKILLS))]` → `[("personal", render_remote_path("~/" + PERSONAL_SKILLS))]`
- 70행: `tagged.append(("project", _render_root(root)))` → `tagged.append(("project", render_remote_path(root)))`
- `import shlex`가 다른 곳에서 안 쓰이면 제거: `grep -n "shlex" src/cchub/skills.py`로 확인 후 미사용 시 삭제.

- [ ] **Step 5: doctor.py 교체**

`src/cchub/doctor.py`에서:
- `from cchub.ssh import Remote` → `from cchub.ssh import Remote, render_remote_path`
- `_projects_check_script`를 다음으로 교체:

```python
def _projects_check_script(claude_dir: str) -> str:
    # claude_dir 의 ~ 는 원격 $HOME 으로 확장
    return f"test -d {render_remote_path(claude_dir)}/projects"
```

- `import shlex` 미사용 시 삭제 (`grep -n "shlex" src/cchub/doctor.py`).

- [ ] **Step 6: 전체 테스트 (기존 skills/doctor 테스트 무수정 통과가 회귀 게이트)**

Run: `.venv/bin/python -m pytest -q`
Expected: 전체 passed (기존 168 + 신규 1 = 169 passed, 4 skipped). tests/test_skills.py·tests/test_doctor.py는 한 줄도 수정하지 않은 채 통과해야 한다.

- [ ] **Step 7: 커밋**

```bash
git add src/cchub/ssh.py src/cchub/skills.py src/cchub/doctor.py tests/test_ssh.py
git commit -m "refactor: 원격 경로 렌더를 ssh.render_remote_path로 통합 (skills·doctor 공유)"
```

---

### Task 2: `tmux.spawn_session` + `list_session_names`

**Files:**
- Modify: `src/cchub/tmux.py`
- Test: `tests/test_tmux.py`

**Interfaces:**
- Consumes: `render_remote_path` (Task 1), 기존 `list_panes`, `send_prompt`, `CLAUDE_COMMANDS`, `Remote`.
- Produces (Task 3·4가 사용):

```python
@dataclass
class SpawnResult:
    ok: bool                        # tmux 세션 생성 성공 여부 (성공 기준)
    name: str = ""                  # 실제 세션명
    prompt_sent: bool | None = None # None=프롬프트 미요청, True/False=전달 여부
    error: str = ""

def list_session_names(remote: Remote) -> list[str]
def spawn_session(remote: Remote, cwd: str, launch_cmd: str, name: str | None = None,
                  prompt: str | None = None, poll_attempts: int = 20,
                  poll_interval: float = 0.5,
                  sleep: Callable[[float], None] = time.sleep) -> SpawnResult
```

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tmux.py`에 추가. 기존 파일의 import 방식을 먼저 확인(`head -15 tests/test_tmux.py`)하고 맞춘다. FakeRemote는 `from conftest import FakeRemote`, `RunResult`는 `from cchub.ssh import RunResult`. FakeRemote는 `(argv[0], argv[1])` 2-튜플 키 우선 매칭임에 유의.

```python
from cchub.tmux import SpawnResult, list_session_names, spawn_session

PANE_CLAUDE = "%9\tcchub-2:0.0\t/home/u\tclaude\t200\n"
PANE_BASH = "%9\tcchub-2:0.0\t/home/u\tbash\t200\n"


def test_list_session_names():
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "main\ncchub-1\n", "")})
    assert list_session_names(fake) == ["main", "cchub-1"]
    fake2 = FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running")})
    assert list_session_names(fake2) == []


def test_spawn_autoname_skips_taken():
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "cchub-1\n", "")})
    res = spawn_session(fake, "~", "claude --dangerously-skip-permissions")
    assert res.ok and res.name == "cchub-2" and res.prompt_sent is None
    # new-session은 sh -c 로 cwd를 $HOME 확장해 실행
    sh_calls = [c for c in fake.calls if c[0] == "sh"]
    assert sh_calls and 'tmux new-session -d -s cchub-2 -c "$HOME"' in sh_calls[0][2]
    # launch 명령은 -l -- 리터럴 주입 후 Enter
    sends = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    assert ["tmux", "send-keys", "-t", "cchub-2", "-l", "--",
            "claude --dangerously-skip-permissions"] in sends
    assert ["tmux", "send-keys", "-t", "cchub-2", "Enter"] in sends


def test_spawn_explicit_name_and_cwd():
    fake = FakeRemote()
    res = spawn_session(fake, "~/proj", "claude", name="exp1")
    assert res.ok and res.name == "exp1"
    sh_calls = [c for c in fake.calls if c[0] == "sh"]
    assert 'tmux new-session -d -s exp1 -c "$HOME"/proj' in sh_calls[0][2]
    # 이름이 지정되면 list-sessions 조회 불필요
    assert not any(c[:2] == ["tmux", "list-sessions"] for c in fake.calls)


def test_spawn_new_session_failure():
    fake = FakeRemote({"sh": RunResult(1, "", "create session failed: no such directory")})
    res = spawn_session(fake, "/no/such/dir", "claude")
    assert not res.ok and "no such directory" in res.error
    assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


def test_spawn_prompt_sent_after_claude_boots():
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, PANE_CLAUDE, "")})
    slept = []
    res = spawn_session(fake, "~", "claude", name="cchub-2",
                        prompt="버그 고쳐줘", sleep=slept.append)
    assert res.ok and res.prompt_sent is True
    assert slept == []                       # 첫 폴링에 이미 기동 → 대기 없음
    sends = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    # 프롬프트는 발견한 pane_id(%9)로 전송
    assert ["tmux", "send-keys", "-t", "%9", "-l", "--", "버그 고쳐줘"] in sends


def test_spawn_prompt_poll_timeout():
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, PANE_BASH, "")})
    slept = []
    res = spawn_session(fake, "~", "claude", name="cchub-2",
                        prompt="버그 고쳐줘", poll_attempts=3, sleep=slept.append)
    assert res.ok and res.prompt_sent is False
    assert len(slept) == 3                   # 매 실패마다 sleep
    assert not any(c == ["tmux", "send-keys", "-t", "%9", "-l", "--", "버그 고쳐줘"]
                   for c in fake.calls)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_tmux.py -k spawn -q`
Expected: FAIL — ImportError (`spawn_session` 없음).

- [ ] **Step 3: tmux.py 구현**

`src/cchub/tmux.py` 상단 import 수정:

```python
from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from typing import Callable

from cchub.ssh import Remote, render_remote_path
```

파일 끝(`confirm_delivery` 아래)에 추가:

```python
@dataclass
class SpawnResult:
    ok: bool                         # tmux 세션 생성 성공 여부 (성공 기준)
    name: str = ""                   # 실제 세션명
    prompt_sent: bool | None = None  # None=프롬프트 미요청, True/False=전달 여부
    error: str = ""


def list_session_names(remote: Remote) -> list[str]:
    """tmux 세션명 목록. 서버 미기동/미설치 시 []."""
    r = remote.run(["tmux", "list-sessions", "-F", "#{session_name}"])
    if r.rc != 0:
        return []
    return [ln for ln in r.out.splitlines() if ln]


def spawn_session(
    remote: Remote,
    cwd: str,
    launch_cmd: str,
    name: str | None = None,
    prompt: str | None = None,
    poll_attempts: int = 20,
    poll_interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult:
    """detached tmux 세션 생성 + launch_cmd 기동 (+ 선택적 초기 프롬프트).

    성공 기준은 세션 생성. prompt는 pane command가 claude 계열이 될 때까지
    폴링한 뒤 전송하며, 타임아웃이어도 세션은 성공으로 본다 (prompt_sent=False).
    """
    if name is None:
        existing = set(list_session_names(remote))
        n = 1
        while f"cchub-{n}" in existing:
            n += 1
        name = f"cchub-{n}"
    r = remote.run([
        "sh", "-c",
        f"tmux new-session -d -s {shlex.quote(name)} -c {render_remote_path(cwd)}",
    ])
    if r.rc != 0:
        return SpawnResult(ok=False, name=name, error=r.err.strip())
    if (remote.run(["tmux", "send-keys", "-t", name, "-l", "--", launch_cmd]).rc != 0
            or remote.run(["tmux", "send-keys", "-t", name, "Enter"]).rc != 0):
        return SpawnResult(ok=True, name=name,
                           prompt_sent=False if prompt is not None else None,
                           error="claude 실행 명령 주입 실패")
    if prompt is None:
        return SpawnResult(ok=True, name=name)
    for _ in range(poll_attempts):
        pane = next(
            (p for p in list_panes(remote)
             if p.location.startswith(f"{name}:") and p.command in CLAUDE_COMMANDS),
            None,
        )
        if pane is not None:
            return SpawnResult(ok=True, name=name,
                               prompt_sent=send_prompt(remote, pane.pane_id, prompt))
        sleep(poll_interval)
    return SpawnResult(ok=True, name=name, prompt_sent=False)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_tmux.py -q`
Expected: 전부 passed (신규 6 포함).

- [ ] **Step 5: 전체 스위트 + 커밋**

Run: `.venv/bin/python -m pytest -q` — Expected: 175 passed, 4 skipped (수치는 전체 기준으로 보고).

```bash
git add src/cchub/tmux.py tests/test_tmux.py
git commit -m "feat: tmux.spawn_session — detached 세션 생성 + claude 기동 + 프롬프트 폴링"
```

---

### Task 3: CLI `cchub spawn`

**Files:**
- Modify: `src/cchub/cli.py` (cmd_spawn, argparse, handler dict)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `tmux.spawn_session`, `tmux.list_session_names`, `tmux.SpawnResult` (Task 2); 기존 `load_config`, `_make_remote`.
- Produces: `def cmd_spawn(args) -> int`; argparse 서브커맨드 `spawn` (`server`, `cwd` nargs="?" default="~", `--name`, `--safe`, `--prompt`).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_cli.py`에 추가 (모듈 상단에 `from cchub.tmux import SpawnResult` 추가; `Config`/`ServerConfig`/`FakeRemote`/`RunResult`는 기존 import 재사용 — 파일 상단 확인):

```python
def _spawn_cfg():
    return Config(sync_interval=30, stats_interval=2,
                  servers={"srv1": ServerConfig(name="srv1", host="sudal")})


def test_cmd_spawn_default_flags(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    captured = {}

    def fake_spawn(remote, cwd, launch_cmd, name=None, prompt=None):
        captured.update(cwd=cwd, launch=launch_cmd, name=name, prompt=prompt)
        return SpawnResult(ok=True, name="cchub-1")

    monkeypatch.setattr(cli.tmux, "spawn_session", fake_spawn)
    rc = cli.main(["spawn", "srv1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured == {"cwd": "~", "launch": "claude --dangerously-skip-permissions",
                        "name": None, "prompt": None}
    assert "cchub-1" in out and "tmux attach -t cchub-1" in out


def test_cmd_spawn_safe_name_prompt(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    captured = {}

    def fake_spawn(remote, cwd, launch_cmd, name=None, prompt=None):
        captured.update(cwd=cwd, launch=launch_cmd, name=name, prompt=prompt)
        return SpawnResult(ok=True, name=name, prompt_sent=True)

    monkeypatch.setattr(cli.tmux, "spawn_session", fake_spawn)
    rc = cli.main(["spawn", "srv1", "~/proj", "--safe", "--name", "exp1",
                   "--prompt", "테스트 돌려줘"])
    assert rc == 0
    assert captured == {"cwd": "~/proj", "launch": "claude",
                        "name": "exp1", "prompt": "테스트 돌려줘"}


def test_cmd_spawn_prompt_not_delivered_warns(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    monkeypatch.setattr(cli.tmux, "spawn_session",
                        lambda *a, **k: SpawnResult(ok=True, name="cchub-1",
                                                    prompt_sent=False))
    rc = cli.main(["spawn", "srv1", "--prompt", "x"])
    err = capsys.readouterr().err
    assert rc == 0                       # 세션 생성이 성공 기준
    assert "미전달" in err


def test_cmd_spawn_create_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    monkeypatch.setattr(cli.tmux, "spawn_session",
                        lambda *a, **k: SpawnResult(ok=False, name="cchub-1",
                                                    error="boom"))
    rc = cli.main(["spawn", "srv1"])
    err = capsys.readouterr().err
    assert rc == 1 and "boom" in err


def test_cmd_spawn_rejects_bad_or_taken_name(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "exp1\n", "")})
    monkeypatch.setattr(cli, "_make_remote", lambda host: fake)
    assert cli.main(["spawn", "srv1", "--name", "bad name!"]) == 1
    assert "세션명" in capsys.readouterr().err
    assert cli.main(["spawn", "srv1", "--name", "exp1"]) == 1
    assert "이미 존재" in capsys.readouterr().err


def test_cmd_spawn_unknown_server(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    monkeypatch.setattr(cli, "load_config", _spawn_cfg)
    rc = cli.main(["spawn", "srv9"])
    assert rc == 1
    assert "알 수 없는 서버" in capsys.readouterr().err
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k spawn -q`
Expected: FAIL — argparse가 spawn을 모름(SystemExit 2) 또는 ImportError.

- [ ] **Step 3: cmd_spawn 구현**

`src/cchub/cli.py` 상단에 `import re` 추가 (이미 있으면 생략 — `grep -n "^import re" src/cchub/cli.py`).

`cmd_doctor` 아래에 추가:

```python
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def cmd_spawn(args) -> int:
    cfg = load_config()
    s = cfg.servers.get(args.server)
    if not s:
        print(f"알 수 없는 서버: {args.server} (설정: {', '.join(cfg.servers)})",
              file=sys.stderr)
        return 1
    remote = _make_remote(s.host)
    if args.name:
        if not _SESSION_NAME_RE.fullmatch(args.name):
            print("세션명은 [A-Za-z0-9_-]+ 만 허용됩니다", file=sys.stderr)
            return 1
        if args.name in tmux.list_session_names(remote):
            print(f"이미 존재하는 세션명: {args.name}", file=sys.stderr)
            return 1
    launch = "claude" if args.safe else "claude --dangerously-skip-permissions"
    res = tmux.spawn_session(remote, args.cwd, launch, name=args.name,
                             prompt=args.prompt)
    if not res.ok:
        print(f"{args.server}: 세션 생성 실패 — {res.error}", file=sys.stderr)
        return 1
    print(f"{args.server}: 세션 {res.name} 생성됨 (cwd={args.cwd}) "
          f"— tmux attach -t {res.name}")
    if res.prompt_sent is False:
        print("주의: 초기 프롬프트 미전달 — claude 기동 확인 실패, "
              "attach 후 직접 입력하세요", file=sys.stderr)
    return 0
```

- [ ] **Step 4: argparse + handler 등록**

`main()`에서 `sub.add_parser("doctor", ...)` 아래에:

```python
    p = sub.add_parser("spawn", help="원격 tmux 세션 생성 + claude 기동")
    p.add_argument("server")
    p.add_argument("cwd", nargs="?", default="~", help="작업 디렉토리 (기본 ~)")
    p.add_argument("--name", default=None, help="tmux 세션명 (기본: cchub-<n> 자동)")
    p.add_argument("--safe", action="store_true",
                   help="--dangerously-skip-permissions 없이 실행")
    p.add_argument("--prompt", default=None, help="claude 기동 후 주입할 초기 프롬프트")
```

handler dict에 `"spawn": cmd_spawn,` 추가.

- [ ] **Step 5: 통과 확인 + 전체 + 커밋**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k spawn -q` — Expected: 6 passed.
Run: `.venv/bin/python -m pytest -q` — Expected: 181 passed, 4 skipped (전체 기준 보고).

```bash
git add src/cchub/cli.py tests/test_cli.py
git commit -m "feat: cchub spawn CLI (--name/--safe/--prompt, attach 안내)"
```

---

### Task 4: TUI — 서버 노드 data 수정 + `N` 키 + SpawnScreen + 워커

**Files:**
- Modify: `src/cchub/tui/app.py` (BINDINGS, apply_snapshots, on_tree_node_selected, action_spawn, spawn_worker, import)
- Modify: `src/cchub/tui/screens.py` (SpawnScreen, Static import)
- Test: `tests/test_screens.py`

**Interfaces:**
- Consumes: `tmux.spawn_session` (Task 2); 기존 `LiveSession`(app.py에 import 됨), 워커 규약, `SearchScreen` 모달 패턴.
- Produces: `SpawnScreen(server: str)` — `ModalScreen[tuple[str, str] | None]`, dismiss 값 `(cwd, prompt)` (prompt는 빈 문자열 가능); `CchubApp.action_spawn()`, `CchubApp.spawn_worker(server: str, cwd: str, prompt: str | None)`; 서버 트리 노드의 `data`가 서버명 문자열.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_screens.py`에 추가 (파일 상단 import에 `SpawnScreen` 추가: `from cchub.tui.screens import SearchScreen, HistoryScreen, SkillsScreen, SpawnScreen`; `Tree`도 필요: `from textual.widgets import DataTable, Input, RichLog, Tree`):

```python
async def test_server_node_select_does_not_pollute_selected(tmp_path):
    """서버 노드 선택이 self.selected를 문자열로 오염시키지 않는다 (회귀)."""
    from types import SimpleNamespace
    from cchub.sessions import LiveSession
    from cchub.tui.data import ServerSnapshot

    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        ls = LiveSession(server="srv1", number=1, pane_id="%5", location="main:0.0",
                         cwd="/home/u/proj", project="-home-u-proj",
                         session_id="s-1", title="", state="idle")
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[ls])})
        app.selected = ls
        app.on_tree_node_selected(SimpleNamespace(node=SimpleNamespace(data="srv1")))
        assert app.selected is ls


async def test_N_spawn_flow_from_server_node(tmp_path):
    from cchub.config import ServerConfig
    from cchub.tui.data import ServerSnapshot

    app = make_indexed_app(tmp_path)
    calls = []
    async with app.run_test() as pilot:
        app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[])})
        tree = app.query_one("#tree", Tree)
        tree.select_node(tree.root.children[0])          # 서버 노드
        app.spawn_worker = lambda server, cwd, prompt: calls.append((server, cwd, prompt))
        await pilot.press("N")
        assert isinstance(app.screen, SpawnScreen)
        app.screen.query_one("#spawn-cwd", Input).value = "~/proj"
        await pilot.press("enter")                       # cwd 제출 → 프롬프트로 포커스
        pr = app.screen.query_one("#spawn-prompt", Input)
        assert pr.has_focus
        pr.value = "버그 고쳐줘"
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, SpawnScreen)
        assert calls == [("srv1", "~/proj", "버그 고쳐줘")]


async def test_N_spawn_empty_prompt_becomes_none(tmp_path):
    from cchub.config import ServerConfig
    from cchub.tui.data import ServerSnapshot

    app = make_indexed_app(tmp_path)
    calls = []
    async with app.run_test() as pilot:
        app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[])})
        tree = app.query_one("#tree", Tree)
        tree.select_node(tree.root.children[0])
        app.spawn_worker = lambda server, cwd, prompt: calls.append((server, cwd, prompt))
        await pilot.press("N")
        await pilot.press("enter")                       # cwd 기본 ~ 제출
        await pilot.press("enter")                       # 빈 프롬프트 제출
        await pilot.pause()
        assert calls == [("srv1", "~", None)]


async def test_N_without_server_context_warns(tmp_path):
    app = make_indexed_app(tmp_path)
    notes = []
    async with app.run_test() as pilot:
        app.notify = lambda msg, **kw: notes.append(msg)
        await pilot.press("N")
        assert not isinstance(app.screen, SpawnScreen)
        assert any("서버" in n for n in notes)
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_screens.py -k "spawn or pollute" -q`
Expected: FAIL — ImportError (`SpawnScreen` 없음).

- [ ] **Step 3: screens.py에 SpawnScreen 추가**

`src/cchub/tui/screens.py`:
- import 수정: `from textual.widgets import DataTable, Input, Static`
- 파일 끝에 추가:

```python
class SpawnScreen(ModalScreen[tuple[str, str] | None]):
    """새 원격 claude 세션 생성 입력. (cwd, prompt)를 dismiss, 취소 시 None."""

    CSS = """
    SpawnScreen { align: center middle; }
    #spawn-box { width: 70%; height: auto; background: $panel;
                 border: solid $primary; padding: 1 2; }
    """
    BINDINGS = [Binding("escape", "close", "닫기")]

    def __init__(self, server: str):
        super().__init__()
        self.server = server

    def compose(self) -> ComposeResult:
        with Vertical(id="spawn-box"):
            yield Static(f"[{self.server}] 새 claude 세션", id="spawn-title")
            yield Input(value="~", placeholder="작업 디렉토리 (기본 ~)", id="spawn-cwd")
            yield Input(placeholder="초기 프롬프트 (없으면 비워두고 Enter)",
                        id="spawn-prompt")

    def on_mount(self) -> None:
        self.query_one("#spawn-cwd", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()   # 메인 화면 #prompt로 버블링 금지 (do_send 오발사 방지)
        if event.input.id == "spawn-cwd":
            self.query_one("#spawn-prompt", Input).focus()
            return
        cwd = self.query_one("#spawn-cwd", Input).value.strip() or "~"
        self.dismiss((cwd, self.query_one("#spawn-prompt", Input).value.strip()))

    def action_close(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: app.py 수정**

`src/cchub/tui/app.py`:

1. import: `from cchub.tui.screens import SearchScreen, HistoryScreen, SkillsScreen, SpawnScreen`
2. BINDINGS에 추가 (o 바인딩 아래): `Binding("N", "spawn", "새세션"),`
3. `apply_snapshots`의 서버 노드 생성에 data 부여:

```python
            node = tree.root.add(label, expand=True, data=name)
```

4. `on_tree_node_selected` 가드 교체:

```python
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if isinstance(event.node.data, LiveSession):
            self.selected = event.node.data
        self.show_detail()
```

5. `action_switch_pane` 아래에 추가:

```python
    def action_spawn(self) -> None:
        node = self.query_one("#tree", Tree).cursor_node
        data = node.data if node else None
        if isinstance(data, LiveSession):
            server = data.server
        elif isinstance(data, str):
            server = data
        else:
            self.notify("서버를 선택하세요", severity="warning")
            return

        def _submitted(result: tuple[str, str] | None) -> None:
            if result:
                self.spawn_worker(server, result[0], result[1] or None)

        self.push_screen(SpawnScreen(server), _submitted)

    @work(thread=True, exclusive=True, group="spawn", exit_on_error=False)
    def spawn_worker(self, server: str, cwd: str, prompt: str | None) -> None:
        worker = get_current_worker()
        s = self.cfg.servers.get(server)
        if s is None:
            return
        try:
            res = tmux.spawn_session(
                self.remote_factory(s.host), cwd,
                "claude --dangerously-skip-permissions", prompt=prompt)
        except Exception as e:  # noqa: BLE001 - 원격 실패가 앱을 죽이면 안 됨
            if not worker.is_cancelled:
                self.call_from_thread(self.notify, f"세션 생성 오류: {e}",
                                      severity="error")
            return
        if worker.is_cancelled:
            return
        if not res.ok:
            self.call_from_thread(self.notify, f"세션 생성 실패: {res.error}",
                                  severity="error")
            return
        msg = f"{server}: 세션 {res.name} 생성됨"
        if res.prompt_sent is False:
            msg += " (초기 프롬프트 미전달)"
        self.call_from_thread(self.notify, msg)
        self.call_from_thread(self.action_refresh)
```

- [ ] **Step 5: 통과 확인 (신규 + 기존 TUI 회귀)**

Run: `.venv/bin/python -m pytest tests/test_screens.py tests/test_tui_app.py -q`
Expected: 전부 passed. 특히 `test_modal_enter_never_sends_to_session`(기존 Critical 회귀 테스트)이 여전히 통과해야 한다.

- [ ] **Step 6: 전체 스위트 + 커밋**

Run: `.venv/bin/python -m pytest -q` — Expected: 185 passed, 4 skipped (전체 기준 보고).

```bash
git add src/cchub/tui/app.py src/cchub/tui/screens.py tests/test_screens.py
git commit -m "feat: TUI N 키 — SpawnScreen 모달로 원격 세션 생성 (+서버 노드 data 회귀 수정)"
```

---

### Task 5: README 상세 문서화

**Files:**
- Modify: `README.md`

**Interfaces:** 없음 (문서만).

- [ ] **Step 1: 빠른 시작에 spawn 추가**

`## 빠른 시작` 코드블록의 `cchub doctor` 줄 아래에:

```
cchub spawn srv1 ~/proj    # 원격에 새 tmux 세션 + claude 기동
```

- [ ] **Step 2: CLI 사용법에 절 추가**

`### 연결 문제 진단` 절 아래에 새 절 추가 (기존 절 스타일에 맞춤):

````markdown
### 원격 세션 생성 (spawn)

```bash
cchub spawn srv1                          # $HOME에서 detached tmux 세션 + claude 기동
cchub spawn srv1 ~/proj                   # 작업 디렉토리 지정 (~는 원격 $HOME 기준)
cchub spawn srv1 ~/proj --name exp1       # tmux 세션명 지정 (기본: cchub-1, cchub-2, …)
cchub spawn srv1 ~/proj --safe            # --dangerously-skip-permissions 없이 실행
cchub spawn srv1 ~/proj --prompt "테스트 전부 돌리고 실패 원인 정리해줘"
```

- 기본으로 `claude --dangerously-skip-permissions`를 실행합니다 — 원격에서 무인
  자동화가 목적이므로 권한 확인 프롬프트를 건너뜁니다. 신뢰할 수 없는 작업이면
  `--safe`로 일반 `claude`를 실행하세요.
- `--prompt`를 주면 claude가 뜰 때까지(pane 명령이 claude/node가 될 때까지, 최대
  ~10초) 폴링한 뒤 주입합니다. 시간 안에 못 뜨면 세션은 만들어진 채 경고만 출력
  합니다 — `tmux attach` 후 직접 입력하세요.
- 생성 확인은 tmux 세션까지입니다. 서버에서 직접 보려면:
  `ssh <서버> -t tmux attach -t <세션명>`.
- 세션명은 `[A-Za-z0-9_-]+`만 허용되며, 이미 있는 이름은 거부됩니다.
- TUI에서는 `N` 키 — 트리에서 서버(또는 그 서버의 세션)를 선택한 뒤 누르면 작업
  디렉토리와 초기 프롬프트를 입력하는 모달이 뜹니다.
````

- [ ] **Step 3: TUI 키맵 표에 N 추가**

README의 TUI 키 안내(기존 `x`/`|`/`o` 키가 설명된 표 또는 목록 — `grep -n "패널전환\|분할" README.md`로 위치 확인)에 같은 형식으로 `N` = 새 세션 생성(서버 선택 필요) 항목 추가.

- [ ] **Step 4: 전체 테스트 + 수동 확인 + 커밋**

Run: `.venv/bin/python -m pytest -q` — Expected: 185 passed, 4 skipped.
Run: `.venv/bin/cchub spawn --help` — Expected: 사용법에 server/cwd/--name/--safe/--prompt 표시.

```bash
git add README.md
git commit -m "docs: cchub spawn 상세 사용법 README 반영 (CLI·TUI N 키)"
```

---

## Self-Review

- **Spec coverage:** §0 렌더 추출(Task 1), §1 tmux 계층(Task 2), §2 CLI(Task 3), §3 TUI+서버노드 수정(Task 4), §4 보안(각 태스크의 shlex/-l --/검증), §5 테스트(각 태스크 TDD), §6 README(Task 5) — 전부 커버.
- **Placeholder scan:** 모든 코드 스텝에 실제 코드·명령·기대값 포함. Task 5 Step 3의 "형식 확인 후 추가"는 README 실물 의존이라 grep 지시로 대체.
- **Type consistency:** `SpawnResult(ok, name, prompt_sent, error)`, `spawn_session(remote, cwd, launch_cmd, name, prompt, poll_attempts, poll_interval, sleep)`, `list_session_names`, `render_remote_path`, `SpawnScreen(server)`→`(cwd, prompt)`, `spawn_worker(server, cwd, prompt)` — Task 간 일치. CLI 스파이 `fake_spawn(remote, cwd, launch_cmd, name=None, prompt=None)`도 실호출 시그니처(위치 3개+키워드 2개)와 일치.
- **테스트 수 산정:** T1 +1=169, T2 +6=175, T3 +6=181, T4 +4=185. (실행 중 어긋나면 전체 수치 기준으로 보고.)
