# cchub doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cchub doctor` 명령을 추가해 서버별 SSH·rsync·projects·tmux를 실제 점검하고 원인별 힌트를 출력하며, sync 실패(CLI·TUI)가 doctor를 가리키게 한다.

**Architecture:** 진단 로직은 `Remote` 주입식 순수 모듈(`doctor.py`)로 분리해 FakeRemote로 테스트한다. CLI는 `cmd_doctor`가 포매팅·exit code를 담당하고, `cmd_sync`는 실패 시 안내 한 줄을 덧붙인다. TUI는 `apply_snapshots`에서 에러 변경을 감지해 1회만 notify한다.

**Tech Stack:** Python 3.13, argparse, dataclass, pytest. 기존 `cchub.ssh.Remote` 인터페이스와 `tests/conftest.py::FakeRemote` 재사용.

## Global Constraints

- Python ≥ 3.13, 외부 의존성 추가 금지 (stdlib만).
- Remote 명령은 `remote.run(argv)` 형태로만; 틸드(`~`) 확장이 필요하면 `["sh", "-c", ...]`로 감싸고 `"$HOME"`을 쓴다 (skills.py `_render_root` 패턴).
- transcript/원격 호출은 관대하게 — 예외를 UI로 전파하지 않는다.
- 모든 사용자 대면 문자열은 한국어.
- 테스트는 `.venv/bin/python -m pytest`로 실행.

---

### Task 1: 진단 로직 모듈 `doctor.py`

**Files:**
- Create: `src/cchub/doctor.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `cchub.ssh.Remote`, `cchub.ssh.RunResult`; `tests/conftest.py::FakeRemote` (fixture는 conftest에 이미 있음, import는 `from conftest import FakeRemote`가 아니라 기존 테스트 방식대로 — 아래 테스트 코드 참고).
- Produces:
  - `@dataclass CheckResult(name: str, status: str, detail: str = "", hint: str = "")` — status ∈ {"ok","fail","warn","skip"}
  - `def diagnose_server(remote: Remote, name: str, host: str, claude_dir: str) -> list[CheckResult]`
  - `def _classify_ssh_error(stderr: str) -> str`

- [ ] **Step 1: 기존 FakeRemote import 방식 확인**

`tests/test_skills.py` 상단을 보고 FakeRemote를 어떻게 import하는지 그대로 따른다. (conftest.py에 정의됨 — 대부분 `from conftest import FakeRemote` 또는 fixture. 아래 테스트는 직접 import를 가정하며, 기존 테스트가 다른 방식이면 그 방식에 맞춘다.)

Run: `grep -n "FakeRemote" tests/test_skills.py tests/test_sessions.py | head`
Expected: import 패턴 확인 (예: `from conftest import FakeRemote`).

- [ ] **Step 2: `_classify_ssh_error` 실패 테스트 작성**

`tests/test_doctor.py`:

```python
from cchub.doctor import CheckResult, diagnose_server, _classify_ssh_error
from cchub.ssh import RunResult
from conftest import FakeRemote


def test_classify_ssh_error_permission_denied():
    hint = _classify_ssh_error("Permission denied (publickey,password).")
    assert "ssh-copy-id" in hint


def test_classify_ssh_error_connection_refused():
    hint = _classify_ssh_error("ssh: connect to host x port 22: Connection refused")
    assert "포트" in hint


def test_classify_ssh_error_timed_out():
    hint = _classify_ssh_error("ssh: connect to host x port 22: Operation timed out")
    assert "포트" in hint


def test_classify_ssh_error_resolve():
    hint = _classify_ssh_error("ssh: Could not resolve hostname foo: nodename nor servname")
    assert "host" in hint or "alias" in hint


def test_classify_ssh_error_generic():
    hint = _classify_ssh_error("some unexpected error")
    assert "BatchMode" in hint
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.doctor'`

- [ ] **Step 4: `doctor.py` 최소 구현 (CheckResult + _classify_ssh_error)**

