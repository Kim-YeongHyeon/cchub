from __future__ import annotations

from datetime import datetime
from pathlib import Path

from cchub.config import Config
from cchub.index import SessionIndex

_MAX_FILES_PER_SERVER = 50
_MAX_SESSIONS_PER_SERVER = 5


def generate_briefing(
    cfg: Config,
    root: Path,
    index: SessionIndex,
    now: datetime | None = None,
) -> tuple[Path, str]:
    """수집 결과 + 세션 요약을 담은 브리핑 md를 만들고, 로컬 Claude용 프롬프트를 반환."""
    now = now or datetime.now()
    results_root = root / "results"
    lines = [f"# 실험 결과 브리핑 — {now:%Y-%m-%d %H:%M}", ""]
    for server in sorted(cfg.servers):
        lines.append(f"## {server}")
        sdir = results_root / server
        files = sorted(p for p in sdir.rglob("*") if p.is_file()) if sdir.is_dir() else []
        if files:
            lines.append(f"### 수집된 결과 파일 ({len(files)}개)")
            for f in files[:_MAX_FILES_PER_SERVER]:
                lines.append(f"- {f.relative_to(results_root)}")
            if len(files) > _MAX_FILES_PER_SERVER:
                lines.append(f"- … 외 {len(files) - _MAX_FILES_PER_SERVER}개")
        else:
            lines.append("(수집된 결과 없음 — `cchub results` 실행)")
        rows = index.list_sessions(server)[:_MAX_SESSIONS_PER_SERVER]
        if rows:
            lines.append("### 최근 세션")
            for r in rows:
                label = r.title or r.first_prompt[:60]
                lines.append(f"- {label} — 마지막 활동 {r.last_ts}")
        lines.append("")
    results_root.mkdir(parents=True, exist_ok=True)
    path = results_root / f"briefing-{now:%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines))
    prompt = (
        f"{path} 브리핑 파일을 읽고, {results_root} 아래에 수집된 실험 결과 파일들을 "
        "분석해서 종합 리포트를 작성해줘."
    )
    return path, prompt
