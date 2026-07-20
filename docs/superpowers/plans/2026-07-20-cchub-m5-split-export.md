# cchub M5 (상세 패널 2분할 + 클립보드 내보내기) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TUI 상세 패널을 `|`로 2분할해 세션 두 개를 나란히 보고, `x`로 활성 패널 내용을 클립보드에 복사한다.

**Architecture:** 기존 단일 상태(`selected/transcript_mode/follow_on`)를 `PaneState` 리스트로 일반화하되, 기존 이름들을 활성 패널을 가리키는 property(+setter)로 유지해 **기존 145개 테스트가 무수정 통과**하는 것을 리팩터링 게이트로 삼는다. 상세 조회 워커는 패널 인덱스별 그룹(`detail-<i>`)으로 분리(동적 그룹이 필요하므로 `@work` 대신 `self.run_worker` 사용).

**Tech Stack:** Textual 8.2.8 — `vertical_line`(`|`)/`o`/`x` 키 이름과 `App.copy_to_clipboard`(OSC52) 실검증 완료.

## Global Constraints

- 기존 테스트 145개는 **수정 없이** 통과해야 한다 (Task 1의 핵심 게이트) — `app.selected`(대입 포함), `app.transcript_mode`, `app.follow_on`, `#detail` id, `_write_detail(text)` 시그니처가 기존 그대로 동작할 것.
- 워커 규약: thread, exclusive(그룹 단위), exit_on_error=False, is_cancelled 가드, UI 반영은 call_from_thread (기존과 동일).
- x는 클립보드 복사만 — 파일 저장 없음 (사용자 결정).
- 활성 패널 전환 키는 `o` (Tab은 Textual 포커스 이동에 예약 — 스펙에 명시됨).
- 테스트 `.venv/bin/pytest`, 브랜치 `m5-split-export`, 커밋 메시지 한국어 짧은 제목·트레일러 없음.

---

### Task 1: PaneState 리팩터링 (동작 불변)

**Files:**
- Modify: `src/cchub/tui/app.py`
- Test: 기존 스위트 전체가 게이트 + `tests/test_tui_app.py`에 PaneState 확인 테스트 1건

**Interfaces:**
- Produces: `app.PaneState(session, transcript_mode, follow_on, text)`; `CchubApp.panes: list[PaneState]`(초기 길이 1), `CchubApp.active: int`(0); property `selected`/`transcript_mode`/`follow_on`(활성 패널 위임, setter 포함); `show_detail(pane: int | None = None)`; `_write_detail_pane(i, text)`(text를 `panes[i].text`에 기록 후 표시); `_detail_log(i) -> RichLog`. 레거시 `_write_detail(text)`는 활성 패널로 위임.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tui_app.py`에 추가

```python
async def test_pane_state_backs_legacy_attrs(tmp_path):
    """selected/transcript_mode/follow_on이 PaneState[active]를 통해 동작한다."""
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert len(app.panes) == 1 and app.active == 0
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        assert app.panes[0].session is app.selected
        await pilot.press("t")
        assert app.panes[0].transcript_mode is True
        await pilot.press("f")
        assert app.panes[0].follow_on is True
        app._write_detail("내용 확인")
        assert app.panes[0].text == "내용 확인"
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -k pane_state -v`
Expected: FAIL — `AttributeError: panes`

- [ ] **Step 3: 구현** — `src/cchub/tui/app.py`

모듈 상단(ConfirmSend 아래)에 추가 (`from dataclasses import dataclass, field` import):

```python
@dataclass
class PaneState:
    session: LiveSession | None = None
    transcript_mode: bool = False
    follow_on: bool = False
    text: str = ""
```

`__init__` 끝에 (on_mount보다 먼저 존재해야 property가 안전):

```python
        self.panes: list[PaneState] = [PaneState()]
        self.active: int = 0
```

property 3종 추가 (클래스 본문):

```python
    @property
    def selected(self) -> LiveSession | None:
        return self.panes[self.active].session

    @selected.setter
    def selected(self, value: LiveSession | None) -> None:
        self.panes[self.active].session = value

    @property
    def transcript_mode(self) -> bool:
        return self.panes[self.active].transcript_mode

    @transcript_mode.setter
    def transcript_mode(self, value: bool) -> None:
        self.panes[self.active].transcript_mode = value

    @property
    def follow_on(self) -> bool:
        return self.panes[self.active].follow_on

    @follow_on.setter
    def follow_on(self, value: bool) -> None:
        self.panes[self.active].follow_on = value
