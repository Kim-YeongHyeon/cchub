import shutil
from pathlib import Path

from cchub.index import SessionIndex

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def make(tmp_path) -> tuple[SessionIndex, Path]:
    # 세션ID는 파일명(stem)에서 온다 — fixture를 세션ID 이름으로 복사
    p = tmp_path / "s-1.jsonl"
    shutil.copy(FIXTURE, p)
    return SessionIndex(tmp_path / "index.db"), p


def test_index_file_populates_session_row(tmp_path):
    idx, p = make(tmp_path)
    n = idx.index_file("srv1", "-home-u-proj", p)
    assert n == 3  # message 3건 (title은 카운트 제외)
    row = idx.get_session("srv1", "s-1")
    assert row.title == "NUMA 실험"
    assert row.first_prompt == "NUMA 실험 돌려줘"
    assert row.last_role == "user"
    assert row.last_ts == "2026-07-01T10:00:00.000Z"
    assert row.project == "-home-u-proj"


def test_index_file_is_incremental(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    assert idx.index_file("srv1", "-home-u-proj", p) == 0  # 변화 없음 → 0
    with open(p, "a") as fp:
        fp.write('{"type":"assistant","message":{"role":"assistant","content":'
                 '[{"type":"text","text":"추가 응답"}]},'
                 '"timestamp":"2026-07-01T11:00:00.000Z","sessionId":"s-1"}\n')
    assert idx.index_file("srv1", "-home-u-proj", p) == 1
    assert idx.get_session("srv1", "s-1").last_role == "assistant"


def test_partial_last_line_not_consumed(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    with open(p, "a") as fp:
        fp.write('{"type":"user","message"')  # 개행 없는 쓰다 만 줄
    assert idx.index_file("srv1", "-home-u-proj", p) == 0
    with open(p, "a") as fp:
        fp.write(':{"role":"user","content":"이어서"},"sessionId":"s-1",'
                 '"timestamp":"2026-07-01T12:00:00.000Z"}\n')
    assert idx.index_file("srv1", "-home-u-proj", p) == 1


def test_shrunk_file_reindexes_from_scratch(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    p.write_text('{"type":"user","message":{"role":"user","content":"새 내용"},'
                 '"sessionId":"s-1","timestamp":"2026-07-02T00:00:00.000Z"}\n')
    assert idx.index_file("srv1", "-home-u-proj", p) == 1
    row = idx.get_session("srv1", "s-1")
    assert row.first_prompt == "새 내용"


def test_search_and_tail_and_list(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    hits = idx.search("NUMA")
    assert hits and hits[0][0] == "srv1" and hits[0][1] == "s-1"
    msgs = idx.tail("srv1", "s-1", limit=2)
    assert [m[0] for m in msgs] == ["assistant", "user"]  # 시간순 마지막 2개
    assert msgs[-1][2] == "결과 요약해줘"
    assert idx.list_sessions()[0].session_id == "s-1"
    assert idx.list_sessions("없는서버") == []


def test_forget_all(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    idx.forget_all()
    assert idx.list_sessions() == []
    assert idx.index_file("srv1", "-home-u-proj", p) == 3  # 재인덱싱 가능


def test_shrink_to_zero_is_durable_across_connections(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    p.write_text("")  # 0바이트로 truncate
    assert idx.index_file("srv1", "-home-u-proj", p) == 0
    other = SessionIndex(tmp_path / "index.db")  # 별도 연결
    assert other.get_session("srv1", "s-1") is None


def test_search_survives_fts_syntax_hazards(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    for q in ["O'Brien", "NUMA AND", 'unbalanced "quote', "-"]:
        idx.search(q)  # 예외만 안 나면 됨
    assert idx.search('NUMA"') != []
