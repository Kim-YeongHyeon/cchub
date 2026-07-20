from __future__ import annotations

import argparse
import sqlite3
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
            if rep.error:
                print(f"주의: {rep.error}", file=sys.stderr)
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
    if tmux.confirm_delivery(remote, ls.pane_id, args.prompt):
        print(f"{args.server} 세션 {args.number}({ls.project})에 전송됨")
    else:
        print(f"{args.server} 세션 {args.number}({ls.project})에 전송됨 (화면 반영 미확인)")
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


def cmd_tui(_args) -> int:
    from cchub.tui.app import run_tui
    run_tui()
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
    sub.add_parser("tui", help="터미널 UI 실행")

    args = ap.parse_args(argv)
    handler = {
        "init": cmd_init, "sync": cmd_sync, "list": cmd_list, "send": cmd_send,
        "tail": cmd_tail, "search": cmd_search, "reindex": cmd_reindex, "tui": cmd_tui,
    }[args.cmd]
    try:
        return handler(args)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
