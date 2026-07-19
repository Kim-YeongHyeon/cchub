from pathlib import Path

from cchub.transcript import extract_events

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def events():
    return list(extract_events(FIXTURE.read_text().splitlines()))


def test_extracts_messages_and_title_only():
    evs = events()
    assert [e.kind for e in evs] == ["message", "message", "title", "message"]


def test_user_string_content():
    e = events()[0]
    assert e.role == "user"
    assert e.text == "NUMA 실험 돌려줘"
    assert e.timestamp == "2026-07-01T09:00:00.000Z"
    assert e.cwd == "/home/u/proj"
    assert e.session_id == "s-1"


def test_assistant_joins_text_blocks_only():
    e = events()[1]
    assert e.role == "assistant"
    assert e.text == "실험을 시작합니다"  # thinking/tool_use 블록 제외


def test_title_event():
    e = events()[2]
    assert e.kind == "title" and e.text == "NUMA 실험"


def test_user_list_content():
    assert events()[3].text == "결과 요약해줘"


def test_malformed_and_unknown_lines_ignored():
    # fixture에 비JSON 줄, queue-operation, sidechain, meta가 있어도 예외 없이 4개만
    assert len(events()) == 4


def test_nonstring_text_block_does_not_raise():
    lines = ['{"type":"user","message":{"role":"user","content":[{"type":"text","text":123},{"type":"text","text":"진짜 텍스트"}]},"sessionId":"s-9","timestamp":"t"}']
    evs = list(extract_events(lines))
    assert len(evs) == 1
    assert evs[0].text == "진짜 텍스트"
