from pathlib import Path

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.sessions import LiveSession
from cchub.tui.app import CchubApp
from cchub.tui.data import ServerSnapshot
from textual.widgets import Tree


def make_app(tmp_path: Path) -> CchubApp:
    return CchubApp(
        cfg=Config(servers={}),
        root=tmp_path,
        index=SessionIndex(tmp_path / "i.db"),
        remote_factory=lambda h: None,
    )


def snap(server="srv1", state="idle", error=""):
    ls = LiveSession(server=server, number=1, pane_id="%5", location="main:0.0",
                     cwd="/home/u/proj", project="-home-u-proj",
                     session_id="s-1", title="NUMA 실험", state=state)
    return {server: ServerSnapshot(server=server, sessions=[ls], error=error)}


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


async def test_apply_snapshots_populates_tree(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="working"))
        tree = app.query_one("#tree", Tree)
        server_node = tree.root.children[0]
        assert "srv1" in str(server_node.label)
        leaf = server_node.children[0]
        assert "●" in str(leaf.label) and "NUMA 실험" in str(leaf.label)
        assert leaf.data is not None and leaf.data.session_id == "s-1"


async def test_apply_snapshots_shows_server_error(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(error="connect refused"))
        tree = app.query_one("#tree", Tree)
        assert "⚠" in str(tree.root.children[0].label)


async def test_selecting_leaf_sets_selected(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        tree = app.query_one("#tree", Tree)
        leaf = tree.root.children[0].children[0]
        tree.select_node(leaf)
        await pilot.pause()
        assert app.selected is not None and app.selected.number == 1
