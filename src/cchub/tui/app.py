from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static, Tree

from cchub.config import Config, cchub_dir, load_config
from cchub.index import SessionIndex
from cchub.ssh import SSHRemote
from cchub.tui.data import RemoteFactory


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

    def compose(self) -> ComposeResult:
        yield Static("", id="stats")
        with Horizontal(id="body"):
            yield Tree("서버", id="tree")
            with Vertical():
                yield RichLog(id="detail", wrap=True, markup=False, highlight=False)
                yield Input(placeholder="프롬프트 입력 후 Enter (세션 선택 필요)", id="prompt")
        yield Footer()

    # 이후 태스크에서 액션/워커 메서드 추가. 지금은 no-op 액션으로 바인딩만 유효화.
    def action_refresh(self) -> None:
        pass

    def action_toggle_stats(self) -> None:
        pass

    def action_toggle_follow(self) -> None:
        pass

    def action_toggle_transcript(self) -> None:
        pass


def run_tui() -> None:
    CchubApp().run()
