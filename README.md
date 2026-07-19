# cchub

버전 0.2.0. 여러 서버에서 돌아가는 Claude Code 세션을 한 곳에서
확인·검색·제어하는 CLI. 각 서버에 SSH로 접속해 `~/.claude/projects`를
로컬로 미러링하고, tmux pane을 찾아 실행 중인 세션과 매칭한다. 0.2.0부터는
`cchub tui`로 여러 서버의 세션을 한 화면에서 볼 수 있는 인터랙티브 뷰를
제공한다 (의존성 `textual>=8,<9`).

## 설치

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 시작하기

```bash
cchub init                 # ~/.cchub/config.toml 템플릿 생성
# config.toml에 서버를 추가한 뒤:
cchub sync                 # 모든 서버 미러링 + 인덱싱
cchub list                 # 서버별 live 세션 목록
```

### config.toml 예시

```toml
[general]
sync_interval = 30
stats_interval = 2

[servers.srv1]
host = "user@10.0.0.11"      # ssh 접속 문자열. ~/.ssh/config의 Host 별칭도 그대로 사용 가능
results = ["~/exp/**"]       # 결과 수집 경로 (M3에서 사용 예정)

[servers.srv2]
host = "my-alias"            # 예: ~/.ssh/config에 등록된 Host my-alias
claude_dir = "~/.claude"      # 원격의 claude 홈 (기본값)
```

`host`는 `ssh <host>`로 그대로 넘어가므로, `user@ip`든 `~/.ssh/config`의
`Host` 별칭(포트/키 파일 등 포함)이든 동일하게 동작한다.

## 명령어

| 명령어 | 설명 |
|---|---|
| `cchub init` | 설정 템플릿(`config.toml`)을 생성한다 |
| `cchub sync` | 등록된 모든 서버의 `~/.claude/projects`를 미러링하고 새 이벤트를 인덱싱한다 |
| `cchub list` | 동기화 후 서버별로 tmux에서 돌고 있는 claude 세션 목록을 번호·상태와 함께 보여준다 |
| `cchub send <server> <n> <prompt>` | 지정한 세션의 tmux pane에 프롬프트를 전송한다 |
| `cchub tail <server> <n> [-n N] [--live]` | 세션의 최근 대화를 보여준다. `--live`는 인덱스 대신 tmux 화면을 그대로 캡처한다 |
| `cchub search <query>` | 모든 서버의 전체 이력에서 전문(FTS) 검색을 한다 |
| `cchub reindex` | 캐시(미러된 파일)에서 인덱스를 처음부터 재구축한다 |
| `cchub tui` | 모든 서버의 세션을 한 화면(트리+상세+CPU바)에서 보고 제어하는 인터랙티브 TUI를 띄운다 |

## TUI (`cchub tui`)

서버 트리에서 세션을 고르면 오른쪽에 live tmux 화면(또는 저장된
transcript)이 보이고, 상단 바에는 서버별 CPU/메모리 스파크라인이 주기적으로
갱신된다. 하단 입력창에 프롬프트를 적고 Enter를 누르면 선택한 세션의 pane으로
전송된다 (세션이 작업 중이면 먼저 확인을 받는다).

```bash
cchub tui
```

### 키맵

| 키 | 동작 |
|---|---|
| `q` | 종료 |
| `y` | 전체 서버 강제 동기화 (트리 새로고침) |
| `c` | CPU/메모리 바 표시 토글 (끄면 폴링도 멈춘다) |
| `f` | 선택한 세션 화면을 주기적으로 자동 새로고침(팔로우) 토글 |
| `t` | 상세 패널을 live tmux 화면 ↔ 저장된 transcript로 전환 |
| `Enter` (입력창) | 프롬프트를 선택된 세션에 전송. 세션이 작업 중(●)이면 `y`/`n` 확인 후 전송 |

## 데이터 위치

기본적으로 `~/.cchub` 아래에 모든 상태를 둔다 (환경변수 `CCHUB_DIR`로 변경 가능):

- `config.toml` — 설정
- `cache/<server>/projects/` — 서버별 미러링된 `~/.claude/projects` 원본
- `index.db` — SQLite FTS5 세션 인덱스
- `cm/` — (레거시) ssh ControlMaster 소켓. 실제 소켓은 경로 길이 제한을 피하기
  위해 시스템 임시 디렉터리(`$TMPDIR` 또는 `/tmp`) 아래
  `cchub-cm-<uid>-<hash>/`에 둔다

기존 `index.db`가 구버전 스키마(메타데이터 컬럼까지 FTS 인덱싱하던 버전)로
남아 있으면 여는 즉시 자동으로 비워지며, 다음 `cchub sync`(또는
`cchub reindex`)에서 새 스키마로 재구축된다.

## 제약 사항

- 각 원격 서버에는 `tmux`와 `rsync`, 키 기반 SSH 접속이 준비돼 있어야 한다.
- 미러링에는 `rsync --delete`를 쓰지 않는다 — 원격에서 오래된 세션 로그가
  정리되더라도 로컬에 모인 이력은 영구 보존된다.
- tmux pane은 `pane_current_command`가 `claude` 또는 `node`인 것만 세션
  후보로 본다 (claude 실행 방식에 따라 다르게 보이기 때문). 그 외 명령(예:
  `cat`, `bash`)이 돌고 있는 pane은 목록/전송 대상에서 제외된다.
- 같은 작업 디렉터리(cwd)에서 claude pane을 두 개 이상 띄우면, 둘 다 그
  프로젝트의 가장 최근 jsonl 세션에 매칭된다 — tail/title/상태가 실제로는
  다른 pane의 내용을 가리킬 수 있다. M1의 알려진 한계이며, M2에서
  프로세스 기반 매칭으로 개선할 예정이다.

## 스모크 테스트

`cchub-smoke` (localhost, SSH 포트 7777)를 서버로 등록해 실제 SSH·rsync·tmux
경로를 검증했다: `sync`가 실제 `~/.claude/projects`를 미러링·인덱싱하고,
`list`/`search`/`tail`(인덱스 및 `--live` 캡처 모두)이 실제 데이터로 동작함을
확인했다. 실제 `~/.claude`에서 파일 22개·이벤트 6658건이 동기화됐고, 살아있는
tmux pane들이 정상적으로 발견됐다. 이 과정에서 `CCHUB_DIR`이 깊은 경로에 있을
때 ssh `ControlPath`가 AF_UNIX 소켓 108바이트 제한을 넘어 실패하는 결함을
발견해 수정했다.
