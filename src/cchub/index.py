from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cchub.transcript import extract_events

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
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
CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
    server UNINDEXED, session_id UNINDEXED, role UNINDEXED, ts UNINDEXED, text
);
"""


@dataclass
class SessionRow:
    server: str
    session_id: str
    project: str
    title: str
    first_prompt: str
    first_ts: str
    last_ts: str
    last_role: str


_ROW_COLS = "server, session_id, project, title, first_prompt, first_ts, last_ts, last_role"


class SessionIndex:
    def __init__(self, db_path: Path | str):
        self.db = sqlite3.connect(db_path)
        self._migrate_if_needed()
        self.db.executescript(_SCHEMA)

    def _migrate_if_needed(self) -> None:
        """구버전 fts5 스키마(메타데이터 컬럼까지 인덱싱)를 감지하면 비우고
        재구축을 유도한다. 다음 sync에서 처음부터 다시 인덱싱된다."""
        row = self.db.execute(
            "SELECT sql FROM sqlite_master WHERE name='messages'"
        ).fetchone()
        if row and "UNINDEXED" not in row[0]:
            self.db.execute("DROP TABLE messages")
            self.db.execute("DROP TABLE IF EXISTS sessions")  # bytes_indexed 리셋 → 재인덱싱 유도
            self.db.commit()

    def index_file(self, server: str, project: str, path: Path) -> int:
        """path에서 아직 읽지 않은 바이트만 파싱해 반영한다. 반영한 message 수 반환."""
        session_id = path.stem
        row = self.db.execute(
            "SELECT bytes_indexed FROM sessions WHERE server=? AND session_id=?",
            (server, session_id),
        ).fetchone()
        offset = row[0] if row else 0
        size = path.stat().st_size
        if size < offset:  # 파일이 줄어듦(교체) → 처음부터 다시
            self._forget(server, session_id)
            offset = 0
        if size == offset:
            return 0
        with open(path, "rb") as fp:
            fp.seek(offset)
            data = fp.read()
        cut = data.rfind(b"\n") + 1  # 쓰다 만 마지막 줄은 다음 기회에
        if cut == 0:
            return 0
        lines = data[:cut].decode("utf-8", errors="replace").splitlines()

        self.db.execute(
            "INSERT OR IGNORE INTO sessions(server, session_id, project, path)"
            " VALUES(?,?,?,?)",
            (server, session_id, project, str(path)),
        )
        n = 0
        for ev in extract_events(lines):
            if ev.kind == "title":
                self.db.execute(
                    "UPDATE sessions SET title=? WHERE server=? AND session_id=?",
                    (ev.text, server, session_id),
                )
                continue
            self.db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (server, session_id, ev.role, ev.timestamp, ev.text),
            )
            if ev.role == "user":
                self.db.execute(
                    "UPDATE sessions SET"
                    " first_prompt=CASE WHEN first_prompt='' THEN ? ELSE first_prompt END,"
                    " first_ts=CASE WHEN first_ts='' THEN ? ELSE first_ts END"
                    " WHERE server=? AND session_id=?",
                    (ev.text[:200], ev.timestamp, server, session_id),
                )
            self.db.execute(
                "UPDATE sessions SET last_ts=?, last_role=? WHERE server=? AND session_id=?",
                (ev.timestamp, ev.role, server, session_id),
            )
            n += 1
        self.db.execute(
            "UPDATE sessions SET bytes_indexed=? WHERE server=? AND session_id=?",
            (offset + cut, server, session_id),
        )
        self.db.commit()
        return n

    def _forget(self, server: str, session_id: str) -> None:
        self.db.execute(
            "DELETE FROM sessions WHERE server=? AND session_id=?", (server, session_id)
        )
        self.db.execute(
            "DELETE FROM messages WHERE server=? AND session_id=?", (server, session_id)
        )
        self.db.commit()

    def get_session(self, server: str, session_id: str) -> SessionRow | None:
        row = self.db.execute(
            f"SELECT {_ROW_COLS} FROM sessions WHERE server=? AND session_id=?",
            (server, session_id),
        ).fetchone()
        return SessionRow(*row) if row else None

    def list_sessions(self, server: str | None = None) -> list[SessionRow]:
        if server is None:
            rows = self.db.execute(
                f"SELECT {_ROW_COLS} FROM sessions ORDER BY last_ts DESC"
            ).fetchall()
        else:
            rows = self.db.execute(
                f"SELECT {_ROW_COLS} FROM sessions WHERE server=? ORDER BY last_ts DESC",
                (server,),
            ).fetchall()
        return [SessionRow(*r) for r in rows]

    def search(self, query: str, limit: int = 20) -> list[tuple[str, str, str, str, str]]:
        try:
            return self.db.execute(
                "SELECT server, session_id, role, ts,"
                " snippet(messages, 4, '[', ']', '…', 12)"
                " FROM messages WHERE messages MATCH ? ORDER BY ts DESC LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Retry with query escaped as a quoted phrase for FTS5
            escaped_query = '"' + query.replace('"', '""') + '"'
            try:
                return self.db.execute(
                    "SELECT server, session_id, role, ts,"
                    " snippet(messages, 4, '[', ']', '…', 12)"
                    " FROM messages WHERE messages MATCH ? ORDER BY ts DESC LIMIT ?",
                    (escaped_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # If escaped query also fails, return empty results
                return []

    def tail(self, server: str, session_id: str, limit: int = 10) -> list[tuple[str, str, str]]:
        rows = self.db.execute(
            "SELECT role, ts, text FROM messages WHERE server=? AND session_id=?"
            " ORDER BY ts DESC LIMIT ?",
            (server, session_id, limit),
        ).fetchall()
        return list(reversed(rows))

    def forget_all(self) -> None:
        self.db.execute("DELETE FROM sessions")
        self.db.execute("DELETE FROM messages")
        self.db.commit()
