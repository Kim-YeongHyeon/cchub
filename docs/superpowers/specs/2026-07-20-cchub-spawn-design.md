# cchub spawn — 원격 tmux 세션 + claude 새 세션 생성 설계

날짜: 2026-07-20
상태: 설계 승인됨

## 목적

원격 서버에서 tmux 세션을 새로 만들고 그 안에서 Claude Code를 기동해, 로컬에서
한 번에 원격 작업 세션을 띄운다. 선택적으로 초기 프롬프트까지 주입한다. 기존
send-keys 플러밍(리터럴 `-l --`) 위에 얇게 얹는다.

## 요구사항 (사용자 확정)

1. **용도**: 세션 생성 + (선택) 초기 프롬프트 주입 둘 다.
2. **tmux 배치**: 새 **detached** 세션 (`new-session -d`). 기존 세션과 안 섞임.
3. **실행 플래그**: 기본 `claude --dangerously-skip-permissions`, `--safe`로 일반 `claude`.
4. **생성 후 확인**: **tmux 세션 생성까지만**을 성공 기준으로 본다 (claude 기동 여부는
   성공 판정에 넣지 않음).
5. **초기 프롬프트 전달**: 프롬프트가 있을 때만, pane command가 claude/node가 될 때까지
   최대 ~10초 폴링 후 전송. 타임아웃이어도 세션 생성은 성공.
6. **이름/cwd**: 세션명 기본 자동 `cchub-<n>`(충돌 회피), `--name`으로 지정. cwd는 선택
   인자, 생략 시 원격 `$HOME`.
7. **범위**: CLI + TUI 키(`N`). migrate는 별도 기능(이 spec 범위 외).
8. **문서**: README에 상세 사용법 추가.

## 설계

### 0. 공용 경로 렌더 추출 (선행 리팩터)

`skills.py::_render_root`(틸드→`"$HOME"`, 그 외 `shlex.quote`)를
`ssh.py::render_remote_path(path: str) -> str`로 이동한다.

- `skills.py`는 `from cchub.ssh import render_remote_path`로 대체 (기존 skills 테스트
  **무수정 통과**가 회귀 게이트).
- `doctor.py::_projects_check_script`의 인라인 틸드 확장도 이 함수를 쓰도록 교체
  (이월된 T1-M1 DRY 및 `~/`→`"$HOME"/''` 엣지 불일치 해소).
- spawn의 cwd 렌더도 이 함수 사용.

### 1. tmux 계층 (`tmux.py`, 순수·Remote 주입)

```python
@dataclass
class SpawnResult:
    ok: bool                       # tmux 세션 생성 성공 여부 (성공 기준)
    name: str = ""                 # 실제 세션명
    prompt_sent: bool | None = None  # None=프롬프트 미요청, True/False=전달 확인 여부
    error: str = ""

def list_session_names(remote: Remote) -> list[str]:
    """tmux list-sessions -F '#{session_name}'. 서버 미기동/미설치 시 []."""

def spawn_session(
    remote: Remote,
    cwd: str,                      # "~" 또는 절대경로 등 (render_remote_path로 확장)
    launch_cmd: str,              # 예: "claude --dangerously-skip-permissions"
    name: str | None = None,       # None이면 cchub-<n> 자동
    prompt: str | None = None,
    poll_attempts: int = 20,
    poll_interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> SpawnResult
```

`spawn_session` 시퀀스:

1. 이름 결정: `name`이 주어지면 그대로(형식 검증은 CLI/TUI 진입부 책임), 없으면
   `list_session_names`로 `cchub-1,2,…` 중 미사용 최소 번호.
2. `remote.run(["sh", "-c", f"tmux new-session -d -s {shlex.quote(name)} -c {render_remote_path(cwd)}"])`.
   rc≠0이면 `SpawnResult(ok=False, name=name, error=err)` 반환(세션 미생성).
3. 실행 명령 주입: `send-keys -t {name} -l -- {launch_cmd}` → `send-keys -t {name} Enter`.
   (기존 `send_prompt`와 동일한 `-l --` 안전 패턴. 타겟은 세션명 → 활성 pane.)
4. `prompt`가 None이면 `SpawnResult(ok=True, name=name, prompt_sent=None)`.
5. `prompt`가 있으면 폴링: `poll_attempts`회 반복하며 `list_panes`에서 location이
   `f"{name}:"`로 시작하는 pane의 command가 `CLAUDE_COMMANDS`에 들면 `send_prompt(prompt)`
   후 `prompt_sent=True`. 매 실패마다 `sleep(poll_interval)`. 끝까지 못 뜨면
   `prompt_sent=False` (세션은 여전히 ok=True).

### 2. CLI (`cli.py::cmd_spawn`)

```
cchub spawn <server> [cwd] [--name NAME] [--safe] [--prompt TEXT]
```

- 서버 해석: `cfg.servers.get(server)` 직접 사용(라이브 세션 불필요 — `_resolve` 아님).
  없으면 `알 수 없는 서버: … (설정: …)`, exit 1.
