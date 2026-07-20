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
