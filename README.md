# cchub

여러 서버에서 돌아가는 Claude Code 세션을 한 곳에서 확인·검색·제어하는 CLI.
각 서버에 SSH로 접속해 `~/.claude/projects`를 로컬로 미러링하고, tmux pane을
찾아 실행 중인 세션과 매칭한다.

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

## 데이터 위치

기본적으로 `~/.cchub` 아래에 모든 상태를 둔다 (환경변수 `CCHUB_DIR`로 변경 가능):

- `config.toml` — 설정
- `cache/<server>/projects/` — 서버별 미러링된 `~/.claude/projects` 원본
- `index.db` — SQLite FTS5 세션 인덱스
- `cm/` — (레거시) ssh ControlMaster 소켓. 실제 소켓은 경로 길이 제한을 피하기
  위해 시스템 임시 디렉터리(`$TMPDIR` 또는 `/tmp`) 아래
  `cchub-cm-<uid>-<hash>/`에 둔다

## 제약 사항

- 각 원격 서버에는 `tmux`와 `rsync`, 키 기반 SSH 접속이 준비돼 있어야 한다.
- 미러링에는 `rsync --delete`를 쓰지 않는다 — 원격에서 오래된 세션 로그가
  정리되더라도 로컬에 모인 이력은 영구 보존된다.
- tmux pane은 `pane_current_command`가 `claude` 또는 `node`인 것만 세션
  후보로 본다 (claude 실행 방식에 따라 다르게 보이기 때문). 그 외 명령(예:
  `cat`, `bash`)이 돌고 있는 pane은 목록/전송 대상에서 제외된다.

## 스모크 테스트

`cchub-smoke` (localhost, SSH 포트 7777)를 서버로 등록해 실제 SSH·rsync·tmux
경로를 검증했다: `sync`가 실제 `~/.claude/projects`를 미러링·인덱싱하고,
`list`/`search`/`tail`(인덱스 및 `--live` 캡처 모두)이 실제 데이터로 동작함을
확인했다. 이 과정에서 `CCHUB_DIR`이 깊은 경로에 있을 때 ssh `ControlPath`가
AF_UNIX 소켓 108바이트 제한을 넘어 실패하는 결함을 발견해 수정했다 (자세한
내용은 `.superpowers/sdd/task-9-report.md` 참고).
