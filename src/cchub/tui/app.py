from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static, Tree

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

    def action_toggle_stats(self) -> None:
        pass

    def action_toggle_follow(self) -> None:
        pass

    def action_toggle_transcript(self) -> None:
        pass


def run_tui() -> None:
    CchubApp().run()
