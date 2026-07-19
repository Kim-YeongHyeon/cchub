import shutil
import sqlite3
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


def test_search_matches_text_only_not_metadata(tmp_path):
    idx, p = make(tmp_path)
    idx.index_file("srv1", "-home-u-proj", p)
    assert idx.search("srv1") == []      # 서버명은 매칭 안 됨
    assert idx.search("user") == []      # role은 매칭 안 됨
    assert idx.search("s-1") == []       # 세션ID는 매칭 안 됨
    assert idx.search("NUMA")            # 본문은 매칭됨


def test_old_schema_index_is_migrated_on_open(tmp_path):
    db_path = tmp_path / "index.db"
    # 구버전 DDL(메타데이터 컬럼까지 인덱싱)로 수동 생성 + 더미 행 삽입
    old_conn = sqlite3.connect(db_path)
    old_conn.executescript(
        """
        CREATE TABLE sessions(
            server TEXT NOT NULL,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            first_prompt TEXT NOT NULL DEFAULT '',
            first_ts TEXT NOT NULL DEFAULT '',
            last_ts TEXT NOT NULL DEFAULT '',
            last_role TEXT NOT NULL DEFAULT '',
            bytes_indexed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (server, session_id)
        );
        CREATE VIRTUAL TABLE messages USING fts5(
            server, session_id, role, ts, text
        );
        """
    )
    old_conn.execute(
        "INSERT INTO sessions(server, session_id, project, path, bytes_indexed)"
        " VALUES('srv1','s-1','-home-u-proj','/x',999)"
    )
    old_conn.execute(
        "INSERT INTO messages VALUES('srv1','s-1','user','2026-07-01T00:00:00.000Z','old text')"
    )
    old_conn.commit()
    old_conn.close()

    idx = SessionIndex(db_path)  # 마이그레이션 트리거
    assert idx.get_session("srv1", "s-1") is None  # 구 데이터는 비워짐
    assert idx.list_sessions() == []

    p = tmp_path / "s-1.jsonl"
    shutil.copy(FIXTURE, p)
    assert idx.index_file("srv1", "-home-u-proj", p) == 3  # 재인덱싱 가능
    assert idx.search("srv1") == []  # 새 스키마도 메타데이터 매칭 안 됨
