# cchub M3 (이력·검색 + 결과 수집·종합·중계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TUI 이력/검색 화면(h, /), 실험 결과 수집(r)·종합 브리핑(A)·서버 간 파일 중계(`cchub push`)를 구현하고, M2에서 이월된 세션 매칭·전송 검증 항목을 정리한다.

**Architecture:** M1/M2 코어 위에 쌓는다. 결과 수집·중계는 `Remote`에 fetch/push(rsync)를 추가해 순수 모듈(`results.py`, `briefing.py`)로 만들고 CLI와 TUI가 공유한다. 이력/검색은 SQLite 인덱스만 읽는 ModalScreen 두 개(`tui/screens.py`)로 구현한다. 세션 매칭은 fd 기반이 실물에서 불가함을 확인했으므로(claude가 transcript fd를 상시 열지 않음 — 실측), same-cwd 다중 pane을 "pane 생성 시각 ↔ 세션 시작 시각" 페어링으로 개선한다.

**Tech Stack:** Python 3.13, Textual 8.2.8 (DataTable/ModalScreen — API 실검증 완료: slash/h/A 키 바인딩, add_columns/add_row/RowSelected/cursor_row 동작 확인), rsync, POSIX sh (`/proc/<pid>/stat` starttime 추출 스크립트 실검증 완료).

## Global Constraints

- 런타임 의존성: stdlib + textual>=8,<9 만 (변경 없음). Claude API 호출 금지.
- 블로킹 호출(ssh/rsync)은 `@work(thread=True, exit_on_error=False)` 워커에서만, UI 반영은 `call_from_thread`, hand-off 직전 `worker.is_cancelled` 확인 (M2 확립 패턴). 로컬 SQLite/파일 조회는 ms 단위라 이벤트 루프에서 직접 호출 허용.
- 원격 셸 스크립트는 POSIX sh 호환만 사용 (bash 전용 문법 금지).
- rsync에 `--delete` 금지.
- 테스트는 `.venv/bin/pytest` (현재 86개 통과), 브랜치 `m3-history-results`, 커밋 메시지는 한국어 짧은 제목·트레일러 없음.
- tests import 관례: `from conftest import FakeRemote`.

## File Structure

```
src/cchub/
  tmux.py        # (수정) Pane.pid 추가, CLAUDE_COMMANDS 이동, verify_pane/confirm_delivery
  sessions.py    # (수정) same-cwd 페어링 (_pane_start_times, _pair_same_cwd)
  results.py     # 결과 수집 (collect_results)
  briefing.py    # 종합 브리핑 생성 (generate_briefing)
  ssh.py         # (수정) Remote.fetch/push
  cli.py         # (수정) results/brief/push 서브커맨드, send 반영확인
  index.py       # (수정) DELETE 중복 정리
  tui/
    screens.py   # SearchScreen, HistoryScreen
    app.py       # (수정) slash/h/r/A 바인딩·액션, open_transcript, 전송 검증
tests/
  test_results.py, test_briefing.py, test_screens.py (+기존 파일 수정)
```

---

### Task 1: 이월 소소 정리 (poll_stats 루프 내 취소 체크 · index DELETE 중복 제거)

**Files:**
- Modify: `src/cchub/tui/app.py` (poll_stats), `src/cchub/index.py` (_forget/forget_all)
- Test: `tests/test_tui_app.py` (수정), 기존 index 테스트로 리팩터링 무회귀 확인

**Interfaces:**
- Consumes: 기존 전부. Produces: 시그니처 변화 없음 (내부 정리).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tui_app.py`의 기존 `poll_stats` 취소 가드 테스트 옆에 추가

```python
async def test_poll_stats_cancel_mid_loop_stops_tracker_updates(tmp_path, monkeypatch):
    """루프 도중 취소되면 남은 서버의 tracker.update가 실행되지 않는다."""
    import cchub.tui.app as app_mod

    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["a"] = ServerConfig(name="a", host="u@a")
    app.cfg.servers["b"] = ServerConfig(name="b", host="u@b")

    calls = []

    class FakeWorker:
        def __init__(self):
            self._n = 0

        @property
        def is_cancelled(self):
            # 첫 iteration 후부터 취소된 것으로 응답
            self._n += 1
            return self._n > 1

    monkeypatch.setattr(app_mod, "get_current_worker", lambda: FakeWorker())
    monkeypatch.setattr(app_mod.stats_mod, "read_stats",
                        lambda remote: calls.append(1) or None)
    app.remote_factory = lambda h: None
    async with app.run_test() as pilot:
        app.poll_stats()
        await app.workers.wait_for_complete()
        assert len(calls) <= 1  # 두 번째 서버는 폴링되지 않음
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -k cancel_mid_loop -v`
Expected: FAIL — calls가 2 (루프가 끝까지 돎)

- [ ] **Step 3: 구현 2건**

`src/cchub/tui/app.py` — `poll_stats` 루프에 per-iteration 체크:

```python
    @work(thread=True, exclusive=True, group="stats", exit_on_error=False)
    def poll_stats(self) -> None:
        worker = get_current_worker()
        labels = []
        for name, s in self.cfg.servers.items():
            if worker.is_cancelled:
                return
            tracker = self.server_stats.setdefault(name, stats_mod.ServerStats())
            tracker.update(stats_mod.read_stats(self.remote_factory(s.host)))
            labels.append(tracker.label(name))
        if worker.is_cancelled:
            return
        self.call_from_thread(self._update_stats_bar, "   ".join(labels))
```

`src/cchub/index.py` — DELETE 중복 제거 (내부 헬퍼로 통합, 동작 불변):

```python
    def _delete_rows(self, where: str, params: tuple) -> None:
        self.db.execute(f"DELETE FROM sessions WHERE {where}", params)
        self.db.execute(f"DELETE FROM messages WHERE {where}", params)
        self.db.commit()

    def _forget(self, server: str, session_id: str) -> None:
        self._delete_rows("server=? AND session_id=?", (server, session_id))

    def forget_all(self) -> None:
        with self._lock:
            self._delete_rows("1=1", ())
```

(`_migrate_if_needed`의 DROP 로직은 스키마 교체라 그대로 둔다.)

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 87 passed (기존 index/forget 테스트가 리팩터링 무회귀를 증명)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "fix: poll_stats 루프 내 취소 체크 및 index DELETE 중복 정리"
```

---

### Task 2: same-cwd 다중 pane 세션 배정 (pane 생성시각 ↔ 세션 시작시각 페어링)

**배경 (실측):** 스펙의 `/proc/<pid>/fd` 매칭은 불가 — 실행 중인 claude 2개에서 `find /proc/*/fd -lname '*.claude/projects/*'`가 빈 결과 (transcript fd를 상시 열지 않음). 대안: pane 프로세스의 starttime(`/proc/<pid>/stat` 22번째 필드)과 세션 first_ts를 순서 페어링.

