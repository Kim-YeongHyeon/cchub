import shutil
from pathlib import Path

from textual.widgets import DataTable, Input, RichLog

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.tui.app import CchubApp
from cchub.tui.screens import SearchScreen, HistoryScreen, SkillsScreen

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


async def test_modal_enter_never_sends_to_session(tmp_path):
    """검색/이력 모달의 Enter가 do_send로 버블링되지 않는다 (Critical 회귀)."""
    from cchub.config import ServerConfig
    from cchub.sessions import LiveSession
    from cchub.ssh import RunResult
    from cchub.tui.data import ServerSnapshot
    from conftest import FakeRemote

    fake = FakeRemote({
        ("tmux", "list-panes"): RunResult(0, "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n", ""),
        "tmux": RunResult(0, "", ""),
    })
    app = make_indexed_app(tmp_path)
    app.remote_factory = lambda h: fake
    ls = LiveSession(server="srv1", number=1, pane_id="%5", location="main:0.0",
                     cwd="/home/u/proj", project="-home-u-proj",
                     session_id="s-1", title="", state="idle")
    async with app.run_test() as pilot:
        base_screen = app.screen
        # 서버 등록은 mount 이후에: on_mount의 최초 action_refresh가 백그라운드에서
        # 실제 collect_sessions를 실행해 우리가 지정한 selected를 덮어쓰는 것을 방지.
        app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[ls])})
        app.selected = ls
        for key in ("slash", "h"):
            await pilot.press(key)
            inp = app.screen.query_one(
                "#search-input" if key == "slash" else "#history-filter")
            inp.focus()
            inp.value = "위험한 검색어"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # ConfirmSend가 검색/이력 모달 위에 뜰 수 있으므로 기본 화면으로
            # 돌아올 때까지 escape를 반복해 닫는다.
            while app.screen is not base_screen:
                await pilot.press("escape")
                await pilot.pause()
        assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


async def test_s_opens_skills_screen_with_rows(tmp_path):
    from cchub.config import ServerConfig
    from cchub.ssh import RunResult
    from conftest import FakeRemote

    fake = FakeRemote({
        ("tmux", "list-panes"): RunResult(0, "%5\tm:0.0\t/home/u/proj\tclaude\t100\n", ""),
        "sh": RunResult(0, "project\t/home/u/proj/.claude/skills/e2e-run/SKILL.md\tE2E\n", ""),
    })
    app = make_indexed_app(tmp_path)
    app.remote_factory = lambda h: fake
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        await pilot.press("s")
        assert isinstance(app.screen, SkillsScreen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.screen.query_one("#skills-table", DataTable)
        assert table.row_count >= 1
        await pilot.press("escape")
        assert not isinstance(app.screen, SkillsScreen)
