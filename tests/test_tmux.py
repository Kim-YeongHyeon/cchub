from cchub import tmux
from cchub.ssh import RunResult
from conftest import FakeRemote
from cchub.tmux import SpawnResult, list_session_names, spawn_session


PANES_OUT = (
    "%0\tmain:0.0\t/home/u/proj\tclaude\t100\n"
    "%3\tmain:1.0\t/home/u/other\tbash\t200\n"
    "잘못된 줄\n"
)


def test_list_panes_parses_and_skips_bad_lines():
    fake = FakeRemote({"tmux": RunResult(0, PANES_OUT, "")})
    panes = tmux.list_panes(fake)
    assert len(panes) == 2
    assert panes[0] == tmux.Pane("%0", "main:0.0", "/home/u/proj", "claude", "100")
    assert fake.calls[0] == ["tmux", "list-panes", "-a", "-F", tmux._FMT]
    assert tmux._FMT == (
        "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}"
        "\t#{pane_current_path}\t#{pane_current_command}\t#{pane_pid}"
    )


def test_list_panes_empty_when_tmux_absent():
    fake = FakeRemote({"tmux": RunResult(1, "", "no server running")})
    assert tmux.list_panes(fake) == []


def test_send_prompt_literal_then_enter():
    fake = FakeRemote()
    ok = tmux.send_prompt(fake, "%0", "hello; rm -rf 아님 $HOME")
    assert ok
    assert fake.calls[0] == ["tmux", "send-keys", "-t", "%0", "-l", "--",
                             "hello; rm -rf 아님 $HOME"]
    assert fake.calls[1] == ["tmux", "send-keys", "-t", "%0", "Enter"]


def test_send_prompt_fails_fast():
    fake = FakeRemote({"tmux": RunResult(1, "", "no pane")})
    assert not tmux.send_prompt(fake, "%9", "x")
    assert len(fake.calls) == 1  # Enter는 시도 안 함


def test_send_prompt_leading_dash_not_parsed_as_flag():
    fake = FakeRemote()
    tmux.send_prompt(fake, "%0", "-x 로 시작하는 프롬프트")
    cmd = fake.calls[0]
    assert cmd[cmd.index("--") + 1] == "-x 로 시작하는 프롬프트"


def test_capture():
    fake = FakeRemote({"tmux": RunResult(0, "화면 내용\n", "")})
    out = tmux.capture(fake, "%0", lines=50)
    assert out == "화면 내용\n"
    assert fake.calls[0] == ["tmux", "capture-pane", "-p", "-t", "%0", "-S", "-50"]


def test_verify_pane():
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, PANES_OUT, "")})
    assert tmux.verify_pane(fake, "%0")        # claude pane
    assert not tmux.verify_pane(fake, "%3")    # bash pane
    assert not tmux.verify_pane(fake, "%99")   # 없음


def test_confirm_delivery():
    fake = FakeRemote({("tmux", "capture-pane"): RunResult(0, "…화면에 실험 시작해줘 라고 보임\n", "")})
    assert tmux.confirm_delivery(fake, "%0", "실험 시작해줘")
    assert tmux.confirm_delivery(fake, "%0", "   ")     # 빈 텍스트는 True
    fake2 = FakeRemote({("tmux", "capture-pane"): RunResult(0, "다른 내용\n", "")})
    assert not tmux.confirm_delivery(fake2, "%0", "실험 시작해줘")


PANE_CLAUDE = "%9\tcchub-2:0.0\t/home/u\tclaude\t200\n"
PANE_BASH = "%9\tcchub-2:0.0\t/home/u\tbash\t200\n"


def test_list_session_names():
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "main\ncchub-1\n", "")})
    assert list_session_names(fake) == ["main", "cchub-1"]
    fake2 = FakeRemote({("tmux", "list-sessions"): RunResult(1, "", "no server running")})
    assert list_session_names(fake2) == []


def test_spawn_autoname_skips_taken():
    fake = FakeRemote({("tmux", "list-sessions"): RunResult(0, "cchub-1\n", "")})
    res = spawn_session(fake, "~", "claude --dangerously-skip-permissions")
    assert res.ok and res.name == "cchub-2" and res.prompt_sent is None
    # new-session은 sh -c 로 cwd를 $HOME 확장해 실행
    sh_calls = [c for c in fake.calls if c[0] == "sh"]
    assert sh_calls and 'tmux new-session -d -s cchub-2 -c "$HOME"' in sh_calls[0][2]
    # launch 명령은 -l -- 리터럴 주입 후 Enter
    sends = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    assert ["tmux", "send-keys", "-t", "cchub-2", "-l", "--",
            "claude --dangerously-skip-permissions"] in sends
    assert ["tmux", "send-keys", "-t", "cchub-2", "Enter"] in sends


def test_spawn_explicit_name_and_cwd():
    fake = FakeRemote()
    res = spawn_session(fake, "~/proj", "claude", name="exp1")
    assert res.ok and res.name == "exp1"
    sh_calls = [c for c in fake.calls if c[0] == "sh"]
    assert 'tmux new-session -d -s exp1 -c "$HOME"/proj' in sh_calls[0][2]
    # 이름이 지정되면 list-sessions 조회 불필요
    assert not any(c[:2] == ["tmux", "list-sessions"] for c in fake.calls)


def test_spawn_new_session_failure():
    fake = FakeRemote({"sh": RunResult(1, "", "create session failed: no such directory")})
    res = spawn_session(fake, "/no/such/dir", "claude")
    assert not res.ok and "no such directory" in res.error
    assert not any(c[:2] == ["tmux", "send-keys"] for c in fake.calls)


def test_spawn_prompt_sent_after_claude_boots():
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, PANE_CLAUDE, "")})
    slept = []
    res = spawn_session(fake, "~", "claude", name="cchub-2",
                        prompt="버그 고쳐줘", sleep=slept.append)
    assert res.ok and res.prompt_sent is True
    assert slept == []                       # 첫 폴링에 이미 기동 → 대기 없음
    sends = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    # 프롬프트는 발견한 pane_id(%9)로 전송
    assert ["tmux", "send-keys", "-t", "%9", "-l", "--", "버그 고쳐줘"] in sends


def test_spawn_prompt_poll_timeout():
    fake = FakeRemote({("tmux", "list-panes"): RunResult(0, PANE_BASH, "")})
    slept = []
    res = spawn_session(fake, "~", "claude", name="cchub-2",
                        prompt="버그 고쳐줘", poll_attempts=3, sleep=slept.append)
    assert res.ok and res.prompt_sent is False
    assert len(slept) == 3                   # 매 실패마다 sleep
    assert not any(c == ["tmux", "send-keys", "-t", "%9", "-l", "--", "버그 고쳐줘"]
                   for c in fake.calls)