**Files:**
- Modify: `src/cchub/tmux.py` (_FMT·Pane에 pid 추가, CLAUDE_COMMANDS 이동), `src/cchub/sessions.py` (discover 재구성)
- Test: `tests/test_tmux.py`, `tests/test_sessions.py` (수정+추가). **fixture 파급**: `PANES` 상수가 5필드가 되므로 `tests/test_sessions.py`, `tests/test_tui_data.py`, `tests/test_cli.py`, `tests/test_tui_app.py`(사용 시)의 PANES 문자열에 pid 필드 추가 필요.

**Interfaces:**
- Produces: `tmux.Pane(pane_id, location, cwd, command, pid)` (pid: str); `tmux.CLAUDE_COMMANDS = {"claude", "node"}` (sessions가 import); `sessions.discover` 시그니처 불변, same-cwd 그룹은 서로 다른 세션을 배정받음.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tmux.py` — `_FMT` 고정 테스트와 PANES_OUT 수정:

```python
PANES_OUT = (
    "%0\tmain:0.0\t/home/u/proj\tclaude\t100\n"
    "%3\tmain:1.0\t/home/u/other\tbash\t200\n"
    "잘못된 줄\n"
)
# test_list_panes_parses_and_skips_bad_lines에서:
    assert panes[0] == tmux.Pane("%0", "main:0.0", "/home/u/proj", "claude", "100")
    assert fake.calls[0] == ["tmux", "list-panes", "-a", "-F", tmux._FMT]
    assert tmux._FMT == (
        "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}"
        "\t#{pane_current_path}\t#{pane_current_command}\t#{pane_pid}"
    )
```

`tests/test_sessions.py` — PANES를 5필드로 수정하고 페어링 테스트 추가:

```python
PANES = (
    "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"
    "%1\tmain:1.0\t/home/u/other\tnode\t200\n"
    "%2\tmain:2.0\t/home/u/x\tbash\t300\n"
)

SAME_CWD_PANES = (
    "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"   # 먼저 만든 pane (starttime 1000)
    "%6\tmain:1.0\t/home/u/proj\tclaude\t200\n"   # 나중 pane (starttime 2000)
)


def make_two_sessions(tmp_path):
    """같은 프로젝트에 세션 2개: old-session(first_ts 이름), new-session."""
    from cchub.index import SessionIndex
    cache = tmp_path / "cache" / "srv1"
    d = cache / "projects" / "-home-u-proj"
    d.mkdir(parents=True)
    idx = SessionIndex(tmp_path / "i.db")
    for name, ts in [("old-session", "2026-07-01T09:00:00.000Z"),
                     ("new-session", "2026-07-02T09:00:00.000Z")]:
        f = d / f"{name}.jsonl"
        f.write_text(
            '{"type":"user","message":{"role":"user","content":"시작"},'
            f'"sessionId":"{name}","timestamp":"{ts}"}}\n'.replace("}}", "}")
        )
        idx.index_file("srv1", "-home-u-proj", f)
    return cache, idx


def test_same_cwd_panes_get_distinct_sessions(tmp_path):
    fake = FakeRemote({
        "tmux": RunResult(0, SAME_CWD_PANES, ""),
        "sh": RunResult(0, "100 1000\n200 2000\n", ""),  # pid starttime
    })
    cache, idx = make_two_sessions(tmp_path)
    live = sessions.discover(fake, "srv1", cache, idx)
    by_pane = {s.pane_id: s.session_id for s in live}
    assert by_pane["%5"] == "old-session"   # 먼저 만든 pane ↔ 먼저 시작한 세션
    assert by_pane["%6"] == "new-session"
    assert len(set(by_pane.values())) == 2  # 중복 배정 없음


def test_same_cwd_falls_back_when_starttime_unavailable(tmp_path):
    fake = FakeRemote({
        "tmux": RunResult(0, SAME_CWD_PANES, ""),
        "sh": RunResult(255, "", "권한 없음"),
    })
    cache, idx = make_two_sessions(tmp_path)
    live = sessions.discover(fake, "srv1", cache, idx)
    # 폴백: 기존 휴리스틱(모두 최신 세션) — 크래시 없이 동작하는 것이 핵심
    assert all(s.session_id for s in live)
```

(기존 `setup_cache` 기반 테스트들은 PANES 5필드 수정 외 그대로 통과해야 함. `tests/test_tui_data.py`/`tests/test_cli.py`의 `PANES = "%5\tmain:0.0\t/home/u/proj\tclaude\n"`도 `...\tclaude\t100\n"`으로 수정.)

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tmux.py tests/test_sessions.py -v`
Expected: FAIL — Pane에 pid 없음 / 페어링 미구현

- [ ] **Step 3: 구현**

`src/cchub/tmux.py`:

```python
_FMT = (
    "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}"
    "\t#{pane_current_path}\t#{pane_current_command}\t#{pane_pid}"
)

# claude는 실행 방식에 따라 claude 또는 node로 보인다 (sessions와 공유)
CLAUDE_COMMANDS = {"claude", "node"}


@dataclass
class Pane:
    pane_id: str    # 예: %0 (tmux 고유 ID)
    location: str   # 예: main:1.0
    cwd: str
    command: str
    pid: str        # pane 최상위 프로세스 pid


def list_panes(remote: Remote) -> list[Pane]:
    r = remote.run(["tmux", "list-panes", "-a", "-F", _FMT])
    if r.rc != 0:
        return []
    panes = []
    for line in r.out.splitlines():
        parts = line.split("\t")
        if len(parts) == 5:
            panes.append(Pane(*parts))
    return panes
```

`src/cchub/sessions.py` — `_CLAUDE_COMMANDS`를 `tmux.CLAUDE_COMMANDS` import로 교체하고 discover 재구성:

```python
from cchub.tmux import CLAUDE_COMMANDS

# /proc/<pid>/stat: comm에 공백이 올 수 있어 ')' 뒤를 잘라 22번째(잘린 후 20번째) 필드를 읽는다
_STARTTIME_SCRIPT = (
    'for p in {pids}; do s=$(cat /proc/$p/stat 2>/dev/null) || continue; '
    's=${{s##*) }}; set -- $s; printf "%s %s\\n" "$p" "${{20:-0}}"; done'
)


