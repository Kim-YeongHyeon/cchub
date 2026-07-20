from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, RichLog, Static, Tree
from textual.worker import get_current_worker

from cchub import stats as stats_mod, tmux
from cchub.skills import default_lib_dir, local_skills, scan_skills
from cchub.tmux import CLAUDE_COMMANDS
from cchub.briefing import generate_briefing
from cchub.config import Config, cchub_dir, load_config
from cchub.index import SessionIndex
from cchub.results import collect_results
from cchub.sessions import LiveSession
from cchub.ssh import SSHRemote
from cchub.tui.data import RemoteFactory, ServerSnapshot, collect_sessions
from cchub.tui.screens import SearchScreen, HistoryScreen, SkillsScreen


class ConfirmSend(ModalScreen[bool]):
    CSS = """
    ConfirmSend { align: center middle; }
    #confirm { padding: 1 2; background: $panel; border: solid $warning; }
    """
    BINDINGS = [
        Binding("y", "yes", "전송"),
        Binding("n", "no", "취소"),
        Binding("escape", "no", "취소"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Static(f"{self.message}\n\n[y] 전송   [n] 취소", id="confirm")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


@dataclass
class PaneState:
    session: LiveSession | None = None
    transcript_mode: bool = False
    follow_on: bool = False
    text: str = ""


class CchubApp(App):
    TITLE = "cchub"
    CSS = """
    #stats { height: 1; background: $boost; }
    #stats.hidden { display: none; }
    #body { height: 1fr; }
    #tree { width: 36; border-right: solid $primary; }
    #detail { height: 1fr; }
    """
    BINDINGS = [
        Binding("q", "quit", "종료"),
        Binding("y", "refresh", "동기화"),
        Binding("c", "toggle_stats", "CPU바"),
        Binding("f", "toggle_follow", "팔로우"),
        Binding("t", "toggle_transcript", "transcript"),
        Binding("slash", "search", "검색"),
        Binding("h", "history", "이력"),
        Binding("s", "skills", "스킬"),
        Binding("r", "collect_results", "결과수집"),
        Binding("A", "brief", "브리핑"),
    ]

    _STATE_MARK = {"working": "●", "waiting": "◌", "idle": "▶", "unknown": "?"}

    def __init__(
        self,
        cfg: Config | None = None,
        root: Path | None = None,
        index: SessionIndex | None = None,
        remote_factory: RemoteFactory = SSHRemote,
    ):
        super().__init__()
        self.cfg = cfg or load_config()
        self.root_dir = root or cchub_dir()
        self.index = index or SessionIndex(self.root_dir / "index.db")
        self.remote_factory = remote_factory
        self.panes: list[PaneState] = [PaneState()]
        self.active: int = 0

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

    def on_mount(self) -> None:
        self.snapshots: dict[str, ServerSnapshot] = {}
        self._follow_timer = self.set_interval(2, self._follow_tick, pause=True)
        self.set_interval(self.cfg.sync_interval, self.action_refresh)
        self.stats_on = True
        self.server_stats = {name: stats_mod.ServerStats() for name in self.cfg.servers}
        self._stats_timer = self.set_interval(self.cfg.stats_interval, self.poll_stats)
        self.action_refresh()

    def compose(self) -> ComposeResult:
        yield Static("", id="stats")
        with Horizontal(id="body"):
            yield Tree("서버", id="tree")
            with Vertical():
                yield RichLog(id="detail", wrap=True, markup=False, highlight=False)
                yield Input(placeholder="프롬프트 입력 후 Enter (세션 선택 필요)", id="prompt")
        yield Footer()

    def action_refresh(self) -> None:
        if self.cfg.servers:
            self.load_sessions()

    @work(thread=True, exclusive=True, group="refresh", exit_on_error=False)
    def load_sessions(self) -> None:
        worker = get_current_worker()
        try:
            snaps = collect_sessions(self.cfg, self.root_dir, self.index, self.remote_factory)
        except Exception as e:  # noqa: BLE001 - 주기 갱신 실패가 앱을 죽이면 안 됨
            if worker.is_cancelled:
                return
            self.call_from_thread(self.notify, f"동기화 실패: {e}", severity="error")
            return
        if worker.is_cancelled:
            return
        self.call_from_thread(self.apply_snapshots, snaps)

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

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.selected = event.node.data
        self.show_detail()

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

    def _follow_tick(self) -> None:
        for i, state in enumerate(self.panes):
            if state.follow_on and not state.transcript_mode and state.session:
                self.show_detail(i)

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

    def _update_stats_bar(self, text: str) -> None:
        self.query_one("#stats", Static).update(text)

    def action_toggle_stats(self) -> None:
        self.stats_on = not self.stats_on
        self.query_one("#stats", Static).set_class(not self.stats_on, "hidden")
        if self.stats_on:
            self._stats_timer.resume()
        else:
            self._stats_timer.pause()

    def action_toggle_follow(self) -> None:
        self.follow_on = not self.follow_on
        if any(p.follow_on for p in self.panes):
            self._follow_timer.resume()
        else:
            self._follow_timer.pause()
        self.notify(f"팔로우 {'ON' if self.follow_on else 'OFF'}")

    def action_toggle_transcript(self) -> None:
        self.transcript_mode = not self.transcript_mode
        self.notify(f"transcript 모드 {'ON' if self.transcript_mode else 'OFF(live)'}")
        self.show_detail()

    def action_search(self) -> None:
        def _open(result: tuple[str, str] | None) -> None:
            if result:
                self.open_transcript(*result)
        self.push_screen(SearchScreen(self.index), _open)

    def action_history(self) -> None:
        def _open(result: tuple[str, str] | None) -> None:
            if result:
                self.open_transcript(*result)
        self.push_screen(HistoryScreen(self.index), _open)

    def action_skills(self) -> None:
        if isinstance(self.screen, SkillsScreen):
            return
        screen = SkillsScreen()
        self.push_screen(screen)
        self.load_skills(screen)

    @work(thread=True, exclusive=True, group="skills", exit_on_error=False)
    def load_skills(self, screen: SkillsScreen) -> None:
        worker = get_current_worker()
        rows = local_skills(default_lib_dir())
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

    def open_transcript(self, server: str, session_id: str) -> None:
        # 로컬 sqlite 조회는 ms 단위 — 워커 불필요
        rows = self.index.tail(server, session_id, limit=50)
        text = "\n".join(f"── {r} {t}\n{b}" for r, t, b in rows) or "(transcript 없음)"
        self._write_detail(f"[{server} / {session_id[:8]}]\n{text}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value.strip()
        if not text:
            return
        if self.selected is None:
            self.notify("세션을 먼저 선택하세요", severity="warning")
            return
        if self.selected.state == "working":
            def _decided(ok: bool | None) -> None:
                if ok:
                    self.do_send(text)
            self.push_screen(
                ConfirmSend("세션이 작업 중(●)입니다 — 프롬프트는 큐에 들어갑니다. 보낼까요?"),
                _decided,
            )
        else:
            self.do_send(text)

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


def run_tui() -> None:
    CchubApp().run()
