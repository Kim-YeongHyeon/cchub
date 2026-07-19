from pathlib import Path

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.tui.app import CchubApp


def make_app(tmp_path: Path) -> CchubApp:
    return CchubApp(
        cfg=Config(servers={}),
        root=tmp_path,
        index=SessionIndex(tmp_path / "i.db"),
        remote_factory=lambda h: None,
    )


async def test_app_boots_with_core_widgets(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert app.query_one("#stats")
        assert app.query_one("#tree")
        assert app.query_one("#detail")
        assert app.query_one("#prompt")


async def test_q_quits(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.return_value is None  # 종료까지 예외 없이 도달