```python
from __future__ import annotations

from dataclasses import dataclass

from cchub.ssh import Remote


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "fail" | "warn" | "skip"
    detail: str = ""
    hint: str = ""


def _classify_ssh_error(stderr: str) -> str:
    s = stderr.lower()
    if "permission denied" in s:
        return ("키 기반(비밀번호 없는) 로그인이 필요합니다: "
                "ssh-copy-id <host> 로 공개키를 등록하세요")
    if "connection refused" in s or "timed out" in s or "timeout" in s:
        return ("호스트/포트를 확인하세요 — 사내 서버는 22가 아닐 수 있습니다. "
                "~/.ssh/config에 Port를 지정한 Host alias를 만들어 host에 쓰세요")
    if "could not resolve" in s or "name or service not known" in s:
        return ("host 문자열 또는 ~/.ssh/config alias를 확인하세요")
    return "직접 확인: ssh -o BatchMode=yes <host> true"
```

- [ ] **Step 5: `_classify_ssh_error` 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: 5 passed.

- [ ] **Step 6: `diagnose_server` 동작 테스트 작성**

`tests/test_doctor.py`에 추가:

```python
def _names_status(results):
    return {r.name: r.status for r in results}


def test_diagnose_all_ok():
    remote = FakeRemote()  # 모든 run rc=0
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "ok"
    assert st["원격 rsync"] == "ok"
    assert st["projects 디렉터리"] == "ok"
    assert st["tmux 서버"] == "ok"


def test_diagnose_ssh_fail_skips_rest():
    remote = FakeRemote({"true": RunResult(255, "", "Connection refused")})
    results = diagnose_server(remote, "srv1", "cigar", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "fail"
    assert st["원격 rsync"] == "skip"
    assert st["projects 디렉터리"] == "skip"
    assert st["tmux 서버"] == "skip"
    ssh = next(r for r in results if r.name == "ssh 접속")
    assert "포트" in ssh.hint  # Connection refused → 포트 힌트


def test_diagnose_rsync_missing():
    remote = FakeRemote({("rsync", "--version"): RunResult(127, "", "command not found")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["ssh 접속"] == "ok"
    assert st["원격 rsync"] == "fail"


def test_diagnose_projects_missing():
    # sh -c 로 test -d 를 실행 → rc=1 이면 projects 없음
    remote = FakeRemote({"sh": RunResult(1, "", "")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    st = _names_status(results)
    assert st["projects 디렉터리"] == "fail"


def test_diagnose_tmux_missing_is_warn():
    remote = FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running on ...")})
    results = diagnose_server(remote, "srv1", "sudal", "~/.claude")
    tmux = next(r for r in results if r.name == "tmux 서버")
    assert tmux.status == "warn"
    assert "전송" in tmux.hint or "감지" in tmux.hint
```

주의: FakeRemote는 `(argv[0], argv[1])` 2-튜플 키 또는 `argv[0]` 단일 키로 매칭한다(conftest 참고). `test -d`는 `["sh","-c",script]`로 호출하므로 키가 `"sh"` 또는 `("sh","-c")`가 된다 — 위 테스트는 `"sh"` 단일 키를 쓴다. `diagnose_server` 구현에서 projects 체크를 반드시 `argv[0]=="sh"`로 호출할 것.

- [ ] **Step 7: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: FAIL — `diagnose_server` not defined 또는 AttributeError.

- [ ] **Step 8: `diagnose_server` 구현**

`doctor.py`에 추가:

