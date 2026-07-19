from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static, Tree

from cchub import tmux
from cchub.config import Config, cchub_dir, load_config
from cchub.index import SessionIndex
from cchub.sessions import LiveSession
from cchub.ssh import SSHRemote
from cchub.tui.data import RemoteFactory, ServerSnapshot, collect_sessions


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

    def on_mount(self) -> None:
        self.selected: LiveSession | None = None
        self.snapshots: dict[str, ServerSnapshot] = {}
        self.transcript_mode = False
        self.follow_on = False
        self._follow_timer = self.set_interval(2, self._follow_tick, pause=True)
        self.set_interval(self.cfg.sync_interval, self.action_refresh)
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
        try:
            snaps = collect_sessions(self.cfg, self.root_dir, self.index, self.remote_factory)
        except Exception as e:  # noqa: BLE001 - 주기 갱신 실패가 앱을 죽이면 안 됨
            self.call_from_thread(self.notify, f"동기화 실패: {e}", severity="error")
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
        if self.selected is None:
            return
        key = (self.selected.server, self.selected.pane_id)
        for server_node in tree.root.children:
            for leaf in server_node.children:
                ls = leaf.data
                if ls is not None and (ls.server, ls.pane_id) == key:
                    self.selected = ls
                    tree.select_node(leaf)
                    return
        self.selected = None

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data is not None:
            self.selected = event.node.data
        self.show_detail()

    def show_detail(self) -> None:
        if self.selected is not None:
            self.refresh_detail()

    @work(thread=True, exclusive=True, group="detail", exit_on_error=False)
    def refresh_detail(self) -> None:
        ls = self.selected
        if ls is None:
            return
        if self.transcript_mode:
            rows = (
                self.index.tail(ls.server, ls.session_id, limit=30)
                if ls.session_id else []
            )
            text = "\n".join(f"── {role} {ts}\n{body}" for role, ts, body in rows) \
                   or "(transcript 없음 — 동기화 대기)"
        else:
            try:
                remote = self.remote_factory(self.cfg.servers[ls.server].host)
                text = tmux.capture(remote, ls.pane_id, lines=200) or "(캡처 실패)"
            except Exception as e:  # noqa: BLE001
                self.call_from_thread(self.notify, f"상세 조회 실패: {e}", severity="error")
                return
        self.call_from_thread(self._write_detail, text)

    def _write_detail(self, text: str) -> None:
        log = self.query_one("#detail", RichLog)
        log.clear()
        log.write(text)

    def _follow_tick(self) -> None:
        if self.follow_on and not self.transcript_mode:
            self.show_detail()

    def action_toggle_stats(self) -> None:
        pass

    def action_toggle_follow(self) -> None:
        self.follow_on = not self.follow_on
        if self.follow_on:
            self._follow_timer.resume()
        else:
            self._follow_timer.pause()
        self.notify(f"팔로우 {'ON' if self.follow_on else 'OFF'}")

    def action_toggle_transcript(self) -> None:
        self.transcript_mode = not self.transcript_mode
        self.notify(f"transcript 모드 {'ON' if self.transcript_mode else 'OFF(live)'}")
        self.show_detail()


def run_tui() -> None:
    CchubApp().run()