def _pane_start_times(remote: Remote, pids: list[str]) -> dict[str, int]:
    clean = [p for p in pids if p.isdigit()]
    if not clean:
        return {}
    r = remote.run(["sh", "-c", _STARTTIME_SCRIPT.format(pids=" ".join(clean))])
    if r.rc != 0:
        return {}
    out: dict[str, int] = {}
    for line in r.out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _pair_same_cwd(
    remote: Remote,
    server: str,
    group: list,          # 같은 프로젝트의 Pane들
    jsonls: list[Path],   # mtime 내림차순
    index: SessionIndex | None,
) -> dict[str, Path]:
    """pane 생성순 ↔ 세션 시작순 페어링. starttime 조회 실패 시 기존 휴리스틱."""
    starts = _pane_start_times(remote, [p.pid for p in group])
    if len(starts) < len(group):
        return {p.pane_id: jsonls[0] for p in group}
    panes_sorted = sorted(group, key=lambda p: starts.get(p.pid, 0))
    candidates = jsonls[: len(group)]  # 최신 N개가 활성 세션 후보

    def first_ts(f: Path) -> str:
        row = index.get_session(server, f.stem) if index else None
        return row.first_ts if row and row.first_ts else "9999"

    cands_sorted = sorted(candidates, key=first_ts)
    out = {p.pane_id: f for p, f in zip(panes_sorted, cands_sorted)}
    for p in panes_sorted[len(cands_sorted):]:
        out[p.pane_id] = jsonls[0]
    return out


def discover(
    remote: Remote,
    server: str,
    cache_dir: Path,
    index: SessionIndex | None = None,
) -> list[LiveSession]:
    panes = [p for p in tmux.list_panes(remote) if p.command in CLAUDE_COMMANDS]
    panes.sort(key=lambda p: p.location)
    by_project: dict[str, list] = {}
    for p in panes:
        by_project.setdefault(encode_project(p.cwd), []).append(p)

    assign: dict[str, Path | None] = {}
    for project, group in by_project.items():
        proj_dir = cache_dir / "projects" / project
        jsonls = (
            sorted(proj_dir.glob("*.jsonl"), key=_mtime, reverse=True)
            if proj_dir.is_dir() else []
        )
        if not jsonls:
            for p in group:
                assign[p.pane_id] = None
        elif len(group) == 1:
            assign[group[0].pane_id] = jsonls[0]
        else:
            assign.update(_pair_same_cwd(remote, server, group, jsonls, index))

    now = time.time()
    out: list[LiveSession] = []
    for i, p in enumerate(panes, start=1):
        f = assign.get(p.pane_id)
        session_id, title, state = "", "", "unknown"
        if f is not None:
            session_id = f.stem
            state = "working" if now - _mtime(f) < _WORKING_WINDOW_SECS else "idle"
            if index is not None:
                row = index.get_session(server, session_id)
                if row:
                    title = row.title
                    if state != "working" and row.last_role == "assistant":
                        state = "waiting"
        out.append(LiveSession(
            server=server, number=i, pane_id=p.pane_id, location=p.location,
            cwd=p.cwd, project=encode_project(p.cwd), session_id=session_id,
            title=title, state=state,
        ))
    return out
```

- [ ] **Step 4: 통과 확인 (전체 — fixture 파급 수정 포함)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 89 passed (87 + 2 신규; PANES fixture 수정된 파일들 전부 통과)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: same-cwd 다중 pane을 시작시각 페어링으로 구분 배정"
```

---

### Task 3: 전송 전 pane 검증 + 전송 후 반영 확인 (스펙 이월)

**Files:**
- Modify: `src/cchub/tmux.py`, `src/cchub/tui/app.py` (do_send/_after_send), `src/cchub/cli.py` (cmd_send)
- Test: `tests/test_tmux.py`, `tests/test_tui_app.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `tmux.verify_pane(remote, pane_id) -> bool` (pane 존재 + command가 CLAUDE_COMMANDS); `tmux.confirm_delivery(remote, pane_id, text) -> bool` (전송 텍스트 앞 20자가 capture에 보이는지 best-effort, 빈 텍스트는 True); `CchubApp._after_send(ok: bool, delivered: bool)` (시그니처 변경 — 기존 호출부 함께 수정)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_tmux.py`:

```python
def test_verify_pane():
    fake = FakeRemote({"tmux": RunResult(0, PANES_OUT, "")})
    assert tmux.verify_pane(fake, "%0")        # claude pane
    assert not tmux.verify_pane(fake, "%3")    # bash pane
    assert not tmux.verify_pane(fake, "%99")   # 없음


def test_confirm_delivery():
    fake = FakeRemote({"tmux": RunResult(0, "…화면에 실험 시작해줘 라고 보임\n", "")})
    assert tmux.confirm_delivery(fake, "%0", "실험 시작해줘")
    assert tmux.confirm_delivery(fake, "%0", "   ")     # 빈 텍스트는 True
    fake2 = FakeRemote({"tmux": RunResult(0, "다른 내용\n", "")})
    assert not tmux.confirm_delivery(fake2, "%0", "실험 시작해줘")
```

`tests/test_tui_app.py`:

```python
async def test_send_to_vanished_pane_notifies_and_skips(tmp_path):
    # tmux list-panes가 빈 결과 → verify_pane 실패 → send-keys 미실행
    fake = FakeRemote({"tmux": RunResult(0, "", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="idle"))
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "보내지면 안 됨"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)
```

주의: 기존 send 테스트들(FakeRemote가 tmux 키에 `RunResult(0, "", "")`)은 verify_pane이 빈 pane 목록을 받아 실패하게 됨 → 기존 send 테스트의 FakeRemote 응답을 pane 목록을 돌려주도록 수정해야 한다. 그런데 FakeRemote는 argv[0] 단일 키라 list-panes/send-keys/capture-pane이 모두 "tmux" 키를 공유한다. **conftest.py의 FakeRemote를 확장**해 우선 argv[:2] 튜플 키를 찾고 없으면 argv[0] 키로 폴백:

```python
    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        self.calls.append(list(argv))
        key2 = (argv[0], argv[1]) if len(argv) > 1 else None
        if key2 in self.responses:
            return self.responses[key2]
        return self.responses.get(argv[0], RunResult(0, "", ""))
```

기존 send 테스트는 `FakeRemote({("tmux", "list-panes"): RunResult(0, PANES, ""), "tmux": RunResult(0, "", "")})` 형태로 수정 (PANES는 `"%5\tmain:0.0\t/home/u/proj\tclaude\t100\n"`).

`tests/test_cli.py` — cmd_send 후 반영 확인 문구 (capture가 프롬프트를 포함하면 정상 메시지):

```python
def test_send_confirms_delivery(env, capsys):
    tmp, fake = env
    fake.responses[("tmux", "capture-pane")] = RunResult(0, "실험 시작해줘\n", "")
    assert cli.main(["send", "srv1", "1", "실험 시작해줘"]) == 0
    out = capsys.readouterr().out
    assert "전송됨" in out
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tmux.py tests/test_tui_app.py tests/test_cli.py -v`
Expected: 신규 테스트 FAIL (verify_pane/confirm_delivery 부재)

- [ ] **Step 3: 구현**

`src/cchub/tmux.py`:

```python
def verify_pane(remote: Remote, pane_id: str) -> bool:
    """전송 직전: pane이 존재하고 claude 계열 프로세스인지 확인."""
    return any(
        p.pane_id == pane_id and p.command in CLAUDE_COMMANDS
        for p in list_panes(remote)
    )


def confirm_delivery(remote: Remote, pane_id: str, text: str) -> bool:
    """전송 후 1회: 텍스트 앞부분이 화면에 보이는지 (best-effort, 래핑 시 미검출 가능)."""
    probe = text.strip()[:20]
    if not probe:
        return True
    return probe in capture(remote, pane_id, lines=50)
```

`src/cchub/tui/app.py` — `do_send`/`_after_send` 교체:

```python
    @work(thread=True, group="send", exit_on_error=False)
    def do_send(self, text: str) -> None:
        ls = self.selected
        if ls is None:
            self.call_from_thread(self.notify, "선택된 세션이 사라졌습니다",
                                  severity="warning")
            return
        try:
            remote = self.remote_factory(self.cfg.servers[ls.server].host)
            if not tmux.verify_pane(remote, ls.pane_id):
                self.call_from_thread(
                    self.notify, "대상 pane이 더 이상 유효하지 않습니다 (y로 새로고침)",
                    severity="error")
                return
            ok = tmux.send_prompt(remote, ls.pane_id, text)
            delivered = ok and tmux.confirm_delivery(remote, ls.pane_id, text)
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.notify, f"전송 실패: {e}", severity="error")
            return
        self.call_from_thread(self._after_send, ok, delivered)

    def _after_send(self, ok: bool, delivered: bool) -> None:
        if ok:
            self.query_one("#prompt", Input).value = ""
            if delivered:
                self.notify("전송됨")
            else:
                self.notify("전송됨 (화면 반영 미확인)", severity="warning")
            self.show_detail()
        else:
            self.notify("전송 실패 (pane 소실 또는 tmux 오류)", severity="error")
```

`src/cchub/cli.py` — `cmd_send`에서 전송 후 확인 추가 (send 성공 print 교체):

```python
    if not tmux.send_prompt(remote, ls.pane_id, args.prompt):
        print("전송 실패 (pane이 사라졌거나 tmux 오류)", file=sys.stderr)
        return 1
    if tmux.confirm_delivery(remote, ls.pane_id, args.prompt):
        print(f"{args.server} 세션 {args.number}({ls.project})에 전송됨")
    else:
        print(f"{args.server} 세션 {args.number}({ls.project})에 전송됨 (화면 반영 미확인)")
    return 0
```

(CLI는 이미 `_resolve`의 discover가 pane 존재를 검증하므로 verify_pane 재호출은 생략.)

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 93)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: 전송 전 pane 검증 및 전송 후 화면 반영 확인"
```

---

### Task 4: Remote.fetch/push + 결과 수집 (results.py, CLI results)

**Files:**
- Create: `src/cchub/results.py`
- Modify: `src/cchub/ssh.py` (Remote/SSHRemote에 fetch/push), `tests/conftest.py` (FakeRemote에 fetch/push 기록), `src/cchub/cli.py` (results 서브커맨드)
- Test: `tests/test_ssh.py`, `tests/test_results.py` (Create), `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `Remote.fetch(remote_path: str, local_dir: Path, timeout: int = 300) -> RunResult` — 원격 경로(glob 허용, 원격 셸이 확장)를 local_dir로 rsync
  - `Remote.push(local_path: Path, remote_dir: str, timeout: int = 300) -> RunResult` — 로컬 경로를 원격 디렉토리로 rsync (local_path가 디렉토리면 내용물 전송: 소스에 트레일링 슬래시)
  - `FakeRemote.fetches: list[tuple[str, Path]]`, `FakeRemote.pushes: list[tuple[Path, str]]` (rc=0 반환)
  - `results.FetchReport(server: str, ok: bool, failed: list[str])`; `results.collect_results(cfg, root, server, remote_factory=SSHRemote) -> FetchReport` — 해당 서버의 `results` 패턴들을 `root/results/<server>/`로 수집, 패턴별 실패 격리
  - CLI: `cchub results [server]` (생략 시 전체 서버)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/conftest.py` FakeRemote에 추가:

```python
    def fetch(self, remote_path: str, local_dir, timeout: int = 300) -> RunResult:
        self.fetches.append((remote_path, Path(local_dir)))
        return RunResult(0, "", "")

    def push(self, local_path, remote_dir: str, timeout: int = 300) -> RunResult:
        self.pushes.append((Path(local_path), remote_dir))
        return RunResult(0, "", "")
