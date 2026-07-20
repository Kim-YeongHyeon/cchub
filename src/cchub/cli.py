from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from cchub import sessions, skills as skills_mod, tmux
from cchub.config import Config, ConfigError, cchub_dir, load_config
from cchub.index import SessionIndex
from cchub.ssh import Remote, SSHRemote
from cchub.sync import sync_server
from cchub.tmux import CLAUDE_COMMANDS

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


def _parse_loc(cfg: Config, loc: str) -> tuple[str | None, str]:
    """'srv1:~/path' → ('srv1', '~/path'), 로컬 경로 → (None, 경로).

    콜론 접두가 등록된 서버가 아니고 경로 문자(/)도 없으면 오타로 간주해 에러.
    """
    if ":" in loc:
        name, _, path = loc.partition(":")
        if name in cfg.servers:
            return name, path
        if "/" not in name:
            raise ValueError(
                f"알 수 없는 서버: {name} (설정: {', '.join(cfg.servers)})")
    return None, loc


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


def cmd_results(args) -> int:
    from cchub.results import collect_results
    cfg, root, _index = _ctx()
    servers = [args.server] if args.server else list(cfg.servers)
    ok = True
    for name in servers:
        if name not in cfg.servers:
            print(f"알 수 없는 서버: {name}", file=sys.stderr)
            return 1
        rep = collect_results(cfg, root, name, _make_remote)
        if rep.ok:
            print(f"{name}: ok → {root / 'results' / name}")
        else:
            ok = False
            print(f"{name}: 실패 패턴 {', '.join(rep.failed)}", file=sys.stderr)
    return 0 if ok else 1


def cmd_tui(_args) -> int:
    from cchub.tui.app import run_tui
    run_tui()
    return 0


def cmd_brief(_args) -> int:
    from cchub.briefing import generate_briefing
    cfg, root, index = _ctx()
    path, prompt = generate_briefing(cfg, root, index)
    print(f"브리핑 생성됨: {path}")
    print()
    print("아래 프롬프트를 로컬 Claude Code 세션에 붙여넣으세요:")
    print()
    print(prompt)
    return 0