```python
import shlex


def _projects_check_script(claude_dir: str) -> str:
    # claude_dir 의 ~ 는 원격 $HOME 으로 확장
    if claude_dir == "~":
        base = '"$HOME"'
    elif claude_dir.startswith("~/"):
        base = '"$HOME"/' + shlex.quote(claude_dir[2:])
    else:
        base = shlex.quote(claude_dir)
    return f'test -d {base}/projects'


def diagnose_server(remote: Remote, name: str, host: str,
                    claude_dir: str) -> list[CheckResult]:
    results: list[CheckResult] = []

    ssh = remote.run(["true"], timeout=8)
    if ssh.rc != 0:
        detail = (ssh.err.strip().splitlines() or [""])[0][:120]
        results.append(CheckResult("ssh 접속", "fail", detail,
                                   _classify_ssh_error(ssh.err)))
        for n in ("원격 rsync", "projects 디렉터리", "tmux 서버"):
            results.append(CheckResult(n, "skip", "ssh 실패로 건너뜀"))
        return results
    results.append(CheckResult("ssh 접속", "ok"))

    rsync = remote.run(["rsync", "--version"], timeout=8)
    if rsync.rc != 0:
        results.append(CheckResult("원격 rsync", "fail",
                                   (rsync.err.strip().splitlines() or [""])[0][:120],
                                   "서버에 rsync를 설치하세요"))
    else:
        results.append(CheckResult("원격 rsync", "ok"))

    proj = remote.run(["sh", "-c", _projects_check_script(claude_dir)], timeout=8)
    if proj.rc != 0:
        results.append(CheckResult("projects 디렉터리", "fail",
                                   f"{claude_dir}/projects 없음",
                                   "이 서버에서 Claude Code 실행 이력이 없거나 "
                                   "claude_dir 설정이 실제 경로와 다릅니다"))
    else:
        results.append(CheckResult("projects 디렉터리", "ok"))

    tmux = remote.run(["tmux", "list-sessions"], timeout=8)
    if tmux.rc != 0:
        err = (tmux.err + tmux.out).lower()
        if "command not found" in err or "not found" in err:
            hint = "tmux 미설치 — 세션 전송/상태 감지에만 필요합니다"
        else:
            hint = "tmux 서버가 떠 있지 않음 — 세션 전송/상태 감지에만 필요합니다"
        results.append(CheckResult("tmux 서버", "warn",
                                   (tmux.err.strip().splitlines() or [""])[0][:120], hint))
    else:
        results.append(CheckResult("tmux 서버", "ok"))

    return results
```

- [ ] **Step 9: 전체 doctor 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_doctor.py -q`
Expected: 10 passed.

- [ ] **Step 10: 커밋**

```bash
git add src/cchub/doctor.py tests/test_doctor.py
git commit -m "feat: doctor.py — 서버별 SSH·rsync·projects·tmux 진단 로직"
```

---

### Task 2: CLI `cchub doctor` 명령

**Files:**
- Modify: `src/cchub/cli.py` (import, `cmd_doctor`, argparse 등록, handler dict)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cchub.doctor.diagnose_server`, `CheckResult` (Task 1); `_ctx()` 또는 `load_config()`+`_make_remote` (cli.py 기존).
- Produces: `def cmd_doctor(_args) -> int`. exit code: fail 존재 시 1, 아니면 0. 서버 0개 시 안내 후 1.

- [ ] **Step 1: cmd_doctor 테스트 작성**

`tests/test_cli.py`에 추가. 기존 test_cli.py의 monkeypatch/capsys 패턴을 먼저 확인한다.

Run: `grep -n "def test_\|monkeypatch\|capsys\|_make_remote\|load_config" tests/test_cli.py | head -20`

그 패턴에 맞춰 아래를 추가 (아래는 `cli._make_remote`를 FakeRemote로 치환하고 `cli.load_config`를 스텁하는 방식):

```python
from cchub import doctor as doctor_mod


def test_cmd_doctor_all_ok(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="sudal")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote", lambda host: FakeRemote())
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "[srv1]" in out and "ssh 접속" in out and "✓" in out


def test_cmd_doctor_fail_exit_1(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    from cchub.ssh import RunResult
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="cigar")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote",
                        lambda host: FakeRemote({"true": RunResult(255, "", "Connection refused")}))
    rc = cli.cmd_doctor(None)
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗" in out and "포트" in out


def test_cmd_doctor_warn_only_exit_0(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config, ServerConfig
    from cchub.ssh import RunResult
    cfg = Config(sync_interval=30, stats_interval=2,
                 servers={"srv1": ServerConfig(name="srv1", host="sudal")})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    monkeypatch.setattr(cli, "_make_remote",
                        lambda host: FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running")}))
    rc = cli.cmd_doctor(None)
    assert rc == 0
    assert "⚠" in capsys.readouterr().out


def test_cmd_doctor_no_servers(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.config import Config
    cfg = Config(sync_interval=30, stats_interval=2, servers={})
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.cmd_doctor(None)
    assert rc == 1
    assert "servers" in capsys.readouterr().out
```