```

(`__init__`에 `self.fetches = []`, `self.pushes = []` 추가.)

`tests/test_ssh.py`:

```python
def test_fetch_builds_rsync(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "results" / "srv1"
    r = SSHRemote("u@h").fetch("~/exp/*.json", dest)
    assert r.rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == "rsync" and "--delete" not in cmd
    assert cmd[-2] == "u@h:~/exp/*.json"      # 원격 셸이 glob 확장
    assert cmd[-1] == str(dest) + "/"
    assert dest.exists()


def test_push_dir_sends_contents(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    d = tmp_path / "payload"
    d.mkdir()
    SSHRemote("u@h").push(d, "~/inbox")
    cmd = captured["cmd"]
    assert cmd[-2] == str(d) + "/"            # 디렉토리 → 내용물 전송
    assert cmd[-1] == "u@h:~/inbox/"
    f = tmp_path / "one.txt"
    f.write_text("x")
    SSHRemote("u@h").push(f, "~/inbox")
    assert captured["cmd"][-2] == str(f)      # 파일 → 그대로
```

`tests/test_results.py`:

```python
from pathlib import Path

from cchub.config import Config, ServerConfig
from cchub.results import collect_results
from cchub.ssh import RunResult
from conftest import FakeRemote


def make_cfg():
    return Config(servers={"srv1": ServerConfig(
        name="srv1", host="u@h", results=["~/exp/**", "~/bench/*.json"])})


def test_collect_results_fetches_each_pattern(tmp_path):
    fake = FakeRemote()
    rep = collect_results(make_cfg(), tmp_path, "srv1", remote_factory=lambda h: fake)
    assert rep.ok and rep.failed == []
    assert [f[0] for f in fake.fetches] == ["~/exp/**", "~/bench/*.json"]
    assert all(f[1] == tmp_path / "results" / "srv1" for f in fake.fetches)


def test_collect_results_isolates_pattern_failure(tmp_path):
    class Flaky(FakeRemote):
        def fetch(self, remote_path, local_dir, timeout=300):
            super().fetch(remote_path, local_dir, timeout)
            if "bench" in remote_path:
                return RunResult(23, "", "rsync 에러")
            return RunResult(0, "", "")

    fake = Flaky()
    rep = collect_results(make_cfg(), tmp_path, "srv1", remote_factory=lambda h: fake)
    assert not rep.ok
    assert rep.failed == ["~/bench/*.json"]
    assert len(fake.fetches) == 2   # 실패해도 다음 패턴 계속


def test_collect_results_no_patterns(tmp_path):
    cfg = Config(servers={"s": ServerConfig(name="s", host="h")})
    rep = collect_results(cfg, tmp_path, "s", remote_factory=lambda h: FakeRemote())
    assert rep.ok and rep.failed == []
```

`tests/test_cli.py`:

```python
def test_results_command(env, capsys):
    tmp, fake = env
    assert cli.main(["results", "srv1"]) == 0
    assert "srv1" in capsys.readouterr().out
```

(env fixture의 config에 `results = ["~/exp/*"]`를 추가해야 fetch가 기록됨 — config.toml 문자열 수정.)

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_ssh.py tests/test_results.py tests/test_cli.py -v`
Expected: FAIL (fetch/push/collect_results 부재)

- [ ] **Step 3: 구현**

`src/cchub/ssh.py` — Remote 베이스에 추상 메서드, SSHRemote 구현 추가:

```python
    # Remote 클래스에:
    def fetch(self, remote_path: str, local_dir: Path, timeout: int = 300) -> RunResult:
        raise NotImplementedError

    def push(self, local_path: Path, remote_dir: str, timeout: int = 300) -> RunResult:
        raise NotImplementedError

    # SSHRemote에:
    def _rsync(self, src: str, dst: str, timeout: int) -> RunResult:
        try:
            p = subprocess.run(
                ["rsync", "-az", "-e", "ssh " + " ".join(self._opts), src, dst],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def fetch(self, remote_path: str, local_dir: Path, timeout: int = 300) -> RunResult:
        local_dir.mkdir(parents=True, exist_ok=True)
        return self._rsync(f"{self.host}:{remote_path}", str(local_dir) + "/", timeout)

    def push(self, local_path: Path, remote_dir: str, timeout: int = 300) -> RunResult:
        src = str(local_path) + ("/" if local_path.is_dir() else "")
        return self._rsync(src, f"{self.host}:{remote_dir}/", timeout)
```

(기존 `mirror`도 `_rsync`를 쓰도록 정리 가능하면 정리 — 동작·argv 불변 확인.)

`src/cchub/results.py`:

```python
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
```

`src/cchub/cli.py` — 서브커맨드/핸들러 추가:

```python
    p = sub.add_parser("results", help="실험 결과 수집 (config의 results 패턴)")
    p.add_argument("server", nargs="?", help="생략 시 전체 서버")
```

```python
def cmd_results(args) -> int:
    from cchub.results import collect_results
    cfg, root, _index = _ctx()
    servers = [args.server] if args.server else list(cfg.servers)
    ok = True
    for name in servers:
        if name not in cfg.servers:
            print(f"알 수 없는 서버: {name}", file=sys.stderr)
            return 1
        rep = collect_results(cfg, root, name, _make_remote)
        if rep.ok:
            print(f"{name}: ok → {root / 'results' / name}")
        else:
            ok = False
            print(f"{name}: 실패 패턴 {', '.join(rep.failed)}", file=sys.stderr)
    return 0 if ok else 1
```

주의: `collect_results`의 remote_factory 파라미터에 `_make_remote`(host→SSHRemote)를 그대로 넘긴다 (테스트에서 monkeypatch되는 지점 유지).

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 99)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: Remote fetch/push 및 결과 수집 (cchub results)"
```

---

### Task 5: 종합 브리핑 생성 (briefing.py, CLI brief)

**Files:**
- Create: `src/cchub/briefing.py`
- Modify: `src/cchub/cli.py`
- Test: `tests/test_briefing.py` (Create), `tests/test_cli.py`

**Interfaces:**
- Consumes: `index.list_sessions(server)`, `root/results/` 트리
- Produces: `briefing.generate_briefing(cfg, root, index, now: datetime | None = None) -> tuple[Path, str]` — 브리핑 md 파일 경로와 "로컬 Claude에 붙여넣을 프롬프트" 문자열; CLI `cchub brief`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_briefing.py`

```python
from datetime import datetime
from pathlib import Path

from cchub.briefing import generate_briefing
from cchub.config import Config, ServerConfig
from cchub.index import SessionIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def make_env(tmp_path):
    cfg = Config(servers={"srv1": ServerConfig(name="srv1", host="u@h")})
    idx = SessionIndex(tmp_path / "i.db")
    import shutil
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    idx.index_file("srv1", "-home-u-proj", proj / "s-1.jsonl")
    rdir = tmp_path / "results" / "srv1"
    rdir.mkdir(parents=True)
    (rdir / "bench.json").write_text("{}")
    return cfg, idx


def test_generate_briefing_writes_file_and_prompt(tmp_path):
    cfg, idx = make_env(tmp_path)
    now = datetime(2026, 7, 20, 14, 30)
    path, prompt = generate_briefing(cfg, tmp_path, idx, now=now)
    assert path.name == "briefing-20260720-1430.md"
    body = path.read_text()
    assert "srv1" in body
    assert "bench.json" in body            # 수집 파일 목록
    assert "NUMA 실험" in body              # 세션 제목
    assert str(path) in prompt              # 프롬프트가 파일을 가리킴
    assert "리포트" in prompt


def test_generate_briefing_without_results(tmp_path):
    cfg = Config(servers={"s": ServerConfig(name="s", host="h")})
    idx = SessionIndex(tmp_path / "i.db")
    path, _ = generate_briefing(cfg, tmp_path, idx,
                                now=datetime(2026, 7, 20, 0, 0))
    assert "수집된 결과 없음" in path.read_text()
```

`tests/test_cli.py`:

```python
def test_brief_command(env, capsys):
    cli.main(["sync"])
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "briefing-" in out and "붙여넣" in out
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_briefing.py tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.briefing'`

- [ ] **Step 3: 구현** — `src/cchub/briefing.py`

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from cchub.config import Config
from cchub.index import SessionIndex

_MAX_FILES_PER_SERVER = 50
_MAX_SESSIONS_PER_SERVER = 5


def generate_briefing(
    cfg: Config,
    root: Path,
    index: SessionIndex,
    now: datetime | None = None,
) -> tuple[Path, str]:
    """수집 결과 + 세션 요약을 담은 브리핑 md를 만들고, 로컬 Claude용 프롬프트를 반환."""
    now = now or datetime.now()
    results_root = root / "results"
    lines = [f"# 실험 결과 브리핑 — {now:%Y-%m-%d %H:%M}", ""]
    for server in sorted(cfg.servers):
        lines.append(f"## {server}")
        sdir = results_root / server
        files = sorted(p for p in sdir.rglob("*") if p.is_file()) if sdir.is_dir() else []
        if files:
            lines.append(f"### 수집된 결과 파일 ({len(files)}개)")
            for f in files[:_MAX_FILES_PER_SERVER]:
                lines.append(f"- {f.relative_to(results_root)}")
            if len(files) > _MAX_FILES_PER_SERVER:
                lines.append(f"- … 외 {len(files) - _MAX_FILES_PER_SERVER}개")
        else:
            lines.append("(수집된 결과 없음 — `cchub results` 실행)")
        rows = index.list_sessions(server)[:_MAX_SESSIONS_PER_SERVER]
        if rows:
            lines.append("### 최근 세션")
            for r in rows:
                label = r.title or r.first_prompt[:60]
                lines.append(f"- {label} — 마지막 활동 {r.last_ts}")
        lines.append("")
    results_root.mkdir(parents=True, exist_ok=True)
    path = results_root / f"briefing-{now:%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines))
    prompt = (
        f"{path} 브리핑 파일을 읽고, {results_root} 아래에 수집된 실험 결과 파일들을 "
        "분석해서 종합 리포트를 작성해줘."
    )
    return path, prompt
```

`src/cchub/cli.py`:

```python
    sub.add_parser("brief", help="수집 결과 종합 브리핑 생성")
```

```python
def cmd_brief(_args) -> int:
    from cchub.briefing import generate_briefing
    cfg, root, index = _ctx()
    path, prompt = generate_briefing(cfg, root, index)
    print(f"브리핑 생성됨: {path}")
    print()
    print("아래 프롬프트를 로컬 Claude Code 세션에 붙여넣으세요:")
    print()
    print(prompt)
    return 0
```

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 102)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: 종합 브리핑 생성 (cchub brief)"
```

---

### Task 6: 서버 간 파일 중계 (CLI push)

**Files:**
- Modify: `src/cchub/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Remote.fetch/push` (Task 4)
- Produces: CLI `cchub push <src> <dst>` — src/dst는 `로컬경로` 또는 `<서버>:<경로>`. 로컬↔서버 직접, 서버→서버는 로컬 `root/relay/<임시디렉토리>` 경유.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cli.py`

```python
def test_push_local_to_remote(env, capsys):
    tmp, fake = env
    f = tmp / "data.json"
    f.write_text("{}")
    assert cli.main(["push", str(f), "srv1:~/inbox"]) == 0
    assert fake.pushes and fake.pushes[0][1] == "~/inbox"


def test_push_remote_to_local(env, capsys, tmp_path):
    tmp, fake = env
    dest = tmp_path / "here"
    assert cli.main(["push", "srv1:~/exp/out.json", str(dest)]) == 0
    assert fake.fetches and fake.fetches[0][0] == "~/exp/out.json"


def test_push_remote_to_remote_relays_via_local(env, capsys):
    tmp, fake = env
    # env fixture에 두 번째 서버 srv2 추가 필요 (config.toml에 [servers.srv2] host="u@h2")
    assert cli.main(["push", "srv1:~/exp/out.json", "srv2:~/inbox"]) == 0
    assert fake.fetches[0][0] == "~/exp/out.json"
    assert fake.pushes[0][1] == "~/inbox"
    # relay 경유 경로가 CCHUB_DIR/relay 아래
    assert str(tmp / "relay") in str(fake.fetches[0][1])


def test_push_both_local_is_error(env, capsys):
    assert cli.main(["push", "/a", "/b"]) == 1
    assert "서버" in capsys.readouterr().err
```

(env fixture config에 `[servers.srv2]\nhost = "u@h2"` 추가 — 기존 테스트 영향 확인: cmd_sync/list가 srv2도 순회하므로 출력 단언이 깨지지 않는지 확인하고 필요한 최소 수정.)

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_cli.py -v -k push`
Expected: FAIL — push 서브커맨드 없음 (argparse 에러)

- [ ] **Step 3: 구현** — `src/cchub/cli.py`

```python
    p = sub.add_parser("push", help="파일 중계 (<서버>:<경로> 또는 로컬 경로)")
    p.add_argument("src")
    p.add_argument("dst")
```

```python
def _parse_loc(cfg: Config, loc: str) -> tuple[str | None, str]:
    """'srv1:~/path' → ('srv1', '~/path'), 로컬 경로 → (None, 경로)."""
    if ":" in loc:
        name, _, path = loc.partition(":")
        if name in cfg.servers:
            return name, path
    return None, loc


def cmd_push(args) -> int:
    import tempfile

    cfg, root, _index = _ctx()
    src_srv, src_path = _parse_loc(cfg, args.src)
    dst_srv, dst_path = _parse_loc(cfg, args.dst)
    if src_srv is None and dst_srv is None:
        print("src/dst 중 하나는 <서버>:<경로> 형식이어야 합니다", file=sys.stderr)
        return 1
    if src_srv and dst_srv:
        relay_root = root / "relay"
        relay_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=relay_root))
        r = _make_remote(cfg.servers[src_srv].host).fetch(src_path, tmp)
        if r.rc != 0:
            print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
            return 1
        r = _make_remote(cfg.servers[dst_srv].host).push(tmp, dst_path)
    elif src_srv:
        r = _make_remote(cfg.servers[src_srv].host).fetch(
            src_path, Path(dst_path).expanduser())
    else:
        r = _make_remote(cfg.servers[dst_srv].host).push(
            Path(src_path).expanduser(), dst_path)
    if r.rc != 0:
        print(f"전송 실패: {r.err.strip()}", file=sys.stderr)
        return 1
    print("완료")
    return 0
```

(핸들러 dict에 `"push": cmd_push`, `"results": cmd_results`, `"brief": cmd_brief` 등록 확인. `from pathlib import Path` 이미 있음.)

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 106)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: 서버 간 파일 중계 (cchub push)"
```

---

### Task 7: TUI 검색 화면 (/) + open_transcript

**Files:**
- Create: `src/cchub/tui/screens.py`
- Modify: `src/cchub/tui/app.py` (바인딩 slash, action_search, open_transcript)
- Test: `tests/test_screens.py` (Create)

**Interfaces:**
- Consumes: `index.search(query, limit)`, `index.tail`
- Produces: `screens.SearchScreen(ModalScreen[tuple[str, str] | None])` — Enter로 검색, 행 선택 시 `(server, session_id)` dismiss, escape로 None dismiss; `CchubApp.open_transcript(server: str, session_id: str)` — detail 패널에 해당 세션 transcript 표시 (로컬 sqlite 조회라 워커 불필요); `CchubApp.action_search` + `Binding("slash", "search", "검색")`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_screens.py`

