from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input

from cchub.index import SessionIndex, SessionRow
from cchub.skills import SkillInfo


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
        event.stop()
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
        self._rows: list[SessionRow] = []

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

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#history-table", DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        r = self._rows[event.cursor_row]
        self.dismiss((r.server, r.session_id))

    def action_close(self) -> None:
        self.dismiss(None)


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
        if not table.columns:
            table.add_columns("서버", "scope", "이름", "설명")
        table.loading = False
        table.clear()
        for i in rows:
            table.add_row(i.server, i.scope, i.name, i.description[:80])

    def action_close(self) -> None:
        self.dismiss(None)