- `--name` 검증: `[A-Za-z0-9_-]+`만 허용, 위반 시 거부(exit 1). 또한 지정 이름이
  `list_session_names`에 이미 있으면 `이미 존재하는 세션명: NAME` 거부(exit 1).
- `launch_cmd`: `--safe`면 `"claude"`, 아니면 `"claude --dangerously-skip-permissions"`.
- `cwd`: 인자 생략 시 `"~"`.
- 성공 출력:
  `srv1: 세션 cchub-2 생성됨 (cwd=~/proj) — tmux attach -t cchub-2`
  프롬프트 미전달 시 추가: `주의: 초기 프롬프트 미전달 — claude 기동 확인 실패, attach 후 직접 입력` (stderr).
- exit code: `SpawnResult.ok`이면 0, 아니면 1. (프롬프트 미전달은 0 유지.)
- argparse 등록 + handler dict.

### 3. TUI (`app.py`, `screens.py`)

**키**: `Binding("N", "spawn", "새세션")` (소문자 `n`은 모달의 취소와 충돌해 대문자).

**서버 노드 해석 수정 (선행 필요)**:
- `apply_snapshots`에서 서버 노드를 `tree.root.add(label, expand=True, data=name)`로
  생성(현재 data 없음).
- `on_tree_node_selected` 가드를 `if isinstance(event.node.data, LiveSession):`로 좁힘
  (서버 노드 선택이 `self.selected`를 문자열로 오염시키던 회귀 방지).

**`action_spawn`**: `tree.cursor_node.data`로 대상 서버 결정 —
`LiveSession`이면 `.server`, `str`이면 그 이름, 그 외/None이면
`서버를 선택하세요` notify. 결정되면 `SpawnScreen`을 push, 결과 `(cwd, prompt)`를
받아 `spawn_worker(server, cwd, prompt)` 호출.

**`SpawnScreen(ModalScreen[tuple[str, str] | None])`** (screens.py, SearchScreen 스타일):
cwd Input(기본값 `~`) + 프롬프트 Input(빈 값 허용). 프롬프트 Input에서 Enter →
`dismiss((cwd, prompt))`. Escape → `dismiss(None)`.

**`spawn_worker`** (`@work(thread=True, exclusive=True, group="spawn", exit_on_error=False)`):
`get_current_worker` + `is_cancelled` 가드. `spawn_session` 호출(TUI는 기본
`--dangerously-skip-permissions`, safe 토글 없음 — YAGNI). 완료 후
`call_from_thread`로 notify(성공/프롬프트 미전달 경고) + `action_refresh`(트리 갱신).

### 4. 보안

- 세션명: `[A-Za-z0-9_-]+` 검증(진입부) + `shlex.quote`(tmux 인자).
- cwd: `render_remote_path`(틸드는 `"$HOME"`, 그 외 `shlex.quote`) — 셸 주입 차단.
- launch_cmd: cchub가 조립하는 고정 문자열(`--safe` 토글만), send-keys `-l --`로 리터럴.
- prompt: 기존 `send_prompt`의 `-l --` 경로.

### 5. 테스트

- **render_remote_path 추출**: 기존 skills 테스트 무수정 통과 + 신규 단위 테스트
  (`~`, `~/x`, `~/`, 절대경로, 공백 포함 경로).
- **tmux.spawn_session (FakeRemote)**: 이름 자동생성(응답에 `cchub-1` 있으면 `cchub-2`),
  new-session argv가 `sh -c`+cwd 렌더, launch `-l --` 주입, safe/기본 명령 차이,
  `--prompt` 시 폴링→send / 미제공 시 send 없음, 폴링 타임아웃 시 `prompt_sent=False`
  (no-op `sleep` 주입으로 즉시), new-session rc≠0 시 `ok=False`.
- **cli.cmd_spawn**: 인자 파싱, `--safe`/`--name`/cwd 기본값, 이름 형식 거부,
  기존 이름 거부, 알 수 없는 서버, 출력·exit code.
- **TUI**: `N` 키 → `SpawnScreen` push; 서버 노드/세션 노드/미선택 각각의 서버 해석;
  `on_tree_node_selected`가 서버 노드(str data)에서 `self.selected`를 오염시키지 않음
  (회귀 테스트); `SpawnScreen` 제출이 올바른 인자로 워커 호출.

### 6. 문서 (README)

기존 절 스타일(예: "### skill 통합 관리")에 맞춰 **### 원격 세션 생성** 추가:
- `cchub spawn` 시그니처와 각 인자/플래그(`cwd` 기본 `$HOME`, `--name`, `--safe`, `--prompt`).
- 기본 `--dangerously-skip-permissions` on의 의미와 주의(원격 무인 자동화 목적).
- attach 방법(`tmux attach -t <name>`), 초기 프롬프트 폴링 동작 설명.
- TUI `N` 키.
- bash 예시 블록 포함.

## 범위 외 (명시)

- migrate(세션 jsonl 이전 + resume) — 별도 spec.
- TUI safe 토글, 세션 종료/삭제 명령, 다중 창(window) 생성.
- claude 미설치/미인증 사전 점검(doctor로 별도 진단).