```python
import shutil
from pathlib import Path

from textual.widgets import DataTable, Input, RichLog

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.tui.app import CchubApp
from cchub.tui.screens import SearchScreen

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def make_indexed_app(tmp_path) -> CchubApp:
    idx = SessionIndex(tmp_path / "i.db")
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    idx.index_file("srv1", "-home-u-proj", proj / "s-1.jsonl")
    return CchubApp(cfg=Config(servers={}), root=tmp_path, index=idx,
                    remote_factory=lambda h: None)


async def test_slash_opens_search_and_finds(tmp_path):
    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("slash")
        assert isinstance(app.screen, SearchScreen)
        inp = app.screen.query_one("#search-input", Input)
        inp.value = "NUMA"
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one("#search-results", DataTable)
        assert table.row_count >= 1


async def test_search_select_opens_transcript(tmp_path):
    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("slash")
        inp = app.screen.query_one("#search-input", Input)
        inp.value = "NUMA"
        await pilot.press("enter")
        await pilot.pause()
        table = app.screen.query_one("#search-results", DataTable)
        table.focus()
        await pilot.press("enter")     # 첫 행 선택 → dismiss → open_transcript
        await pilot.pause()
        assert not isinstance(app.screen, SearchScreen)
        # detail 패널에 transcript가 표시됨 (open_transcript 직접 검증 보조)
        app.open_transcript("srv1", "s-1")
        # RichLog 내부 렌더는 검증이 취약하므로 예외 없이 동작 + 화면 전환만 확인


async def test_search_escape_closes(tmp_path):
    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("slash")
        await pilot.press("escape")
        assert not isinstance(app.screen, SearchScreen)
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_screens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.tui.screens'`