```

`on_mount`에서 `self.selected: LiveSession | None = None`, `self.transcript_mode = False`, `self.follow_on = False` 세 줄을 **삭제** (property가 대체 — 대입하면 setter를 타므로 남겨둬도 무해하지만 타입 주석 줄은 문법상 제거 필요).

`_reconcile_selection`을 패널 순회로 교체:

```python
    def _reconcile_selection(self, tree: Tree) -> None:
        leaves = [leaf for node in tree.root.children for leaf in node.children]
        for i, state in enumerate(self.panes):
            if state.session is None:
                continue
            key = (state.session.server, state.session.pane_id)
            for leaf in leaves:
                ls = leaf.data
                if ls is not None and (ls.server, ls.pane_id) == key:
                    state.session = ls
                    if i == self.active:
                        tree.select_node(leaf)
                    break
            else:
                state.session = None
```

`show_detail`/`refresh_detail`/`_write_detail`을 패널 단위로 교체 (동적 그룹이 필요해 `@work` 대신 `run_worker`; import에서 `work` 유지 — 다른 워커들이 씀):

```python
    def _detail_log(self, i: int) -> RichLog:
        return self.query_one("#detail" if i == 0 else "#detail-1", RichLog)

    def show_detail(self, pane: int | None = None) -> None:
        i = self.active if pane is None else pane
        if i < len(self.panes) and self.panes[i].session is not None:
            self.run_worker(
                lambda: self._fetch_detail(i),
                group=f"detail-{i}", exclusive=True, thread=True, exit_on_error=False,
            )

    def _fetch_detail(self, i: int) -> None:
        worker = get_current_worker()
        state = self.panes[i]
        ls = state.session
        if ls is None:
            return
        if state.transcript_mode:
            try:
                rows = (
                    self.index.tail(ls.server, ls.session_id, limit=30)
                    if ls.session_id else []
                )
            except Exception as e:  # noqa: BLE001
                if worker.is_cancelled:
                    return
                self.call_from_thread(self.notify, f"transcript 조회 실패: {e}", severity="error")
                return
            text = "\n".join(f"── {role} {ts}\n{body}" for role, ts, body in rows) \
                   or "(transcript 없음 — 동기화 대기)"
        else:
            try:
                remote = self.remote_factory(self.cfg.servers[ls.server].host)
                text = tmux.capture(remote, ls.pane_id, lines=200) or "(캡처 실패)"
            except Exception as e:  # noqa: BLE001
                if worker.is_cancelled:
                    return
                self.call_from_thread(self.notify, f"상세 조회 실패: {e}", severity="error")
                return
        if worker.is_cancelled:
            return
        self.call_from_thread(self._write_detail_pane, i, text)

    def _write_detail_pane(self, i: int, text: str) -> None:
        if i >= len(self.panes):
            return
        self.panes[i].text = text
        log = self._detail_log(i)
        log.clear()
        log.write(text)

    def _write_detail(self, text: str) -> None:
        self._write_detail_pane(self.active, text)
```

(기존 `refresh_detail` 메서드는 삭제 — `_fetch_detail`이 대체. `tests/test_tui_app.py`의 기존 테스트들은 `show_detail()`/press 경유라 영향 없음을 전체 스위트로 확인.)

`_follow_tick`을 패널 순회로 교체, `action_toggle_follow`는 "하나라도 켜져 있으면 타이머 resume":

```python
    def _follow_tick(self) -> None:
        for i, state in enumerate(self.panes):
            if state.follow_on and not state.transcript_mode and state.session:
                self.show_detail(i)

    def action_toggle_follow(self) -> None:
        self.follow_on = not self.follow_on
        if any(p.follow_on for p in self.panes):
            self._follow_timer.resume()
        else:
            self._follow_timer.pause()
        self.notify(f"팔로우 {'ON' if self.follow_on else 'OFF'}")
```

- [ ] **Step 4: 통과 확인 (전체 — 리팩터링 게이트)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 146 passed (기존 145 무수정 + 신규 1)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "refactor: 상세 패널 상태를 PaneState로 일반화 (동작 불변)"
```

---

### Task 2: x — 활성 패널 내용 클립보드 복사

**Files:**
- Modify: `src/cchub/tui/app.py`
- Test: `tests/test_tui_app.py`