주의: `Config`/`ServerConfig` 생성 시그니처는 `src/cchub/config.py`를 확인해 맞춘다 (필드명·기본값). 위와 다르면 실제 시그니처로 고친다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k doctor -q`
Expected: FAIL — `cmd_doctor` not defined.

- [ ] **Step 3: cmd_doctor 구현**

`src/cchub/cli.py` — import에 추가:

```python
from cchub.doctor import diagnose_server
```

`cmd_sync` 근처(또는 파일 내 적당한 위치)에 추가:

```python
_DOCTOR_MARK = {"ok": "✓", "fail": "✗", "warn": "⚠", "skip": "-"}


def cmd_doctor(_args) -> int:
    cfg = load_config()
    if not cfg.servers:
        print("config.toml에 [servers.<이름>] 항목을 추가하세요", file=sys.stderr)
        return 1
    any_fail = False
    for name, s in cfg.servers.items():
        print(f"[{name}] host={s.host}")
        results = diagnose_server(_make_remote(s.host), name, s.host, s.claude_dir)
        for r in results:
            mark = _DOCTOR_MARK.get(r.status, "?")
            line = f"  {mark} {r.name}"
            if r.detail:
                line += f" — {r.detail}"
            print(line)
            if r.hint and r.status in ("fail", "warn"):
                print(f"      → {r.hint}")
            if r.status == "fail":
                any_fail = True
    return 1 if any_fail else 0
```

- [ ] **Step 4: argparse 등록 + handler dict**

`src/cchub/cli.py::main` 안, `sub.add_parser("sync", ...)` 아래에:

```python
    sub.add_parser("doctor", help="서버별 연결 진단 (SSH·rsync·projects·tmux)")
```

handler dict에 `"doctor": cmd_doctor,` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k doctor -q`
Expected: 4 passed.

- [ ] **Step 6: 커밋**

```bash
git add src/cchub/cli.py tests/test_cli.py
git commit -m "feat: cchub doctor CLI 명령 (체크리스트 출력·exit code)"
```

---

### Task 3: sync 실패 시 doctor 안내 (CLI)