- [ ] **Step 3: 구현**

`src/cchub/tui/screens.py`:

```python
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input

from cchub.index import SessionIndex


class SearchScreen(ModalScreen[tuple[str, str] | None]):
    """전체 이력 FTS 검색. 행 선택 시 (server, session_id)를 dismiss."""

    CSS = """
    SearchScreen { align: center middle; }
    #search-box { width: 90%; height: 80%; background: $panel; border: solid $primary; }
    #search-results { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "닫기")]

    def __init__(self, index: SessionIndex):
        super().__init__()
        self.index = index
        self._hits: list[tuple[str, str, str, str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Input(placeholder="전체 이력 검색 (Enter)", id="search-input")
            yield DataTable(id="search-results")

    def on_mount(self) -> None:
        table = self.query_one("#search-results", DataTable)
        table.add_columns("서버", "세션", "역할", "시각", "내용")
        table.cursor_type = "row"
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        self._hits = self.index.search(query, limit=100)
        table = self.query_one("#search-results", DataTable)
        table.clear()
        for server, sid, role, ts, snippet in self._hits:
            table.add_row(server, sid[:8], role, ts[:19], snippet)
        if self._hits:
            table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        hit = self._hits[event.cursor_row]
        self.dismiss((hit[0], hit[1]))

    def action_close(self) -> None:
        self.dismiss(None)
```

`src/cchub/tui/app.py` — import `from cchub.tui.screens import SearchScreen`, BINDINGS에 `Binding("slash", "search", "검색")` 추가, 메서드 추가:

```python
    def action_search(self) -> None:
        def _open(result: tuple[str, str] | None) -> None:
            if result:
                self.open_transcript(*result)
        self.push_screen(SearchScreen(self.index), _open)

    def open_transcript(self, server: str, session_id: str) -> None:
        # 로컬 sqlite 조회는 ms 단위 — 워커 불필요
        rows = self.index.tail(server, session_id, limit=50)
        text = "\n".join(f"── {r} {t}\n{b}" for r, t, b in rows) or "(transcript 없음)"
        self._write_detail(f"[{server} / {session_id[:8]}]\n{text}")
```

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 109)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: TUI 전체 이력 검색 화면 (/)"
```

---

### Task 8: TUI 이력 화면 (h) + 결과 수집(r)/브리핑(A) 액션

**Files:**
- Modify: `src/cchub/tui/screens.py` (HistoryScreen), `src/cchub/tui/app.py` (h/r/A 바인딩·액션)
- Test: `tests/test_screens.py`, `tests/test_tui_app.py`

**Interfaces:**
- Consumes: `index.list_sessions()`, `results.collect_results`, `briefing.generate_briefing`, `open_transcript` (Task 7)
- Produces: `screens.HistoryScreen(ModalScreen[tuple[str, str] | None])` — 전 서버 세션 타임라인(DataTable, last_ts 내림차순은 list_sessions가 보장), 상단 필터 Input(서버/프로젝트/제목/첫프롬프트 부분 문자열, 입력 즉시 반영), 행 선택 시 `(server, session_id)` dismiss; `CchubApp.action_history`(h), `action_collect_results`(r, 워커), `action_brief`(A)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_screens.py`에 추가:

```python
from cchub.tui.screens import HistoryScreen


async def test_h_opens_history_with_rows(tmp_path):
    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("h")
        assert isinstance(app.screen, HistoryScreen)
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 1     # s-1 세션


async def test_history_filter_narrows(tmp_path):
    app = make_indexed_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("h")
        inp = app.screen.query_one("#history-filter", Input)
        inp.focus()
        inp.value = "없는키워드"
        await pilot.pause()
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count == 0
        inp.value = "NUMA"
        await pilot.pause()
        assert table.row_count == 1
```

`tests/test_tui_app.py`에 추가:

```python
async def test_r_collects_results_via_worker(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod
    from cchub.results import FetchReport

    collected = []

    def fake_collect(cfg, root, server, remote_factory):
        collected.append(server)
        return FetchReport(server=server, ok=True)

    monkeypatch.setattr(app_mod, "collect_results", fake_collect)
    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        assert collected == ["srv1"]


async def test_A_generates_briefing_and_shows_prompt(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("A")
        await pilot.pause()
        briefs = list((tmp_path / "results").glob("briefing-*.md"))
        assert len(briefs) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_screens.py tests/test_tui_app.py -v`
Expected: 신규 4건 FAIL

- [ ] **Step 3: 구현**

`src/cchub/tui/screens.py`에 추가:

```python
class HistoryScreen(ModalScreen[tuple[str, str] | None]):
    """전 서버 통합 세션 타임라인. 필터 입력은 즉시 반영."""

    CSS = """
    HistoryScreen { align: center middle; }
    #history-box { width: 95%; height: 85%; background: $panel; border: solid $primary; }
    #history-table { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "닫기")]

    def __init__(self, index: SessionIndex):
        super().__init__()
        self.index = index
        self._rows = []

    def compose(self) -> ComposeResult:
        with Vertical(id="history-box"):
            yield Input(placeholder="필터 (서버/프로젝트/제목)", id="history-filter")
            yield DataTable(id="history-table")

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_columns("서버", "프로젝트", "제목/첫 프롬프트", "마지막 활동", "역할")
        table.cursor_type = "row"
        self._refresh("")
        table.focus()

    def _refresh(self, needle: str) -> None:
        needle = needle.lower()
        rows = self.index.list_sessions()
        self._rows = [
            r for r in rows
            if needle in f"{r.server} {r.project} {r.title} {r.first_prompt}".lower()
        ]
        table = self.query_one("#history-table", DataTable)
        table.clear()
        for r in self._rows:
            label = r.title or r.first_prompt[:40]
            table.add_row(r.server, r.project[-28:], label, r.last_ts[:19], r.last_role)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        r = self._rows[event.cursor_row]
        self.dismiss((r.server, r.session_id))

    def action_close(self) -> None:
        self.dismiss(None)
```

