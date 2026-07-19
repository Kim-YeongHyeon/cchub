from pathlib import Path

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.sessions import LiveSession
from cchub.ssh import RunResult
from cchub.tui.app import CchubApp
from cchub.tui.data import ServerSnapshot
from conftest import FakeRemote
from textual.widgets import Input, RichLog, Tree


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


async def test_refresh_reconciles_selected_to_fresh_object(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="idle"))
        tree = app.query_one("#tree", Tree)
        leaf = tree.root.children[0].children[0]
        tree.select_node(leaf)
        await pilot.pause()
        old = app.selected
        app.apply_snapshots(snap(state="working"))   # 같은 pane_id, 새 객체
        assert app.selected is not old
        assert app.selected.state == "working"       # 새 객체로 교체됨


async def test_refresh_clears_selected_when_pane_gone(tmp_path):
    from cchub.tui.data import ServerSnapshot
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        tree = app.query_one("#tree", Tree)
        tree.select_node(tree.root.children[0].children[0])
        await pilot.pause()
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[])})
        assert app.selected is None


async def test_load_sessions_survives_collect_exception(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod
    def boom(*a, **k):
        raise RuntimeError("네트워크 붕괴")
    monkeypatch.setattr(app_mod, "collect_sessions", boom)
    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        app.load_sessions()
        try:
            await app.workers.wait_for_complete()
        except Exception:
            pass  # Worker may be cancelled; we just care the app survives
        await pilot.pause()
        assert app.is_running   # 앱이 죽지 않음


def make_app_with_remote(tmp_path, fake):
    app = make_app(tmp_path)
    app.remote_factory = lambda h: fake
    # cfg에 서버가 있어야 remote_factory가 조회됨
    from cchub.config import ServerConfig
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    return app


async def test_live_detail_shows_capture(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, "화면 캡처 내용\n", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        app.show_detail()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any(c[:2] == ["tmux", "capture-pane"] for c in fake.calls)


async def test_transcript_mode_reads_index_not_tmux(tmp_path):
    fake = FakeRemote()
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        await pilot.press("t")           # transcript 모드 토글
        await app.workers.wait_for_complete()
        assert app.transcript_mode is True
        assert not any(c[:2] == ["tmux", "capture-pane"] for c in fake.calls)


async def test_follow_toggle_flips_state(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert app.follow_on is False
        await pilot.press("f")
        assert app.follow_on is True
        await pilot.press("f")
        assert app.follow_on is False


async def test_submit_sends_prompt_to_selected_session(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, "", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="idle"))
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "실험 시작해줘"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        sent = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
        assert sent[0] == ["tmux", "send-keys", "-t", "%5", "-l", "--", "실험 시작해줘"]
        assert inp.value == ""  # 성공 시 입력창 비움


async def test_submit_without_selection_warns_and_does_not_send(tmp_path):
    fake = FakeRemote()
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "아무거나"
        await pilot.press("enter")
        await pilot.pause()
        assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


async def test_working_session_asks_confirmation(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, "", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="working"))
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "큐잉될 프롬프트"
        await pilot.press("enter")
        await pilot.pause()
        from cchub.tui.app import ConfirmSend
        assert isinstance(app.screen, ConfirmSend)  # 모달 떠 있음
        await pilot.press("n")                       # 취소
        await pilot.pause()
        assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


async def test_working_session_confirm_y_sends(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, "", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="working"))
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "큐잉될 프롬프트"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("y")          # 확인 → 전송
        await app.workers.wait_for_complete()
        await pilot.pause()
        sent = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
        assert sent[0] == ["tmux", "send-keys", "-t", "%5", "-l", "--", "큐잉될 프롬프트"]
        assert inp.value == ""


PROC_OUT = """cpu  100 0 100 700 100 0 0 0 0 0
MemTotal:       65536000 kB
MemAvailable:   32768000 kB
"""


async def test_stats_bar_updates_from_poll(tmp_path):
    fake = FakeRemote({"cat": RunResult(0, PROC_OUT, "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.poll_stats()
        await app.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import Static
        bar = app.query_one("#stats", Static)
        assert "srv1" in str(bar.content)   # 첫 샘플이라 %는 없어도 이름은 표시


async def test_c_toggles_stats_bar_and_polling(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert app.stats_on is True
        await pilot.press("c")
        assert app.stats_on is False
        assert app.query_one("#stats").has_class("hidden")
        await pilot.press("c")
        assert app.stats_on is True
        assert not app.query_one("#stats").has_class("hidden")
