from cchub import tmux
from cchub.ssh import RunResult
from conftest import FakeRemote


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