`src/cchub/tui/app.py` — import에 `HistoryScreen`, `from cchub.results import collect_results`, `from cchub.briefing import generate_briefing` 추가. BINDINGS에 `Binding("h", "history", "이력")`, `Binding("r", "collect_results", "결과수집")`, `Binding("A", "brief", "브리핑")` 추가. 메서드:

```python
    def action_history(self) -> None:
        def _open(result: tuple[str, str] | None) -> None:
            if result:
                self.open_transcript(*result)
        self.push_screen(HistoryScreen(self.index), _open)

    def action_collect_results(self) -> None:
        if not self.cfg.servers:
            self.notify("설정된 서버가 없습니다", severity="warning")
            return
        self.collect_results_worker()

    @work(thread=True, exclusive=True, group="results", exit_on_error=False)
    def collect_results_worker(self) -> None:
        worker = get_current_worker()
        parts = []
        for name in self.cfg.servers:
            if worker.is_cancelled:
                return
            try:
                rep = collect_results(self.cfg, self.root_dir, name, self.remote_factory)
            except Exception as e:  # noqa: BLE001
                parts.append(f"{name}: 오류 {e}")
                continue
            parts.append(f"{name}: {'ok' if rep.ok else '실패 ' + ', '.join(rep.failed)}")
        if worker.is_cancelled:
            return
        self.call_from_thread(self.notify, "결과 수집 — " + "; ".join(parts))

    def action_brief(self) -> None:
        path, prompt = generate_briefing(self.cfg, self.root_dir, self.index)
        self._write_detail(
            f"브리핑 생성됨: {path}\n\n"
            f"아래 프롬프트를 로컬 Claude Code 세션에 붙여넣으세요:\n\n{prompt}"
        )
        self.notify(f"브리핑 생성: {path.name}")
```

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 전체 pass (약 113)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: TUI 이력 화면(h)·결과 수집(r)·브리핑(A)"
```

---

### Task 9: localhost 통합 스모크 + README + 버전 0.3.0

**Files:** Modify: `README.md`, `pyproject.toml` (version), 필요 시 발견 결함 수정
전제: `cchub-smoke` ssh alias 동작. **실제 claude 세션에 send 금지 (읽기 전용).**

- [ ] **Step 1: 실물 통합 테스트 확장** — `tests/test_tui_app.py`의 기존 `test_real_localhost_end_to_end`에 이어 검색/이력/브리핑 실물 검증 추가 (같은 skipif 가드 재사용):

```python
@requires_smoke_ssh
async def test_real_localhost_history_search_brief(tmp_path):
    from cchub.config import Config, ServerConfig
    from cchub.ssh import SSHRemote
    from cchub.tui.screens import HistoryScreen, SearchScreen

    cfg = Config(servers={"local": ServerConfig(name="local", host="cchub-smoke")},
                 sync_interval=3600, stats_interval=3600)
    app = CchubApp(cfg=cfg, root=tmp_path, index=SessionIndex(tmp_path / "i.db"),
                   remote_factory=SSHRemote)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()   # 실제 sync + discover
        await pilot.pause()
        await pilot.press("h")                   # 이력: 실제 세션들이 보임
        assert isinstance(app.screen, HistoryScreen)
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count > 0
        await pilot.press("escape")
        await pilot.press("slash")               # 검색: 실제 이력에서 매칭
        inp = app.screen.query_one("#search-input", Input)
        inp.value = "envector"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.press("A")                   # 브리핑 생성 (로컬 파일만)
        await pilot.pause()
        assert list((tmp_path / "results").glob("briefing-*.md"))
```

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -v -k real_localhost`
Expected: 2 passed (기존 + 신규). 실패 시 실제 결함 — TDD로 수정 후 커밋.

- [ ] **Step 2: 버전/README**

`pyproject.toml`: `version = "0.3.0"`.
`README.md`: 버전 0.3.0; 명령어 표에 `results`/`brief`/`push` 행 추가; TUI 키맵 표에 `/ 검색`, `h 이력`, `r 결과 수집`, `A 브리핑` 추가; "결과 수집·종합 워크플로우" 섹션 (config results 패턴 → `cchub results` 또는 r → `cchub brief` 또는 A → 로컬 Claude에 프롬프트 붙여넣기); same-cwd 제약 갱신 (M3에서 시작시각 페어링으로 완화, 여전히 휴리스틱).

- [ ] **Step 3: 전체 테스트 + 커밋**

```bash
cd ~/cchub && .venv/bin/pytest -q   # 전체 pass 확인
git add -A && git commit -m "docs: M3 기능 README 반영 및 버전 0.3.0"
```

---

## Self-Review 결과 (계획 작성 시 수행)

- 스펙 커버리지: 이력 타임라인(h)+필터, FTS 검색(/)→transcript 진입, 결과 수집(r/`cchub results`, config 패턴 미러), 종합(A/`cchub brief`, 브리핑 파일+붙여넣기 프롬프트, 로컬 claude 자동 실행 없음), 중계(`cchub push`, 서버간은 로컬 경유) — 스펙 M3 범위 전부 매핑. 이월: same-cwd 매칭 개선(fd 불가 실측 → 시작시각 페어링), 전송 전 검증+전송 후 확인, poll_stats 취소, index 중복. 스펙과의 의도적 차이: (1) fd 기반 매칭 → 실측 불가로 페어링 휴리스틱 대체, (2) 이력 "기간 필터" → 부분 문자열 필터로 단순화, (3) TUI 결과 브라우저에서 파일 선택 중계 → CLI push만 (YAGNI).
- 타입 일관성: Pane 5필드, FakeRemote responses의 (argv[0], argv[1]) 튜플 키 확장(Task 3)과 fetch/push 기록(Task 4)이 이후 태스크 테스트와 일치. `_after_send(ok, delivered)` 변경이 Task 3에 기존 테스트 수정 지시 포함. FetchReport/generate_briefing/SearchScreen/HistoryScreen 시그니처 태스크 간 일치 확인.
- 플레이스홀더 없음. Textual API(slash/h/A 키, DataTable, Input.Changed)와 starttime 스크립트는 실행 검증 완료.