**Files:**
- Modify: `src/cchub/cli.py::cmd_sync`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cmd_sync` 기존 구조 (SyncReport 리스트 순회).
- Produces: 실패 1건 이상이면 stderr에 `→ cchub doctor 로 서버별 진단을 실행해 보세요` 한 줄.

- [ ] **Step 1: 테스트 작성**

`tests/test_cli.py`에 추가. 기존 sync 테스트(`_sync_all` 스텁 방식)를 먼저 확인한다.

Run: `grep -n "cmd_sync\|_sync_all\|SyncReport" tests/test_cli.py`

그 패턴에 맞춰:

```python
def test_cmd_sync_failure_suggests_doctor(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.sync import SyncReport
    monkeypatch.setattr(cli, "_ctx", lambda: (object(), tmp_path, object()))
    monkeypatch.setattr(cli, "_sync_all",
                        lambda cfg, root, index: [SyncReport(server="srv1", ok=False, error="boom")])
    rc = cli.cmd_sync(None)
    err = capsys.readouterr().err
    assert rc == 1
    assert "cchub doctor" in err


def test_cmd_sync_success_no_doctor_hint(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    from cchub import cli
    from cchub.sync import SyncReport
    monkeypatch.setattr(cli, "_ctx", lambda: (object(), tmp_path, object()))
    monkeypatch.setattr(cli, "_sync_all",
                        lambda cfg, root, index: [SyncReport(server="srv1", ok=True, files=1, events=2)])
    rc = cli.cmd_sync(None)
    err = capsys.readouterr().err
    assert rc == 0
    assert "cchub doctor" not in err
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "sync" -q`
Expected: `test_cmd_sync_failure_suggests_doctor` FAIL (안내 줄 없음).

- [ ] **Step 3: cmd_sync 수정**

`src/cchub/cli.py::cmd_sync`의 `return 0 if ok else 1` 직전에:

```python
    if not ok:
        print("→ cchub doctor 로 서버별 진단을 실행해 보세요", file=sys.stderr)
    return 0 if ok else 1
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "sync" -q`
Expected: 모두 passed.

- [ ] **Step 5: 커밋**

```bash
git add src/cchub/cli.py tests/test_cli.py
git commit -m "feat: sync 실패 시 cchub doctor 안내 (CLI)"
```

---

### Task 4: TUI 동기화 실패 시 doctor notify (변경 감지 1회)

**Files:**
- Modify: `src/cchub/tui/app.py` (`on_mount` 상태 초기화, `apply_snapshots`)
- Test: `tests/test_tui_app.py`

**Interfaces:**
- Consumes: `apply_snapshots(snaps: dict[str, ServerSnapshot])` 기존; `ServerSnapshot.error`.
- Produces: 새 인스턴스 속성 `self._last_errors: dict[str, str]`. 에러가 새로 생기거나 문자열이 바뀐 서버에 대해서만 `self.notify(...)` 1회.

- [ ] **Step 1: 기존 tui_app 테스트 패턴 확인**

Run: `grep -n "def test_\|apply_snapshots\|notify\|run_test\|pilot\|App(" tests/test_tui_app.py | head -20`

`apply_snapshots`를 직접 호출하는 단위 테스트가 가능한지(위젯 마운트 필요 여부) 확인. `apply_snapshots`는 `query_one("#tree")`에 의존하므로 Textual `run_test()` pilot 컨텍스트가 필요하다. 기존 테스트에서 pilot 사용 예를 그대로 따른다.

- [ ] **Step 2: 테스트 작성**

`tests/test_tui_app.py`에 추가 (기존 pilot 헬퍼/fixture 재사용; notify를 가로채기 위해 monkeypatch). 기존 파일의 앱 생성·pilot 패턴에 맞춰 조정:

`make_app`은 기존 test_tui_app.py에 있는 모듈 레벨 함수 `make_app(tmp_path) -> CchubApp`이다. 데코레이터는 기존 async 테스트와 동일하게 맞춘다 (`@pytest.mark.asyncio` — 기존 파일 확인).

```python
async def test_apply_snapshots_notifies_once_per_error(monkeypatch, tmp_path):
    from cchub.tui.data import ServerSnapshot
    app = make_app(tmp_path)
    notes = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        bad = {"srv1": ServerSnapshot(server="srv1", sessions=[], error="ssh refused")}
        app.apply_snapshots(bad)
        app.apply_snapshots(bad)  # 동일 에러 재갱신 — 추가 알림 없어야
        assert sum("srv1" in n and "doctor" in n for n in notes) == 1
        # 에러 사라졌다가 다시 발생하면 재알림
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[])})
        app.apply_snapshots(bad)
        assert sum("srv1" in n and "doctor" in n for n in notes) == 2
```

주의: `make_app`/pilot 진입 방식은 기존 test_tui_app.py에 반드시 존재하는 형태로 대체한다. notify 시그니처는 `notify(message, *, severity=...)`이므로 lambda가 `**kw`를 받아야 한다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_tui_app.py -k notif -q`
Expected: FAIL (두 번째/네 번째 호출에서 카운트 불일치 — 매번 알리거나 아예 안 알림).

- [ ] **Step 4: app.py 구현**

`on_mount`의 `self.snapshots = {}` 근처에:

```python
        self._last_errors: dict[str, str] = {}
```