def cmd_push(args) -> int:
    import tempfile

    cfg, root, _index = _ctx()
    try:
        src_srv, src_path = _parse_loc(cfg, args.src)
        dst_srv, dst_path = _parse_loc(cfg, args.dst)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if src_srv is None and dst_srv is None:
        print("src/dst 중 하나는 <서버>:<경로> 형식이어야 합니다", file=sys.stderr)
        return 1
    if src_srv and dst_srv:
        relay_root = root / "relay"
        relay_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=relay_root))
        try:
            r = _make_remote(cfg.servers[src_srv].host).fetch(src_path, tmp)
            if r.rc != 0:
                print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
                return 1
            r = _make_remote(cfg.servers[dst_srv].host).push(tmp, dst_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    elif src_srv:
        r = _make_remote(cfg.servers[src_srv].host).fetch(
            src_path, Path(dst_path).expanduser())
    else:
        r = _make_remote(cfg.servers[dst_srv].host).push(
            Path(src_path).expanduser(), dst_path)
    if r.rc != 0:
        print(f"전송 실패: {r.err.strip()}", file=sys.stderr)
        return 1
    print("완료")
    return 0


def _local_lib() -> Path:
    return Path.home() / ".claude" / "skills"


def _scan_server(cfg: Config, name: str) -> list:
    """서버 하나의 스킬 스캔 (pane cwd + skill_paths). 실패 격리."""
    s = cfg.servers[name]
    remote = _make_remote(s.host)
    try:
        cwds = [p.cwd for p in tmux.list_panes(remote) if p.command in CLAUDE_COMMANDS]
        return skills_mod.scan_skills(remote, name, cwds, s.skill_paths)
    except Exception:  # noqa: BLE001
        return []


def _resolve_skill(cfg: Config, server: str, name: str):
    """서버에서 스킬 참조 해석: personal 우선, 프로젝트 유일 매칭. 실패 시 None+메시지 출력."""
    if server not in cfg.servers:
        print(f"알 수 없는 서버: {server}", file=sys.stderr)
        return None
    found = [i for i in _scan_server(cfg, server) if i.name == name]
    personal = [i for i in found if i.scope == "personal"]
    if personal:
        return personal[0]
    if len(found) == 1:
        return found[0]
    if not found:
        print(f"{server}에서 스킬을 찾지 못함: {name}", file=sys.stderr)
    else:
        paths = ", ".join(i.path for i in found)
        print(f"{server}에 동명 스킬이 여러 개: {paths}", file=sys.stderr)
    return None


def cmd_skills(args) -> int:
    cfg, root, _index = _ctx()
    if args.skills_cmd == "list":
        rows = skills_mod.local_skills(_local_lib())
        servers = [args.server] if args.server else list(cfg.servers)
        for name in servers:
            if name not in cfg.servers:
                print(f"알 수 없는 서버: {name}", file=sys.stderr)
                return 1
            found = _scan_server(cfg, name)
            if not found:
                print(f"[{name}] (스킬 없음/접속 불가)")
            rows += found
        for i in rows:
            desc = i.description[:60]
            print(f"{i.server:10s} {i.scope:8s} {i.name:24s} {desc}  ({i.path})")
        return 0

    if args.skills_cmd == "pull":
        info = _resolve_skill(cfg, args.server, args.name)
        if info is None:
            return 1
        dest = _local_lib() / info.name
        if dest.exists() and not args.force:
            print(f"이미 존재: {dest} (덮어쓰려면 --force)", file=sys.stderr)
            return 1
        r = skills_mod.pull_skill(_make_remote(cfg.servers[args.server].host), info, _local_lib())
        if r.rc != 0:
            print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
            return 1
        print(f"가져옴: {dest}")
        return 0

    if args.skills_cmd == "deploy":
        ok = True
        for name in args.servers:
            if name not in cfg.servers:
                print(f"알 수 없는 서버: {name}", file=sys.stderr)
                return 1
            r = skills_mod.deploy_skill(_make_remote(cfg.servers[name].host), _local_lib(), args.name)
            if r.rc == 0:
                print(f"{name}: 배포됨 (~/.claude/skills/{args.name})")
            else:
                ok = False
                print(f"{name}: 실패 — {r.err.strip()}", file=sys.stderr)
        return 0 if ok else 1

    if args.skills_cmd == "copy":
        import tempfile
        info = _resolve_skill(cfg, args.src_server, args.name)
        if info is None:
            return 1
        relay_root = root / "relay"
        relay_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=relay_root))
        try:
            r = skills_mod.pull_skill(_make_remote(cfg.servers[args.src_server].host), info, tmp)
            if r.rc != 0:
                print(f"가져오기 실패: {r.err.strip()}", file=sys.stderr)
                return 1
            ok = True
            for name in args.dst_servers:
                if name not in cfg.servers:
                    print(f"알 수 없는 서버: {name}", file=sys.stderr)
                    return 1
                r = skills_mod.deploy_skill(_make_remote(cfg.servers[name].host), tmp, info.name)
                if r.rc == 0:
                    print(f"{name}: 복사됨")
                else:
                    ok = False
                    print(f"{name}: 실패 — {r.err.strip()}", file=sys.stderr)
            return 0 if ok else 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if args.skills_cmd == "delete":
        if args.server not in cfg.servers:
            print(f"알 수 없는 서버: {args.server}", file=sys.stderr)
            return 1
        if not skills_mod.valid_skill_name(args.name):
            print(f"잘못된 스킬 이름: {args.name}", file=sys.stderr)
            return 1
        if not args.yes:
            print(f"{args.server}의 개인 스킬 ~/.claude/skills/{args.name} 을(를) 삭제합니다.")
            if input(f"확인을 위해 스킬 이름을 다시 입력하세요: ").strip() != args.name:
                print("취소됨", file=sys.stderr)
                return 1
        r = skills_mod.delete_skill(_make_remote(cfg.servers[args.server].host), args.name)
        if r.rc != 0:
            print(f"삭제 실패: {r.err.strip()}", file=sys.stderr)
            return 1
        print("삭제됨")
        return 0
    return 1


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
    sub.add_parser("brief", help="수집 결과 종합 브리핑 생성")

    p = sub.add_parser("results", help="실험 결과 수집 (config의 results 패턴)")
    p.add_argument("server", nargs="?", help="생략 시 전체 서버")

    p = sub.add_parser("push", help="파일 중계 (<서버>:<경로> 또는 로컬 경로)")
    p.add_argument("src")
    p.add_argument("dst")

    ps = sub.add_parser("skills", help="skill 통합 관리")
    ssub = ps.add_subparsers(dest="skills_cmd", required=True)
    q = ssub.add_parser("list", help="서버별 스킬 조회")
    q.add_argument("server", nargs="?")
    q = ssub.add_parser("pull", help="서버 스킬 → 로컬 라이브러리")
    q.add_argument("server"); q.add_argument("name"); q.add_argument("--force", action="store_true")
    q = ssub.add_parser("deploy", help="로컬 스킬 → 서버들(개인 스킬)")
    q.add_argument("name"); q.add_argument("servers", nargs="+")
    q = ssub.add_parser("copy", help="서버 간 스킬 복사 (로컬 경유)")
    q.add_argument("src_server"); q.add_argument("name"); q.add_argument("dst_servers", nargs="+")
    q = ssub.add_parser("delete", help="서버 개인 스킬 삭제")
    q.add_argument("server"); q.add_argument("name"); q.add_argument("--yes", action="store_true")

    args = ap.parse_args(argv)
    handler = {
        "init": cmd_init, "sync": cmd_sync, "list": cmd_list, "send": cmd_send,
        "tail": cmd_tail, "search": cmd_search, "reindex": cmd_reindex, "tui": cmd_tui,
        "brief": cmd_brief, "results": cmd_results, "push": cmd_push, "skills": cmd_skills,
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
