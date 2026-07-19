# cchub M1 (코어 + CLI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 여러 서버의 tmux 안 Claude Code 세션을 로컬에서 조회·프롬프트 전송·이력 검색할 수 있는 코어 라이브러리와 `cchub` CLI를 구축한다.

**Architecture:** Agentless SSH-pull. 서버의 `~/.claude/projects/*.jsonl`을 rsync로 로컬(`~/.cchub/cache/<server>/`)에 증분 미러링하고 SQLite(FTS5)로 인덱싱한다. tmux `send-keys`/`capture-pane`으로 원격 세션을 제어한다. SSH/tmux 계층은 `Remote` 인터페이스 뒤에 두어 fake로 테스트한다.

**Tech Stack:** Python 3.13 (런타임 의존성 stdlib만: tomllib, sqlite3, subprocess), pytest(dev), OpenSSH ControlMaster, rsync, tmux.

## Global Constraints

- 런타임 외부 패키지 의존성 금지 (M1은 stdlib만; dev 의존성은 pytest만)
- Claude API(LLM 호출) 절대 사용 금지
- rsync 미러링에 `--delete` 금지 (서버 쪽 30일 정리와 무관하게 로컬 이력 영구 보존)
- JSONL 파서는 관대하게: 모르는 줄/파싱 실패 줄은 무시, 예외 전파 금지
- 데이터 디렉토리는 `~/.cchub` (환경변수 `CCHUB_DIR`로 재정의 가능 — 테스트용)
- 프로젝트 루트: `~/cchub`, 파이썬 실행은 `.venv/bin/python`, 테스트는 `.venv/bin/pytest`
- 커밋 메시지 끝에 Co-Authored-By/Claude-Session 트레일러 불필요 (개인 도구, 간결하게)

## File Structure

```
cchub/
  pyproject.toml
  src/cchub/
    __init__.py
    config.py      # 설정 로드 (config.toml)
    transcript.py  # JSONL → Event 관대한 파서
    index.py       # SessionIndex: SQLite + FTS5, 증분 인덱싱
    ssh.py         # Remote 인터페이스, SSHRemote(ControlMaster), mirror(rsync)
    tmux.py        # pane 목록 / send_prompt / capture
    sessions.py    # pane↔세션 매칭, 번호 부여, 상태 추정
    sync.py        # 서버 미러링 + 인덱싱 오케스트레이션
    cli.py         # cchub init/sync/list/send/tail/search/reindex
  tests/
    fixtures/sample_transcript.jsonl
    conftest.py
    test_config.py
    test_transcript.py
    test_index.py
    test_tmux.py
    test_sessions.py
    test_sync.py
    test_cli.py
```

---

### Task 1: 프로젝트 스캐폴드 + 설정 로더

**Files:**
- Create: `pyproject.toml`, `src/cchub/__init__.py`, `src/cchub/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.cchub_dir() -> Path`, `config.load_config(path: Path | None = None) -> Config`, `Config(sync_interval: int, stats_interval: int, servers: dict[str, ServerConfig])`, `ServerConfig(name: str, host: str, results: list[str], claude_dir: str)`, `ConfigError(Exception)`

- [ ] **Step 1: 스캐폴드 생성**

```bash
cd ~/cchub
mkdir -p src/cchub tests/fixtures
touch src/cchub/__init__.py
```

`pyproject.toml`:

```toml
[project]
name = "cchub"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
cchub = "cchub.cli:main"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: venv 생성 및 설치**

```bash
cd ~/cchub && python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
.venv/bin/pytest --version
```

Expected: `pytest 8.x.x` 출력

- [ ] **Step 3: 실패하는 테스트 작성** — `tests/test_config.py`

```python
from pathlib import Path

import pytest

from cchub.config import Config, ConfigError, cchub_dir, load_config


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_load_full_config(tmp_path):
    p = write(tmp_path, """
[general]
sync_interval = 10
stats_interval = 1

[servers.srv1]
host = "user@10.0.0.11"
results = ["~/exp/**"]

[servers.srv2]
host = "srv2-alias"
""")
    cfg = load_config(p)
    assert cfg.sync_interval == 10
    assert cfg.stats_interval == 1
    assert cfg.servers["srv1"].host == "user@10.0.0.11"
    assert cfg.servers["srv1"].results == ["~/exp/**"]
    assert cfg.servers["srv2"].results == []
    assert cfg.servers["srv2"].claude_dir == "~/.claude"