`apply_snapshots` 본문에서, 트리 갱신 로직은 그대로 두고 서버 순회 중(또는 끝에) 에러 변경 감지를 추가:

```python
    def apply_snapshots(self, snaps: dict[str, ServerSnapshot]) -> None:
        self.snapshots = snaps
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.expand()
        for name, s in snaps.items():
            label = f"{name}  ⚠ {s.error}" if s.error else name
            node = tree.root.add(label, expand=True)
            for ls in s.sessions:
                mark = self._STATE_MARK.get(ls.state, "?")
                node.add_leaf(f"{ls.number} {mark} {ls.project}  {ls.title}", data=ls)
        self._reconcile_selection(tree)
        self._notify_new_errors(snaps)

    def _notify_new_errors(self, snaps: dict[str, ServerSnapshot]) -> None:
        for name, s in snaps.items():
            prev = self._last_errors.get(name, "")
            if s.error and s.error != prev:
                self.notify(f"{name} 동기화 실패 — cchub doctor로 진단해 보세요",
                            severity="error")
            if s.error:
                self._last_errors[name] = s.error
            else:
                self._last_errors.pop(name, None)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_tui_app.py -k notif -q`
Expected: passed.

- [ ] **Step 6: 커밋**

```bash
git add src/cchub/tui/app.py tests/test_tui_app.py
git commit -m "feat: TUI 동기화 실패 시 doctor 안내 notify (에러 변경 감지 1회)"
```

---

### Task 5: README 문서화

**Files:**
- Modify: `README.md`

**Interfaces:** 없음 (문서만).

- [ ] **Step 1: 빠른 시작에 doctor 추가**

`README.md`의 빠른 시작 블록(`cchub init` … `cchub sync` 부근)에 한 줄 추가:

```
cchub doctor               # (문제 시) 서버별 연결 진단
```

- [ ] **Step 2: CLI 사용법에 doctor 절 추가**

`### 세션 확인·제어` 근처 또는 별도로, sync 설명 아래:

```markdown
### 연결 문제 진단

\```bash
cchub doctor                      # 서버별 SSH·rsync·projects·tmux 점검, 원인별 힌트
\```

sync가 실패하면 `cchub doctor`가 서버별로 무엇이 막혔는지(키 기반 SSH 미설정,
포트 불일치, rsync 미설치, claude_dir 경로 등) ✓/✗/⚠로 보여주고 해결 힌트를 냅니다.
```

(위 `\``` 는 실제로는 백틱 3개 — README에 코드펜스로 넣는다.)

- [ ] **Step 3: 전체 테스트 + 수동 확인**

Run: `.venv/bin/python -m pytest -q`
Expected: 전체 passed (기존 151 + 신규).

Run: `.venv/bin/cchub doctor`
Expected: srv1/srv2 실제 체크리스트 출력 (이 머신은 sudal/cigar 접속 가능 → ✓ 위주).

- [ ] **Step 4: 커밋**

```bash
git add README.md
git commit -m "docs: cchub doctor 사용법 README 반영"
```

---

## Self-Review

- **Spec coverage:** doctor 명령(Task 1·2), 4체크·ssh-skip·힌트 분류(Task 1), tmux=warn(Task 1), CLI 실패 연동(Task 3), TUI 실패 연동 1회 알림(Task 4), README(Task 5) — 모두 태스크로 커버.
- **Placeholder scan:** 각 스텝에 실제 코드/명령/기대출력 포함. 단, Config/ServerConfig 시그니처·FakeRemote import·기존 tui_app pilot 헬퍼는 "기존 파일 확인 후 맞춤"으로 지시 — 리포지토리 실제 형태에 의존하므로 의도적.
- **Type consistency:** `CheckResult`, `diagnose_server`, `_classify_ssh_error`, `_DOCTOR_MARK`, `_notify_new_errors`, `_last_errors` 이름이 태스크 간 일치.
- **범위 외 준수:** 로컬 점검·ControlPath 사전점검·자동수리 없음 (spec과 일치).
