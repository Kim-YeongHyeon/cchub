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
