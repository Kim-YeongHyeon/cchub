import subprocess
from pathlib import Path

import pytest

from cchub.config import Config
from cchub.index import SessionIndex
from cchub.sessions import LiveSession
from cchub.ssh import RunResult
from cchub.tui.app import CchubApp
from cchub.tui.data import ServerSnapshot
from conftest import FakeRemote
from textual.widgets import DataTable, Input, RichLog, Tree

requires_smoke_ssh = pytest.mark.skipif(
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
                    "cchub-smoke", "true"], capture_output=True).returncode != 0,
    reason="cchub-smoke ssh alias 불가",
)


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


# 주의: 실제 스레드 경합(워커 A를 블로킹시키고 워커 B가 먼저 끝나게 하는 방식)으로
# "취소된 워커가 UI를 덮지 않는다"를 검증하는 테스트는 on_mount()가 이미 같은
# exclusive 그룹("refresh")으로 load_sessions()를 한 번 돌리기 때문에 A/B 두 워커만
# 있다고 가정한 타이밍이 성립하지 않아 실제로 껐다 켰다 하며 flaky했다(5회 중 2회
# 성공/3회 실패를 직접 확인). 그래서 여기서는 대신 get_current_worker()를 가짜
# 취소 상태로 monkeypatch해서 "cancelled면 절대 UI를 갱신하지 않는다"를 결정적으로
# 검증한다 — 세 워커(load_sessions/poll_stats/refresh_detail) 각각에 대해.

async def test_load_sessions_skips_ui_update_when_cancelled(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod

    class FakeCancelledWorker:
        is_cancelled = True

    monkeypatch.setattr(app_mod, "get_current_worker", lambda: FakeCancelledWorker())
    marker = {"srv1": ServerSnapshot(server="srv1", sessions=[])}
    monkeypatch.setattr(app_mod, "collect_sessions", lambda *a, **k: marker)
    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        app.load_sessions()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # collect_sessions는 marker를 반환했지만, 취소된 워커는 apply_snapshots를
        # 호출하지 않으므로 snapshots는 여전히 빈 상태여야 한다.
        assert app.snapshots == {}


async def test_poll_stats_skips_ui_update_when_cancelled(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod

    class FakeCancelledWorker:
        is_cancelled = True

    monkeypatch.setattr(app_mod, "get_current_worker", lambda: FakeCancelledWorker())
    fake = FakeRemote({"cat": RunResult(0, PROC_OUT, "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        from textual.widgets import Static
        bar = app.query_one("#stats", Static)
        before = str(bar.content)
        app.poll_stats()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert str(bar.content) == before   # 취소된 워커는 stats bar를 갱신하지 않음


async def test_poll_stats_cancel_mid_loop_stops_tracker_updates(tmp_path, monkeypatch):
    """루프 도중 취소되면 남은 서버의 tracker.update가 실행되지 않는다."""
    import cchub.tui.app as app_mod

    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["a"] = ServerConfig(name="a", host="u@a")
    app.cfg.servers["b"] = ServerConfig(name="b", host="u@b")

    calls = []

    class FakeWorker:
        def __init__(self):
            self._n = 0

        @property
        def is_cancelled(self):
            # 첫 iteration 후부터 취소된 것으로 응답
            self._n += 1
            return self._n > 1

    monkeypatch.setattr(app_mod, "get_current_worker", lambda: FakeWorker())
    monkeypatch.setattr(app_mod.stats_mod, "read_stats",
                        lambda remote: calls.append(1) or None)
    app.remote_factory = lambda h: None
    async with app.run_test() as pilot:
        app.poll_stats()
        await app.workers.wait_for_complete()
        assert len(calls) <= 1  # 두 번째 서버는 폴링되지 않음


async def test_refresh_detail_skips_write_when_cancelled(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod

    class FakeCancelledWorker:
        is_cancelled = True

    monkeypatch.setattr(app_mod, "get_current_worker", lambda: FakeCancelledWorker())
    fake = FakeRemote({"tmux": RunResult(0, "화면 캡처 내용\n", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        log = app.query_one("#detail", RichLog)
        app.show_detail()
        await app.workers.wait_for_complete()
        await pilot.pause()
        # tmux.capture는 성공했지만, 취소된 워커는 _write_detail을 호출하지 않으므로
        # 로그는 여전히 비어 있어야 한다.
        assert not log.lines


async def test_transcript_error_notifies(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        app.transcript_mode = True

        def boom(*a, **k):
            raise RuntimeError("db 깨짐")

        monkeypatch.setattr(app.index, "tail", boom)
        app.show_detail()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running   # 앱 생존 + (notify는 크래시 없이 처리됨)


async def test_do_send_selected_none_warns_instead_of_silent_return(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.selected = None
        app.do_send("아무거나")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.is_running   # 크래시 없이 경고 처리됨


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
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n", ""),
                       "tmux": RunResult(0, "", "")})
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


async def test_send_to_vanished_pane_notifies_and_skips(tmp_path):
    # tmux list-panes가 빈 결과 → verify_pane 실패 → send-keys 미실행
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, "", "")})
    app = make_app_with_remote(tmp_path, fake)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap(state="idle"))
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        inp = app.query_one("#prompt", Input)
        inp.focus()
        inp.value = "보내지면 안 됨"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


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
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n", ""),
                       "tmux": RunResult(0, "", "")})
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
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, "%5\tmain:0.0\t/home/u/proj\tclaude\t100\n", ""),
                       "tmux": RunResult(0, "", "")})
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


