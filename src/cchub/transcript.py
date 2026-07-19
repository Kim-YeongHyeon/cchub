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
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
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