**Interfaces:**
- Consumes: Task 1의 `panes[active].text`
- Produces: `Binding("x", "copy_detail", "복사")`, `action_copy_detail()` — 내용 없으면 warning notify, 있으면 `self.copy_to_clipboard(text)` + "클립보드로 복사됨 (N자)" notify

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tui_app.py`에 추가

```python
async def test_x_copies_active_pane_text(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    copied = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "copy_to_clipboard", lambda t: copied.append(t))
        await pilot.press("x")            # 내용 없음 → 복사 안 됨
        assert copied == []
        app._write_detail("복사할 내용")
        await pilot.press("x")
        assert copied == ["복사할 내용"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -k copies -v`
Expected: FAIL — x 바인딩 없음 (copied가 비어 있고 두 번째 단언 실패)

- [ ] **Step 3: 구현** — BINDINGS에 `Binding("x", "copy_detail", "복사")` 추가, 메서드:

```python
    def action_copy_detail(self) -> None:
        text = self.panes[self.active].text
        if not text.strip():
            self.notify("복사할 내용이 없습니다 — 세션을 먼저 선택하세요", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify(f"클립보드로 복사됨 ({len(text)}자)")
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 147 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: x 키로 활성 패널 내용 클립보드 복사 (OSC52)"
```

---

### Task 3: `|` 2분할 + `o` 활성 전환

**Files:**
- Modify: `src/cchub/tui/app.py`
- Test: `tests/test_tui_app.py`

**Interfaces:**
- Consumes: Task 1 전부
- Produces: `Binding("vertical_line", "toggle_split", "분할")`, `Binding("o", "switch_pane", "패널전환")`; `action_toggle_split()` — 분할 시 `#detail-1` RichLog mount + `panes.append(PaneState())`, 해제 시 remove + `panes.pop()` + `active=0`; `action_switch_pane()` — 분할 상태에서만 active 토글; 활성 패널 강조 CSS 클래스 `active-pane`(분할 상태에서만 부여); compose의 상세 영역이 `#detail-row`(Horizontal)로 감싸짐

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tui_app.py`에 추가

```python
async def test_split_toggle_and_switch(tmp_path):
    from textual.css.query import NoMatches

    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert len(app.panes) == 1
        await pilot.press("|")            # 분할
        await pilot.pause()
        assert len(app.panes) == 2
        assert app.query_one("#detail-1", RichLog)
        assert app.active == 0
        assert app.query_one("#detail", RichLog).has_class("active-pane")
        await pilot.press("o")            # 전환
        assert app.active == 1
        assert app.query_one("#detail-1", RichLog).has_class("active-pane")
        assert not app.query_one("#detail", RichLog).has_class("active-pane")
        await pilot.press("|")            # 해제
        await pilot.pause()
        assert len(app.panes) == 1 and app.active == 0
        with pytest.raises(NoMatches):
            app.query_one("#detail-1", RichLog)


async def test_split_selection_goes_to_active_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")            # 활성 = 패널 1
        tree = app.query_one("#tree", Tree)
        leaf = tree.root.children[0].children[0]
        tree.select_node(leaf)
        await pilot.pause()
        assert app.panes[1].session is not None   # 활성 패널에 할당
        assert app.panes[0].session is None


async def test_split_independent_transcript_mode(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")
        await pilot.press("t")            # 패널 1만 transcript
        assert app.panes[1].transcript_mode is True
        assert app.panes[0].transcript_mode is False


async def test_unsplit_while_pane1_active_resets(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")
        await pilot.press("|")            # 활성이 1인 채로 해제
        await pilot.pause()
        assert app.active == 0 and len(app.panes) == 1
        await pilot.press("o")            # 단일 상태에서 o는 no-op
        assert app.active == 0
```

(`import pytest`가 파일에 없으면 추가.)

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -k "split or unsplit" -v`
Expected: 4건 FAIL (분할 바인딩 없음)

- [ ] **Step 3: 구현**

compose의 상세 영역을 Horizontal로 감싼다:

```python
            with Vertical():
                with Horizontal(id="detail-row"):
                    yield RichLog(id="detail", wrap=True, markup=False, highlight=False)
                yield Input(placeholder="프롬프트 입력 후 Enter (세션 선택 필요)", id="prompt")
```

CSS 추가 (기존 `#detail { height: 1fr; }` 교체):

```css
    #detail-row { height: 1fr; }
    #detail-row RichLog { width: 1fr; height: 1fr; }
    #detail-row RichLog.active-pane { border: solid $accent; }
```

BINDINGS 추가:

```python
        Binding("vertical_line", "toggle_split", "분할"),
        Binding("o", "switch_pane", "패널전환"),
```

메서드 추가:

```python
    def action_toggle_split(self) -> None:
        row = self.query_one("#detail-row", Horizontal)
        if len(self.panes) == 1:
            self.panes.append(PaneState())
            row.mount(RichLog(id="detail-1", wrap=True, markup=False, highlight=False))
            self.call_after_refresh(self._update_active_classes)
        else:
            self.query_one("#detail-1", RichLog).remove()
            self.panes.pop()
            self.active = 0
            self._update_active_classes()

    def action_switch_pane(self) -> None:
        if len(self.panes) < 2:
            return
        self.active = 1 - self.active
        self._update_active_classes()

    def _update_active_classes(self) -> None:
        split = len(self.panes) > 1
        for i in range(len(self.panes)):
            try:
                self._detail_log(i).set_class(split and i == self.active, "active-pane")
            except Exception:  # noqa: BLE001 - mount 직후 타이밍
                pass
```

(`on_tree_node_selected`는 Task 1의 property 덕에 이미 활성 패널에 할당됨 — 변경 불필요. `_after_send`의 `show_detail()`도 활성 패널 대상이라 그대로.)

- [ ] **Step 4: 통과 확인 (전체)**

Run: `cd ~/cchub && .venv/bin/pytest -q`
Expected: 151 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add -A && git commit -m "feat: 상세 패널 2분할(|)과 활성 패널 전환(o)"
```

---

### Task 4: 실물 스모크 + README + 버전 0.5.0 + GitHub push

**Files:** Modify: `README.md`, `pyproject.toml`, `tests/test_tui_app.py`
전제: `cchub-smoke` alias 동작, 실제 claude 세션 존재. **실제 세션에 send 금지 (읽기 전용).**

- [ ] **Step 1: 실물 통합 테스트** — `tests/test_tui_app.py`에 추가 (기존 requires_smoke_ssh 재사용)

```python
@requires_smoke_ssh
async def test_real_split_view_two_sessions(tmp_path):
    """실제 SSH로 분할 뷰에 세션을 각각 할당하고 두 패널이 채워지는지 확인 (읽기 전용)."""
    from cchub.config import Config, ServerConfig
    from cchub.ssh import SSHRemote

    cfg = Config(servers={"local": ServerConfig(name="local", host="cchub-smoke")},
                 sync_interval=3600, stats_interval=3600)
    app = CchubApp(cfg=cfg, root=tmp_path, index=SessionIndex(tmp_path / "i.db"),
                   remote_factory=SSHRemote)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        tree = app.query_one("#tree", Tree)
        leaves = [l for n in tree.root.children for l in n.children]
        if not leaves:
            pytest.skip("실행 중인 claude 세션 없음")
        await pilot.press("|")
        await pilot.pause()
        tree.select_node(leaves[0])                  # 패널 0
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("o")
        tree.select_node(leaves[-1])                 # 패널 1 (세션 1개면 같은 세션)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.panes[0].text and app.panes[1].text   # 두 패널 모두 내용 표시
```

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tui_app.py -v -k real_split`
Expected: PASS. 실패 시 실제 결함 — TDD로 수정.

- [ ] **Step 2: README + 버전**

`pyproject.toml`: `version = "0.5.0"`.
`README.md`: TUI 키맵에 `x`(활성 패널 내용 클립보드 복사 — OSC52, live 화면도 복사 가능), `|`(상세 패널 2분할 토글), `o`(활성 패널 전환 — 활성 패널에 테두리 강조; 선택/전송/f/t/x는 활성 패널 대상) 행 추가; "로컬 Claude Code와 함께 쓰기" 섹션에 x로 복사→붙여넣기 흐름과 **로컬 머신을 서버로 등록하는 가이드**(자기 자신으로의 ssh alias 등록 → `[servers.local] host = "<alias>"` — 로컬 tmux의 claude 세션도 트리에 나타남) 추가; 버전 히스토리에 0.5.0 항목.

- [ ] **Step 3: 전체 테스트 + 커밋 + push**

```bash
cd ~/cchub && .venv/bin/pytest -q   # 전체 pass 확인 (약 152)
git add -A && git commit -m "docs: 분할·복사 키맵 README 반영 및 버전 0.5.0"
```

(push는 브랜치 머지 후 master에서 수행 — finishing 단계.)

---

## Self-Review 결과 (계획 작성 시 수행)

- 스펙 커버리지: PaneState 모델·property 호환·패널별 워커 그룹(run_worker 동적 그룹)·x 클립보드(파일 저장 없음)·`|`/`o` 키(Tab 대신 o, 스펙 반영)·활성 강조 CSS·독립 팔로우/transcript·해제 시 상태 정리·로컬 서버 등록 가이드(README) — 스펙 전 항목 매핑. 범위 외(3분할/파일 내보내기/패널별 입력창) 미포함.
- 타입 일관성: `_detail_log(i)`/`show_detail(pane)`/`_write_detail_pane(i, text)`/`_fetch_detail(i)`/`panes`/`active` — 태스크 간 일치. 기존 테스트가 쓰는 `app.selected`(getter+setter)/`transcript_mode`/`follow_on`/`_write_detail`/`#detail` 전부 보존.
- 키 이름(`vertical_line`)·`copy_to_clipboard`·`run_worker` 동적 그룹은 설치된 textual 8.2.8에서 실검증 완료. 플레이스홀더 없음.