async def test_unsplit_cancels_stale_detail_worker(tmp_path):
    """해제→재분할 시 이전 패널의 느린 워커가 새 패널을 오염시키지 않는다."""
    import threading

    app = make_app(tmp_path)
    release = threading.Event()

    def slow_stale_fetch():
        release.wait(timeout=10)
        from textual.worker import get_current_worker
        if get_current_worker().is_cancelled:
            return
        app.call_from_thread(app._write_detail_pane, 1, "STALE")

    async with app.run_test() as pilot:
        await pilot.press("|")            # 분할
        await pilot.pause()
        app.run_worker(slow_stale_fetch, group="detail-1", exclusive=True,
                       thread=True, exit_on_error=False)
        await pilot.pause()
        await pilot.press("|")            # 해제 (워커 취소돼야 함)
        await pilot.pause()
        await pilot.press("|")            # 재분할
        await pilot.pause()
        release.set()                     # 낡은 워커 해제
        try:
            await app.workers.wait_for_complete()
        except Exception:
            pass  # 취소된 워커는 WorkerCancelled 예외를 던질 수 있음
        await pilot.pause()
        assert app.panes[1].text != "STALE"   # 새 패널 오염 없음


async def test_unsplit_pauses_follow_timer_if_only_pane1_followed(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")
        await pilot.press("f")            # 패널 1만 팔로우
        paused = []
        monkeypatch.setattr(app._follow_timer, "pause", lambda: paused.append(True))
        await pilot.press("|")            # 해제
        await pilot.pause()
        assert paused                     # 타이머 pause가 실제 호출됨
        assert not any(p.follow_on for p in app.panes)


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


async def test_r_collects_results_via_worker(tmp_path, monkeypatch):
    import cchub.tui.app as app_mod
    from cchub.results import FetchReport

    collected = []

    def fake_collect(cfg, root, server, remote_factory):
        collected.append(server)
        return FetchReport(server=server, ok=True)

    monkeypatch.setattr(app_mod, "collect_results", fake_collect)
    app = make_app(tmp_path)
    from cchub.config import ServerConfig
    app.cfg.servers["srv1"] = ServerConfig(name="srv1", host="u@h")
    async with app.run_test() as pilot:
        await pilot.press("r")
        await app.workers.wait_for_complete()
        assert collected == ["srv1"]


async def test_A_generates_briefing_and_shows_prompt(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("A")
        await pilot.pause()
        briefs = list((tmp_path / "results").glob("briefing-*.md"))
        assert len(briefs) == 1


@requires_smoke_ssh
async def test_real_localhost_end_to_end(tmp_path):
    """실제 SSH로 sync→discover→stats까지 한 바퀴 (send 없음, 읽기 전용)."""
    from cchub.config import ServerConfig
    from cchub.ssh import SSHRemote

    cfg = Config(servers={"local": ServerConfig(name="local", host="cchub-smoke")},
                 sync_interval=3600, stats_interval=3600)
    app = CchubApp(cfg=cfg, root=tmp_path, index=SessionIndex(tmp_path / "i.db"),
                   remote_factory=SSHRemote)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()   # on_mount의 초기 load_sessions
        await pilot.pause()
        tree = app.query_one("#tree", Tree)
        assert tree.root.children                 # local 서버 노드 존재
        app.poll_stats()
        await app.workers.wait_for_complete()
        await pilot.pause()
        from textual.widgets import Static
        assert "local" in str(app.query_one("#stats", Static).content)


@requires_smoke_ssh
async def test_real_localhost_history_search_brief(tmp_path):
    from cchub.config import Config, ServerConfig
    from cchub.ssh import SSHRemote
    from cchub.tui.screens import HistoryScreen, SearchScreen

    cfg = Config(servers={"local": ServerConfig(name="local", host="cchub-smoke")},
                 sync_interval=3600, stats_interval=3600)
    app = CchubApp(cfg=cfg, root=tmp_path, index=SessionIndex(tmp_path / "i.db"),
                   remote_factory=SSHRemote)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()   # 실제 sync + discover
        await pilot.pause()
        await pilot.press("h")                   # 이력: 실제 세션들이 보임
        assert isinstance(app.screen, HistoryScreen)
        table = app.screen.query_one("#history-table", DataTable)
        assert table.row_count > 0
        await pilot.press("escape")
        await pilot.press("slash")               # 검색: 실제 이력에서 매칭
        inp = app.screen.query_one("#search-input", Input)
        inp.value = "envector"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.press("A")                   # 브리핑 생성 (로컬 파일만)
        await pilot.pause()
        assert list((tmp_path / "results").glob("briefing-*.md"))


@requires_smoke_ssh
async def test_real_split_view_two_sessions(tmp_path):
    """실제 SSH로 분할 뷰에 세션을 각각 할당하고 두 패널이 채워지는지 확인 (읽기 전용)."""
    from cchub.config import Config, ServerConfig
    from cchub.ssh import SSHRemote

    cfg = Config(servers={"local": ServerConfig(name="local", host="cchub-smoke")},
                 sync_interval=3600, stats_interval=3600)
    app = CchubApp(cfg=cfg, root=tmp_path, index=SessionIndex(tmp_path / "i.db"),
                   remote_factory=SSHRemote)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        tree = app.query_one("#tree", Tree)
        leaves = [l for n in tree.root.children for l in n.children]
        if not leaves:
            pytest.skip("실행 중인 claude 세션 없음")
        await pilot.press("|")
        await pilot.pause()
        tree.select_node(leaves[0])                  # 패널 0
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("o")
        tree.select_node(leaves[-1])                 # 패널 1 (세션 1개면 같은 세션)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.panes[0].text and app.panes[1].text   # 두 패널 모두 내용 표시


async def test_pane_state_backs_legacy_attrs(tmp_path):
    """selected/transcript_mode/follow_on이 PaneState[active]를 통해 동작한다."""
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert len(app.panes) == 1 and app.active == 0
        app.apply_snapshots(snap())
        app.selected = list(app.snapshots["srv1"].sessions)[0]
        assert app.panes[0].session is app.selected
        await pilot.press("t")
        assert app.panes[0].transcript_mode is True
        await pilot.press("f")
        assert app.panes[0].follow_on is True
        app._write_detail("내용 확인")
        assert app.panes[0].text == "내용 확인"


async def test_x_copies_active_pane_text(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    copied = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "copy_to_clipboard", lambda t: copied.append(t))
        await pilot.press("x")            # 내용 없음 → 복사 안 됨
        assert copied == []
        app._write_detail("복사할 내용")
        await pilot.press("x")
        assert copied == ["복사할 내용"]


async def test_x_copies_pane1_text_when_active(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    copied = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "copy_to_clipboard", lambda t: copied.append(t))
        await pilot.press("|")
        await pilot.pause()
        app._write_detail_pane(0, "패널0 내용")
        app._write_detail_pane(1, "패널1 내용")
        await pilot.press("o")            # 활성 = 1
        await pilot.press("x")
        assert copied == ["패널1 내용"]


async def test_split_toggle_and_switch(tmp_path):
    from textual.css.query import NoMatches

    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        assert len(app.panes) == 1
        await pilot.press("|")            # 분할
        await pilot.pause()
        assert len(app.panes) == 2
        assert app.query_one("#detail-1", RichLog)
        assert app.active == 0
        assert app.query_one("#detail", RichLog).has_class("active-pane")
        await pilot.press("o")            # 전환
        assert app.active == 1
        assert app.query_one("#detail-1", RichLog).has_class("active-pane")
        assert not app.query_one("#detail", RichLog).has_class("active-pane")
        await pilot.press("|")            # 해제
        await pilot.pause()
        assert len(app.panes) == 1 and app.active == 0
        with pytest.raises(NoMatches):
            app.query_one("#detail-1", RichLog)


async def test_split_selection_goes_to_active_pane(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        app.apply_snapshots(snap())
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")            # 활성 = 패널 1
        tree = app.query_one("#tree", Tree)
        leaf = tree.root.children[0].children[0]
        tree.select_node(leaf)
        await pilot.pause()
        assert app.panes[1].session is not None   # 활성 패널에 할당
        assert app.panes[0].session is None


async def test_split_independent_transcript_mode(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")
        await pilot.press("t")            # 패널 1만 transcript
        assert app.panes[1].transcript_mode is True
        assert app.panes[0].transcript_mode is False


async def test_unsplit_while_pane1_active_resets(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("|")
        await pilot.pause()
        await pilot.press("o")
        await pilot.press("|")            # 활성이 1인 채로 해제
        await pilot.pause()
        assert app.active == 0 and len(app.panes) == 1
        await pilot.press("o")            # 단일 상태에서 o는 no-op
        assert app.active == 0


async def test_apply_snapshots_notifies_once_per_error(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    notes = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        bad = {"srv1": ServerSnapshot(server="srv1", sessions=[], error="ssh refused")}
        app.apply_snapshots(bad)
        app.apply_snapshots(bad)  # 동일 에러 재갱신 — 추가 알림 없어야
        assert sum("srv1" in n and "doctor" in n for n in notes) == 1
        # 에러 사라졌다가 다시 발생하면 재알림
        app.apply_snapshots({"srv1": ServerSnapshot(server="srv1", sessions=[])})
        app.apply_snapshots(bad)
        assert sum("srv1" in n and "doctor" in n for n in notes) == 2