def test_defaults_when_general_missing(tmp_path):
    p = write(tmp_path, '[servers.a]\nhost = "h"\n')
    cfg = load_config(p)
    assert cfg.sync_interval == 30
    assert cfg.stats_interval == 2


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_server_without_host_raises(tmp_path):
    p = write(tmp_path, "[servers.a]\nresults = []\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_cchub_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path / "x"))
    assert cchub_dir() == tmp_path / "x"
```

- [ ] **Step 4: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.config'`

- [ ] **Step 5: 구현** — `src/cchub/config.py`

```python
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


def cchub_dir() -> Path:
    return Path(os.environ.get("CCHUB_DIR", "~/.cchub")).expanduser()


@dataclass
class ServerConfig:
    name: str
    host: str
    results: list[str] = field(default_factory=list)
    claude_dir: str = "~/.claude"


@dataclass
class Config:
    sync_interval: int = 30
    stats_interval: int = 2
    servers: dict[str, ServerConfig] = field(default_factory=dict)


def load_config(path: Path | None = None) -> Config:
    path = path or cchub_dir() / "config.toml"
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path} (cchub init으로 생성)")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: TOML 파싱 실패: {e}") from e
    general = data.get("general", {})
    servers: dict[str, ServerConfig] = {}
    for name, s in data.get("servers", {}).items():
        if "host" not in s:
            raise ConfigError(f"servers.{name}: host 항목이 없습니다")
        servers[name] = ServerConfig(
            name=name,
            host=s["host"],
            results=list(s.get("results", [])),
            claude_dir=s.get("claude_dir", "~/.claude"),
        )
    return Config(
        sync_interval=int(general.get("sync_interval", 30)),
        stats_interval=int(general.get("stats_interval", 2)),
        servers=servers,
    )
```

- [ ] **Step 6: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 7: 커밋**

```bash
cd ~/cchub
printf '.venv/\n__pycache__/\n*.egg-info/\n' > .gitignore
git add pyproject.toml .gitignore src tests
git commit -m "feat: 프로젝트 스캐폴드 및 config 로더"
```

---

### Task 2: transcript JSONL 관대한 파서

**Files:**
- Create: `src/cchub/transcript.py`, `tests/fixtures/sample_transcript.jsonl`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Produces: `transcript.Event(kind: str, session_id: str, role: str, text: str, timestamp: str, cwd: str)` — kind는 `"message"` 또는 `"title"`; `transcript.extract_events(lines: Iterable[str]) -> Iterator[Event]`

**배경 (실측한 실제 포맷):** 실제 transcript 라인 타입은 `user`/`assistant`(본문, `message.content`가 str 또는 블록 리스트), `custom-title`(세션 제목), 그 외 `mode`/`queue-operation`/`file-history-snapshot` 등 다수. `isSidechain: true`(서브에이전트)와 `isMeta: true`(시스템 삽입) 라인은 사용자 이력이 아니므로 스킵한다.

- [ ] **Step 1: fixture 작성** — `tests/fixtures/sample_transcript.jsonl` (실제 포맷 축약본, 한 줄이 JSON 하나 — 아래 각 줄을 그대로 한 줄씩)

```jsonl
{"type":"mode","mode":"normal","sessionId":"s-1"}
{"type":"user","isSidechain":false,"message":{"role":"user","content":"NUMA 실험 돌려줘"},"timestamp":"2026-07-01T09:00:00.000Z","cwd":"/home/u/proj","sessionId":"s-1"}
{"type":"assistant","isSidechain":false,"message":{"role":"assistant","content":[{"type":"thinking","thinking":"고민"},{"type":"text","text":"실험을 시작합니다"},{"type":"tool_use","name":"Bash","input":{}}]},"timestamp":"2026-07-01T09:00:05.000Z","cwd":"/home/u/proj","sessionId":"s-1"}
{"type":"custom-title","customTitle":"NUMA 실험","sessionId":"s-1"}
{"type":"user","isSidechain":true,"message":{"role":"user","content":"사이드체인은 스킵"},"timestamp":"2026-07-01T09:00:06.000Z","sessionId":"s-1"}
{"type":"user","isMeta":true,"message":{"role":"user","content":"메타도 스킵"},"timestamp":"2026-07-01T09:00:07.000Z","sessionId":"s-1"}
{"type":"queue-operation","operation":"enqueue","sessionId":"s-1"}
이 줄은 JSON이 아님 — 파서가 조용히 무시해야 함
{"type":"user","isSidechain":false,"message":{"role":"user","content":[{"type":"text","text":"결과 요약해줘"}]},"timestamp":"2026-07-01T10:00:00.000Z","cwd":"/home/u/proj","sessionId":"s-1"}
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_transcript.py`

```python
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
```

- [ ] **Step 3: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_transcript.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.transcript'`

- [ ] **Step 4: 구현** — `src/cchub/transcript.py`

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass
class Event:
    kind: str            # "message" | "title"
    session_id: str
    role: str = ""       # kind=="message"일 때 "user" | "assistant"
    text: str = ""
    timestamp: str = ""
    cwd: str = ""


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def extract_events(lines: Iterable[str]) -> Iterator[Event]:
    """관대한 파서: 아는 형태의 줄만 Event로 변환하고 나머지는 조용히 무시한다.

    transcript 포맷은 Claude Code 내부 포맷이라 버전에 따라 바뀔 수 있다 —
    어떤 입력에도 예외를 전파하지 않는다.
    """
    for line in lines:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        sid = str(obj.get("sessionId", ""))
        kind = obj.get("type")
        if kind == "custom-title" and obj.get("customTitle"):
            yield Event(kind="title", session_id=sid, text=str(obj["customTitle"]))
        elif kind in ("user", "assistant") and not obj.get("isSidechain") and not obj.get("isMeta"):
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            text = _text_of(msg.get("content"))
            if text.strip():
                yield Event(
                    kind="message",
                    session_id=sid,
                    role=kind,
                    text=text,
                    timestamp=str(obj.get("timestamp", "")),
                    cwd=str(obj.get("cwd", "")),
                )
```

- [ ] **Step 5: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_transcript.py -v`
Expected: 6 passed

- [ ] **Step 6: 커밋**

```bash
cd ~/cchub && git add src/cchub/transcript.py tests/fixtures tests/test_transcript.py
git commit -m "feat: transcript JSONL 관대한 파서"
```

---

### Task 3: SQLite 세션 인덱스 (FTS5, 증분)

**Files:**
- Create: `src/cchub/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `transcript.extract_events`
- Produces: `index.SessionRow(server, session_id, project, title, first_prompt, first_ts, last_ts, last_role)`; `index.SessionIndex(db_path)` with:
  - `index_file(server: str, project: str, path: Path) -> int` (새로 반영한 이벤트 수)
  - `get_session(server: str, session_id: str) -> SessionRow | None`
  - `list_sessions(server: str | None = None) -> list[SessionRow]` (last_ts 내림차순)
  - `search(query: str, limit: int = 20) -> list[tuple[str, str, str, str, str]]` (server, session_id, role, ts, snippet)
  - `tail(server: str, session_id: str, limit: int = 10) -> list[tuple[str, str, str]]` (role, ts, text — 시간순)
  - `forget_all() -> None` (reindex용 전체 삭제)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_index.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.index'`

- [ ] **Step 3: 구현** — `src/cchub/index.py`

```python
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
    server, session_id, role, ts, text
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
        self.db.executescript(_SCHEMA)

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
        return self.db.execute(
            "SELECT server, session_id, role, ts,"
            " snippet(messages, 4, '[', ']', '…', 12)"
            " FROM messages WHERE messages MATCH ? ORDER BY ts DESC LIMIT ?",
            (query, limit),
        ).fetchall()

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
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_index.py -v`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/index.py tests/test_index.py
git commit -m "feat: SQLite FTS5 세션 인덱스 (증분 인덱싱)"
```

---

### Task 4: Remote 인터페이스 + SSH/rsync 구현

**Files:**
- Create: `src/cchub/ssh.py`, `tests/conftest.py`
- Test: `tests/test_ssh.py` (Create)

**Interfaces:**
- Consumes: `config.cchub_dir`
- Produces:
  - `ssh.RunResult(rc: int, out: str, err: str)`
  - `ssh.Remote` (추상): `run(argv: list[str], timeout: int = 15) -> RunResult`, `mirror(remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult`
  - `ssh.SSHRemote(host: str)` — Remote 구현 (ControlMaster 다중화, BatchMode)
  - `tests/conftest.py`의 `FakeRemote` — 이후 태스크 테스트가 공용으로 사용:
    `FakeRemote(responses: dict[str, RunResult])` — `run()`은 argv를 `self.calls`에 기록하고, argv[0](명령 첫 단어) 키로 responses에서 응답을 찾음(없으면 rc=0, out=""). `mirror()`는 `self.mirrors`에 (remote_dir, local_dir) 기록 후 rc=0 반환

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/conftest.py`:

```python
from pathlib import Path

from cchub.ssh import Remote, RunResult


class FakeRemote(Remote):
    """argv를 기록하고 미리 정한 응답을 돌려주는 테스트용 Remote."""

    def __init__(self, responses: dict[str, RunResult] | None = None):
        self.responses = responses or {}
        self.calls: list[list[str]] = []
        self.mirrors: list[tuple[str, Path]] = []

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        self.calls.append(list(argv))
        return self.responses.get(argv[0], RunResult(0, "", ""))

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        self.mirrors.append((remote_dir, Path(local_dir)))
        return RunResult(0, "", "")
```

`tests/test_ssh.py`:

```python
import subprocess
from pathlib import Path

from cchub.ssh import RunResult, SSHRemote


def test_run_builds_quoted_ssh_command(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = SSHRemote("user@host").run(["echo", "hello world", "$HOME"])
    assert r == RunResult(0, "ok", "")
    cmd = captured["cmd"]
    assert cmd[0] == "ssh" and "user@host" in cmd
    # 원격에서 실행될 문자열: 공백/특수문자가 shlex 인용됨
    remote_cmd = cmd[-1]
    assert remote_cmd == "echo 'hello world' '$HOME'"
    assert "BatchMode=yes" in " ".join(cmd)
    assert "ControlMaster=auto" in " ".join(cmd)


def test_run_nonzero_and_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 15))

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = SSHRemote("h").run(["sleep", "99"], timeout=1)
    assert r.rc != 0 and "timeout" in r.err


def test_mirror_builds_rsync_without_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dst = tmp_path / "cache" / "projects"
    r = SSHRemote("user@host").mirror("~/.claude/projects", dst)
    assert r.rc == 0
    cmd = captured["cmd"]
    assert cmd[0] == "rsync" and "-az" in cmd
    assert "--delete" not in cmd  # 이력 영구 보존
    assert cmd[-2] == "user@host:~/.claude/projects/"
    assert cmd[-1] == str(dst) + "/"
    assert dst.exists()  # 로컬 디렉토리 자동 생성
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_ssh.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.ssh'`

- [ ] **Step 3: 구현** — `src/cchub/ssh.py`

```python
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cchub.config import cchub_dir


@dataclass
class RunResult:
    rc: int
    out: str
    err: str


class Remote:
    """원격 명령 실행 인터페이스. 테스트에서는 FakeRemote로 대체한다."""

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        raise NotImplementedError

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        raise NotImplementedError


class SSHRemote(Remote):
    def __init__(self, host: str):
        self.host = host
        cm = cchub_dir() / "cm"
        cm.mkdir(parents=True, exist_ok=True)
        self._opts = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=600",
            "-o", f"ControlPath={cm}/%r@%h-%p",
        ]

    def run(self, argv: list[str], timeout: int = 15) -> RunResult:
        remote_cmd = " ".join(shlex.quote(a) for a in argv)
        try:
            p = subprocess.run(
                ["ssh", *self._opts, self.host, remote_cmd],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)

    def mirror(self, remote_dir: str, local_dir: Path, timeout: int = 120) -> RunResult:
        # --delete 금지: 서버 쪽 30일 정리가 로컬 이력을 지우지 않도록
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(
                [
                    "rsync", "-az",
                    "-e", "ssh " + " ".join(self._opts),
                    f"{self.host}:{remote_dir}/",
                    str(local_dir) + "/",
                ],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(124, "", f"timeout after {timeout}s")
        return RunResult(p.returncode, p.stdout, p.stderr)
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_ssh.py -v`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/ssh.py tests/conftest.py tests/test_ssh.py
git commit -m "feat: Remote 인터페이스 + SSH ControlMaster/rsync 구현"
```

---

### Task 5: tmux 브리지 (pane 목록 / 프롬프트 전송 / 캡처)

**Files:**
- Create: `src/cchub/tmux.py`
- Test: `tests/test_tmux.py`

**Interfaces:**
- Consumes: `ssh.Remote`, `tests/conftest.py`의 `FakeRemote`
- Produces: `tmux.Pane(pane_id: str, location: str, cwd: str, command: str)`; `tmux.list_panes(remote: Remote) -> list[Pane]`; `tmux.send_prompt(remote: Remote, pane_id: str, text: str) -> bool`; `tmux.capture(remote: Remote, pane_id: str, lines: int = 100) -> str`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_tmux.py`

```python
from cchub import tmux
from cchub.ssh import RunResult
from tests.conftest import FakeRemote


PANES_OUT = (
    "%0\tmain:0.0\t/home/u/proj\tclaude\n"
    "%3\tmain:1.0\t/home/u/other\tbash\n"
    "잘못된 줄\n"
)


def test_list_panes_parses_and_skips_bad_lines():
    fake = FakeRemote({"tmux": RunResult(0, PANES_OUT, "")})
    panes = tmux.list_panes(fake)
    assert len(panes) == 2
    assert panes[0] == tmux.Pane("%0", "main:0.0", "/home/u/proj", "claude")
    assert fake.calls[0][:3] == ["tmux", "list-panes", "-a"]


def test_list_panes_empty_when_tmux_absent():
    fake = FakeRemote({"tmux": RunResult(1, "", "no server running")})
    assert tmux.list_panes(fake) == []


def test_send_prompt_literal_then_enter():
    fake = FakeRemote()
    ok = tmux.send_prompt(fake, "%0", "hello; rm -rf 아님 $HOME")
    assert ok
    assert fake.calls[0] == ["tmux", "send-keys", "-t", "%0", "-l",
                             "hello; rm -rf 아님 $HOME"]
    assert fake.calls[1] == ["tmux", "send-keys", "-t", "%0", "Enter"]


def test_send_prompt_fails_fast():
    fake = FakeRemote({"tmux": RunResult(1, "", "no pane")})
    assert not tmux.send_prompt(fake, "%9", "x")
    assert len(fake.calls) == 1  # Enter는 시도 안 함


def test_capture():
    fake = FakeRemote({"tmux": RunResult(0, "화면 내용\n", "")})
    out = tmux.capture(fake, "%0", lines=50)
    assert out == "화면 내용\n"
    assert fake.calls[0] == ["tmux", "capture-pane", "-p", "-t", "%0", "-S", "-50"]
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tmux.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.tmux'`

- [ ] **Step 3: 구현** — `src/cchub/tmux.py`

```python
from __future__ import annotations

from dataclasses import dataclass

from cchub.ssh import Remote

_FMT = "#{pane_id}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_path}\t#{pane_current_command}"


@dataclass
class Pane:
    pane_id: str    # 예: %0 (tmux 고유 ID, 순서 바뀌어도 불변)
    location: str   # 예: main:1.0 (사람이 읽는 위치)
    cwd: str
    command: str


def list_panes(remote: Remote) -> list[Pane]:
    r = remote.run(["tmux", "list-panes", "-a", "-F", _FMT])
    if r.rc != 0:
        return []
    panes = []
    for line in r.out.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            panes.append(Pane(*parts))
    return panes


def send_prompt(remote: Remote, pane_id: str, text: str) -> bool:
    # -l(literal): 텍스트를 키 이름으로 해석하지 않음. Enter는 별도 전송.
    if remote.run(["tmux", "send-keys", "-t", pane_id, "-l", text]).rc != 0:
        return False
    return remote.run(["tmux", "send-keys", "-t", pane_id, "Enter"]).rc == 0


def capture(remote: Remote, pane_id: str, lines: int = 100) -> str:
    r = remote.run(["tmux", "capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"])
    return r.out if r.rc == 0 else ""
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_tmux.py -v`
Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/tmux.py tests/test_tmux.py
git commit -m "feat: tmux 브리지 (pane 목록/프롬프트 전송/캡처)"
```

---

### Task 6: 세션 디스커버리 (pane↔세션 매칭, 번호, 상태)

**Files:**
- Create: `src/cchub/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `tmux.list_panes`/`Pane`, `index.SessionIndex.get_session`, `ssh.Remote`
- Produces: `sessions.encode_project(cwd: str) -> str`; `sessions.LiveSession(server, number, pane_id, location, cwd, project, session_id, title, state)` — state는 `"working" | "waiting" | "idle" | "unknown"`; `sessions.discover(remote: Remote, server: str, cache_dir: Path, index: SessionIndex | None = None) -> list[LiveSession]`

**설계 노트:** pane→세션 매칭은 "pane의 cwd를 프로젝트 디렉토리명으로 인코딩(`/`→`-`)한 뒤, 미러된 캐시에서 그 프로젝트의 최신 mtime jsonl"이 그 pane의 활성 세션이라는 휴리스틱. rsync `-a`가 mtime을 보존하므로 미러 파일의 mtime = 서버 원본의 마지막 갱신 시각이다(동기화 주기만큼 지연될 수 있음 — 허용).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_sessions.py`

```python
import os
import time
from pathlib import Path

from cchub import sessions
from cchub.index import SessionIndex
from cchub.ssh import RunResult
from tests.conftest import FakeRemote

PANES = (
    "%5\tmain:0.0\t/home/u/proj\tclaude\n"   # claude pane
    "%1\tmain:1.0\t/home/u/other\tnode\n"    # claude는 node로 보일 수 있음
    "%2\tmain:2.0\t/home/u/x\tbash\n"        # claude 아님 → 제외
)


def setup_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cache" / "srv1"
    d = cache / "projects" / "-home-u-proj"
    d.mkdir(parents=True)
    old = d / "old-session.jsonl"
    old.write_text("{}\n")
    os.utime(old, (time.time() - 9999, time.time() - 9999))
    new = d / "new-session.jsonl"
    new.write_text("{}\n")  # 방금 생성 → mtime 최신 → working
    return cache


def test_encode_project():
    assert sessions.encode_project("/home/u/my-proj") == "-home-u-my-proj"


def test_discover_filters_numbers_and_matches(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    cache = setup_cache(tmp_path)
    live = sessions.discover(fake, "srv1", cache)
    assert len(live) == 2  # bash pane 제외
    assert [s.number for s in live] == [1, 2]
    first = live[0]
    assert first.pane_id == "%5"
    assert first.session_id == "new-session"  # 최신 mtime 파일이 매칭
    assert first.state == "working"           # mtime이 최근 30초 이내


def test_discover_without_cache_is_unknown(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    live = sessions.discover(fake, "srv1", tmp_path / "empty")
    assert live[0].session_id == ""
    assert live[0].state == "unknown"


def test_discover_waiting_state_from_index(tmp_path):
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    cache = setup_cache(tmp_path)
    new = cache / "projects" / "-home-u-proj" / "new-session.jsonl"
    os.utime(new, (time.time() - 300, time.time() - 300))  # 5분 전 → not working
    idx = SessionIndex(tmp_path / "i.db")
    new.write_text(
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"text","text":"끝났습니다"}]},'
        '"timestamp":"2026-07-19T00:00:00.000Z","sessionId":"new-session"}\n'
    )
    os.utime(new, (time.time() - 300, time.time() - 300))
    idx.index_file("srv1", "-home-u-proj", new)
    live = sessions.discover(fake, "srv1", cache, idx)
    assert live[0].state == "waiting"  # 마지막 메시지가 assistant → 입력 대기
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.sessions'`

- [ ] **Step 3: 구현** — `src/cchub/sessions.py`

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cchub import tmux
from cchub.index import SessionIndex
from cchub.ssh import Remote

# claude는 실행 방식에 따라 pane_current_command가 claude 또는 node로 보인다
_CLAUDE_COMMANDS = {"claude", "node"}
_WORKING_WINDOW_SECS = 30


def encode_project(cwd: str) -> str:
    """Claude Code가 cwd를 ~/.claude/projects/ 디렉토리명으로 바꾸는 규칙."""
    return cwd.replace("/", "-")


@dataclass
class LiveSession:
    server: str
    number: int      # 사용자에게 보여주는 서버 내 번호 (1부터)
    pane_id: str
    location: str
    cwd: str
    project: str
    session_id: str  # 매칭 실패 시 ""
    title: str
    state: str       # "working" | "waiting" | "idle" | "unknown"


def discover(
    remote: Remote,
    server: str,
    cache_dir: Path,
    index: SessionIndex | None = None,
) -> list[LiveSession]:
    """서버 tmux에서 claude로 보이는 pane을 찾아 미러된 세션과 매칭하고 번호를 매긴다."""
    panes = [p for p in tmux.list_panes(remote) if p.command in _CLAUDE_COMMANDS]
    panes.sort(key=lambda p: p.location)
    now = time.time()
    out: list[LiveSession] = []
    for i, p in enumerate(panes, start=1):
        project = encode_project(p.cwd)
        proj_dir = cache_dir / "projects" / project
        session_id, title, state = "", "", "unknown"
        jsonls = (
            sorted(proj_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            if proj_dir.is_dir() else []
        )
        if jsonls:
            newest = jsonls[0]
            session_id = newest.stem
            if now - newest.stat().st_mtime < _WORKING_WINDOW_SECS:
                state = "working"
            else:
                state = "idle"
            if index is not None:
                row = index.get_session(server, session_id)
                if row:
                    title = row.title
                    if state != "working" and row.last_role == "assistant":
                        state = "waiting"
        out.append(LiveSession(
            server=server, number=i, pane_id=p.pane_id, location=p.location,
            cwd=p.cwd, project=project, session_id=session_id,
            title=title, state=state,
        ))
    return out
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_sessions.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/sessions.py tests/test_sessions.py
git commit -m "feat: 세션 디스커버리 (pane 매칭·번호·상태 추정)"
```

---

### Task 7: SyncEngine (미러링 + 인덱싱 오케스트레이션)

**Files:**
- Create: `src/cchub/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `ssh.Remote.mirror`, `index.SessionIndex.index_file`
- Produces: `sync.SyncReport(server: str, ok: bool, files: int, events: int, error: str)`; `sync.sync_server(remote: Remote, server: str, claude_dir: str, cache_root: Path, index: SessionIndex) -> SyncReport` — `cache_root/<server>/projects/`로 미러 후 모든 `*/*.jsonl`을 증분 인덱싱

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_sync.py`

```python
import shutil
from pathlib import Path

from cchub.index import SessionIndex
from cchub.ssh import Remote, RunResult
from cchub.sync import sync_server
from tests.conftest import FakeRemote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


class FailingMirrorRemote(FakeRemote):
    def mirror(self, remote_dir, local_dir, timeout=120):
        return RunResult(255, "", "ssh: connect refused")


def test_sync_server_mirrors_then_indexes(tmp_path):
    fake = FakeRemote()
    idx = SessionIndex(tmp_path / "i.db")
    # mirror가 갖다놓았을 파일을 미리 배치 (FakeRemote.mirror는 기록만 함)
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")

    rep = sync_server(fake, "srv1", "~/.claude", tmp_path / "cache", idx)
    assert rep.ok
    assert rep.files == 1 and rep.events == 3
    assert fake.mirrors == [("~/.claude/projects", tmp_path / "cache" / "srv1" / "projects")]
    assert idx.get_session("srv1", "s-1") is not None
    # 두 번째 sync는 변화 없음
    rep2 = sync_server(fake, "srv1", "~/.claude", tmp_path / "cache", idx)
    assert rep2.ok and rep2.files == 0 and rep2.events == 0


def test_sync_server_reports_mirror_failure(tmp_path):
    idx = SessionIndex(tmp_path / "i.db")
    rep = sync_server(FailingMirrorRemote(), "srv1", "~/.claude", tmp_path / "cache", idx)
    assert not rep.ok
    assert "refused" in rep.error
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_sync.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.sync'`

- [ ] **Step 3: 구현** — `src/cchub/sync.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cchub.index import SessionIndex
from cchub.ssh import Remote


@dataclass
class SyncReport:
    server: str
    ok: bool
    files: int = 0    # 새 이벤트가 반영된 파일 수
    events: int = 0   # 반영된 message 수
    error: str = ""


def sync_server(
    remote: Remote,
    server: str,
    claude_dir: str,
    cache_root: Path,
    index: SessionIndex,
) -> SyncReport:
    cache = cache_root / server / "projects"
    r = remote.mirror(f"{claude_dir}/projects", cache)
    if r.rc != 0:
        return SyncReport(server=server, ok=False, error=r.err.strip())
    files = events = 0
    for path in sorted(cache.glob("*/*.jsonl")):
        n = index.index_file(server, path.parent.name, path)
        if n:
            files += 1
            events += n
    return SyncReport(server=server, ok=True, files=files, events=events)
```

- [ ] **Step 4: 통과 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_sync.py -v`
Expected: 2 passed

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/sync.py tests/test_sync.py
git commit -m "feat: SyncEngine (서버 미러링+증분 인덱싱)"
```

---

### Task 8: CLI (init / sync / list / send / tail / search / reindex)

**Files:**
- Create: `src/cchub/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 앞선 모든 모듈. `cli.main(argv: list[str] | None = None) -> int`가 진입점 (`pyproject.toml`의 `cchub` 스크립트가 가리킴).
- Produces (명령):
  - `cchub init` — `$CCHUB_DIR/config.toml` 템플릿 생성 (이미 있으면 거부)
  - `cchub sync` — 모든 서버 미러+인덱싱, 서버별 리포트 출력
  - `cchub list` — 서버별 live 세션: `번호 상태 프로젝트 제목` (내부에서 sync 먼저 수행)
  - `cchub send <server> <번호> <프롬프트>` — 전송 전 재-discover로 번호 검증; 상태 working이면 경고 출력 후 전송(차단하지 않음)
  - `cchub tail <server> <번호> [--live] [-n N]` — 기본: 인덱스에서 마지막 N개 메시지, `--live`: tmux capture-pane 원본
  - `cchub search <질의>` — FTS 검색 결과
  - `cchub reindex` — 인덱스 전체 삭제 후 캐시에서 재구축 (SSH 불필요)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_cli.py`

CLI 테스트는 SSHRemote 생성을 monkeypatch로 FakeRemote로 바꿔 SSH 없이 검증한다.

```python
import shutil
from pathlib import Path

import pytest

from cchub import cli
from cchub.ssh import RunResult
from tests.conftest import FakeRemote

FIXTURE = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"
PANES = "%5\tmain:0.0\t/home/u/proj\tclaude\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """CCHUB_DIR 격리 + config + 미러된 캐시 + FakeRemote 주입."""
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('[servers.srv1]\nhost = "u@h"\n')
    proj = tmp_path / "cache" / "srv1" / "projects" / "-home-u-proj"
    proj.mkdir(parents=True)
    shutil.copy(FIXTURE, proj / "s-1.jsonl")
    fake = FakeRemote({"tmux": RunResult(0, PANES, "")})
    monkeypatch.setattr(cli, "_make_remote", lambda host: fake)
    return tmp_path, fake


def test_init_creates_template(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path / "fresh"))
    assert cli.main(["init"]) == 0
    assert (tmp_path / "fresh" / "config.toml").exists()
    assert cli.main(["init"]) == 1  # 이미 있으면 거부


def test_sync_reports(env, capsys):
    assert cli.main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "srv1" in out and "ok" in out


def test_list_shows_live_sessions(env, capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "srv1" in out and "1" in out and "-home-u-proj" in out


def test_send_resolves_number_and_sends(env, capsys):
    _, fake = env
    assert cli.main(["send", "srv1", "1", "실험 시작해줘"]) == 0
    sent = [c for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    assert sent[0] == ["tmux", "send-keys", "-t", "%5", "-l", "실험 시작해줘"]
    assert sent[1][-1] == "Enter"


def test_send_bad_number_fails(env, capsys):
    assert cli.main(["send", "srv1", "9", "x"]) == 1
    assert "세션 9" in capsys.readouterr().err


def test_tail_from_index(env, capsys):
    cli.main(["sync"])
    assert cli.main(["tail", "srv1", "1", "-n", "2"]) == 0
    out = capsys.readouterr().out
    assert "결과 요약해줘" in out


def test_search(env, capsys):
    cli.main(["sync"])
    assert cli.main(["search", "NUMA"]) == 0
    assert "s-1" in capsys.readouterr().out


def test_reindex(env, capsys):
    cli.main(["sync"])
    assert cli.main(["reindex"]) == 0
    assert "3" in capsys.readouterr().out  # 재인덱싱된 이벤트 수
```

- [ ] **Step 2: 실패 확인**

Run: `cd ~/cchub && .venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cchub.cli'`

- [ ] **Step 3: 구현** — `src/cchub/cli.py`

```python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cchub import sessions, tmux
from cchub.config import Config, ConfigError, cchub_dir, load_config
from cchub.index import SessionIndex
from cchub.ssh import Remote, SSHRemote
from cchub.sync import sync_server

_STATE_MARK = {"working": "●", "waiting": "◌", "idle": "▶", "unknown": "?"}

_TEMPLATE = """\
[general]
sync_interval = 30
stats_interval = 2

# [servers.srv1]
# host = "user@10.0.0.11"      # ssh 접속 문자열 (~/.ssh/config alias 가능)
# results = ["~/exp/**"]       # 결과 수집 경로 (M3에서 사용)
"""


def _make_remote(host: str) -> Remote:
    return SSHRemote(host)


def _ctx() -> tuple[Config, Path, SessionIndex]:
    cfg = load_config()
    root = cchub_dir()
    root.mkdir(parents=True, exist_ok=True)
    return cfg, root, SessionIndex(root / "index.db")


def _sync_all(cfg: Config, root: Path, index: SessionIndex) -> list:
    reports = []
    for name, s in cfg.servers.items():
        reports.append(sync_server(_make_remote(s.host), name, s.claude_dir,
                                   root / "cache", index))
    return reports


def _resolve(cfg: Config, root: Path, index: SessionIndex, server: str, number: int):
    """(remote, LiveSession) 반환. 실패 시 SystemExit 대신 None."""
    s = cfg.servers.get(server)
    if not s:
        print(f"알 수 없는 서버: {server} (설정: {', '.join(cfg.servers)})", file=sys.stderr)
        return None
    remote = _make_remote(s.host)
    live = sessions.discover(remote, server, root / "cache" / server, index)
    for ls in live:
        if ls.number == number:
            return remote, ls
    print(f"{server}에 세션 {number}이(가) 없습니다 (현재 {len(live)}개)", file=sys.stderr)
    return None


def cmd_init(_args) -> int:
    path = cchub_dir() / "config.toml"
    if path.exists():
        print(f"이미 존재합니다: {path}", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_TEMPLATE)
    print(f"생성됨: {path} — 서버를 추가한 뒤 cchub sync를 실행하세요")
    return 0


def cmd_sync(_args) -> int:
    cfg, root, index = _ctx()
    ok = True
    for rep in _sync_all(cfg, root, index):
        if rep.ok:
            print(f"{rep.server}: ok — 파일 {rep.files}개, 이벤트 {rep.events}건 반영")
        else:
            ok = False
            print(f"{rep.server}: 실패 — {rep.error}", file=sys.stderr)
    return 0 if ok else 1


def cmd_list(_args) -> int:
    cfg, root, index = _ctx()
    _sync_all(cfg, root, index)
    for name, s in cfg.servers.items():
        remote = _make_remote(s.host)
        live = sessions.discover(remote, name, root / "cache" / name, index)
        print(f"[{name}]")
        if not live:
            print("  (claude 세션 없음/접속 불가)")
        for ls in live:
            mark = _STATE_MARK.get(ls.state, "?")
            print(f"  {ls.number}  {mark} {ls.state:8s} {ls.project}  {ls.title}")
    return 0


def cmd_send(args) -> int:
    cfg, root, index = _ctx()
    r = _resolve(cfg, root, index, args.server, args.number)
    if r is None:
        return 1
    remote, ls = r
    if ls.state == "working":
        print(f"주의: 세션이 작업 중(●)입니다 — 프롬프트는 큐에 들어갑니다", file=sys.stderr)
    if not tmux.send_prompt(remote, ls.pane_id, args.prompt):
        print("전송 실패 (pane이 사라졌거나 tmux 오류)", file=sys.stderr)
        return 1
    print(f"{args.server} 세션 {args.number}({ls.project})에 전송됨")
    return 0


def cmd_tail(args) -> int:
    cfg, root, index = _ctx()
    r = _resolve(cfg, root, index, args.server, args.number)
    if r is None:
        return 1
    remote, ls = r
    if args.live:
        print(tmux.capture(remote, ls.pane_id, lines=args.n), end="")
        return 0
    if not ls.session_id:
        print("세션 transcript를 아직 찾지 못했습니다 (cchub sync 후 재시도)", file=sys.stderr)
        return 1
    for role, ts, text in index.tail(args.server, ls.session_id, limit=args.n):
        print(f"--- {role} {ts}\n{text}")
    return 0


def cmd_search(args) -> int:
    _cfg, _root, index = _ctx()
    for server, sid, role, ts, snippet in index.search(args.query):
        print(f"{server}  {sid}  {role}  {ts}  {snippet}")
    return 0


def cmd_reindex(_args) -> int:
    _cfg, root, index = _ctx()
    index.forget_all()
    events = 0
    for path in sorted((root / "cache").glob("*/projects/*/*.jsonl")):
        server = path.parents[2].name
        events += index.index_file(server, path.parent.name, path)
    print(f"재인덱싱 완료: 이벤트 {events}건")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cchub", description="멀티 서버 Claude Code 세션 허브")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="설정 템플릿 생성")
    sub.add_parser("sync", help="모든 서버 미러링+인덱싱")
    sub.add_parser("list", help="서버별 live 세션 목록")

    p = sub.add_parser("send", help="세션에 프롬프트 전송")
    p.add_argument("server")
    p.add_argument("number", type=int)
    p.add_argument("prompt")

    p = sub.add_parser("tail", help="세션 최근 대화 보기")
    p.add_argument("server")
    p.add_argument("number", type=int)
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--live", action="store_true", help="tmux 화면 원본 캡처")

    p = sub.add_parser("search", help="전체 이력 FTS 검색")
    p.add_argument("query")

    sub.add_parser("reindex", help="캐시에서 인덱스 재구축")

    args = ap.parse_args(argv)
    handler = {
        "init": cmd_init, "sync": cmd_sync, "list": cmd_list, "send": cmd_send,
        "tail": cmd_tail, "search": cmd_search, "reindex": cmd_reindex,
    }[args.cmd]
    try:
        return handler(args)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인 (전체 테스트)**

Run: `cd ~/cchub && .venv/bin/pytest -v`
Expected: 전체 pass (약 31개)

- [ ] **Step 5: 커밋**

```bash
cd ~/cchub && git add src/cchub/cli.py tests/test_cli.py
git commit -m "feat: cchub CLI (init/sync/list/send/tail/search/reindex)"
```

---

### Task 9: localhost 스모크 테스트 (실물 검증)

**Files:** 없음 (검증 전용 — 문제 발견 시 해당 모듈 수정)

로컬 머신 자체를 "서버"로 등록해 SSH·rsync·tmux 전체 경로를 실물로 검증한다.
전제: `ssh localhost`가 키 인증으로 되는지 먼저 확인하고, 안 되면
`ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys`.

- [ ] **Step 1: 테스트용 CCHUB_DIR과 config 준비**

```bash
export CCHUB_DIR=/tmp/claude-1000/-home-yhkim/bcbd4d23-4f4e-46cc-8bd3-28a4054d28b5/scratchpad/cchub-smoke
cd ~/cchub && .venv/bin/cchub init
cat >> "$CCHUB_DIR/config.toml" <<'EOF'
[servers.local]
host = "localhost"
EOF
```

- [ ] **Step 2: sync 실물 검증**

Run: `.venv/bin/cchub sync`
Expected: `local: ok — 파일 N개, 이벤트 M건 반영` (로컬 `~/.claude/projects/`가 실제로 미러·인덱싱됨. N, M > 0)

- [ ] **Step 3: list/search 실물 검증**

```bash
.venv/bin/cchub list
.venv/bin/cchub search "envector"
```

Expected: list에 현재 tmux 안에서 돌아가는 claude 세션이 보이면 번호·상태 표시 (tmux 밖이면 "claude 세션 없음"이 정상). search는 실제 과거 이력에서 매칭 출력.

- [ ] **Step 4: send/tail 실물 검증 (안전한 대상으로)**

```bash
tmux new-session -d -s cchub-smoke -c /tmp
tmux send-keys -t cchub-smoke "cat" Enter   # claude 대신 cat으로 주입 확인
.venv/bin/cchub list                        # cat pane은 안 보여야 정상 (claude 아님)
tmux kill-session -t cchub-smoke
```

이후 실제 claude가 tmux에서 돌고 있다면: `.venv/bin/cchub tail local 1 --live`로 화면 캡처 확인. `send`는 실제 세션에 영향을 주므로 사용자에게 확인 후 시연.

- [ ] **Step 5: 발견된 문제 수정 후 커밋, README 작성**

`README.md` — 설치(`pip install -e .`), config 예시, 명령어 한 줄씩 요약.

```bash
cd ~/cchub && git add -A && git commit -m "docs: README 및 스모크 테스트 반영"
```

---

## Self-Review 결과 (계획 작성 시 수행)

- 스펙 커버리지: M1 범위(config/SSH pool/동기화/인덱싱/CLI list·send·tail) 전부 태스크에 매핑됨. 스펙의 TUI·CPU바·결과수집·중계·검색 UI는 M2/M3 계획에서 다룸 (`search`는 CLI로 선반영).
- 타입 일관성: `Remote.run/mirror`, `SessionIndex.get_session/list_sessions/search/tail/forget_all`, `sessions.discover(remote, server, cache_dir, index)` — 전 태스크에서 시그니처 동일 확인.
- 플레이스홀더 없음.
