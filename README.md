# cchub

**여러 서버에 흩어진 Claude Code 세션을 한 곳에서 보고, 검색하고, 조종하는 허브.**

여러 대의 서버에서 tmux 안에 Claude Code를 띄워놓고 일하다 보면 "어느 서버에서
무슨 작업을 하고 있었지?", "그 실험 결과 어디 있지?", "이 스킬 저 서버에도
깔아야 하는데"가 반복됩니다. cchub는 로컬 머신 한 곳에서 이 모든 것을
해결합니다 — **서버에는 아무것도 설치하지 않습니다** (sshd, tmux, rsync만 있으면 됨).

```
┌─ 로컬 머신 ────────────────────────────────┐        ┌─ 서버 1..N ──────────┐
│  cchub CLI / TUI                           │  ssh   │  tmux 안의            │
│  · 세션 트리·live 뷰·프롬프트 전송           │ ─────▶ │  Claude Code 세션들   │
│  · 전 서버 이력 검색 (SQLite FTS)           │ rsync  │  ~/.claude/projects   │
│  · 실험 결과 수집·종합 브리핑               │ ◀───── │  실험 결과 파일        │
│  · skill 조회/배포/복사/삭제                │        │  .claude/skills       │
└────────────────────────────────────────────┘        └──────────────────────┘
```

주요 기능:

- **세션 현황판** — 서버별로 tmux에서 돌고 있는 Claude 세션을 번호·상태(작업중●/입력대기◌/유휴▶)·제목과 함께 나열
- **원격 프롬프트 전송** — 로컬에서 특정 서버의 특정 세션에 프롬프트 주입 (전송 전 pane 검증, 전송 후 반영 확인)
- **영구 이력 + 전문 검색** — 모든 서버의 대화 이력을 로컬에 미러링·인덱싱. 서버가 30일 후 로그를 지워도 로컬엔 영구 보존, FTS로 즉시 검색
- **실험 결과 수집·종합** — 서버별 결과 파일을 글롭 패턴으로 수집하고, 로컬 Claude Code에 붙여넣을 종합 브리핑 프롬프트 생성
- **파일 중계** — 로컬↔서버, 서버↔서버(로컬 경유) 파일 전송
- **skill 통합 관리** — 서버별 개인/프로젝트 skill 조회, 가져오기, 배포, 서버 간 복사, 삭제
- **TUI** — 위 전부를 한 화면에서. 상단엔 서버별 CPU/메모리 실시간 스파크라인
- **Claude API 미사용** — LLM 호출 없이 로컬 파일과 SSH만 사용. 분석·종합은 사용자의 로컬 Claude Code 세션이 수행

## 요구사항

- **로컬**: Python ≥ 3.13, `rsync`, OpenSSH 클라이언트
- **각 서버**: sshd(키 기반 인증), `tmux`, `rsync` — 그 외 설치 불필요
- 서버의 Claude Code 세션은 tmux 안에서 실행 중이어야 함

## 설치

```bash
git clone https://github.com/Kim-YeongHyeon/cchub.git
cd cchub
python3 -m venv .venv
.venv/bin/pip install -e .
# 이후 .venv/bin/cchub 로 실행하거나, PATH에 연결:
ln -s "$PWD/.venv/bin/cchub" ~/.local/bin/cchub
```

## 빠른 시작

```bash
cchub init                 # ~/.cchub/config.toml 템플릿 생성
$EDITOR ~/.cchub/config.toml   # 서버 추가 (아래 예시 참고)
cchub sync                 # 모든 서버 미러링 + 인덱싱
cchub list                 # 서버별 live 세션 목록
cchub tui                  # 또는 바로 TUI로
```

### config.toml

```toml
[general]
sync_interval = 30           # TUI 자동 동기화 주기 (초)
stats_interval = 2           # CPU/메모리 폴링 주기 (초)

[servers.srv1]
host = "user@10.0.0.11"      # ssh 접속 문자열
results = ["~/exp/**", "~/bench/*.json"]   # 결과 수집 글롭 패턴 (선택)

[servers.srv2]
host = "my-alias"            # ~/.ssh/config 의 Host 별칭도 그대로 동작 (포트/키 포함)
claude_dir = "~/.claude"     # 원격 claude 홈 (기본값)
skill_paths = ["~/my-project"]   # 프로젝트 skill 스캔 추가 경로 (선택)
```

| 키 | 기본값 | 설명 |
|---|---|---|
| `general.sync_interval` | 30 | TUI가 세션 목록을 자동 갱신하는 주기(초) |
| `general.stats_interval` | 2 | CPU/메모리 바 폴링 주기(초) |
| `servers.<이름>.host` | (필수) | `ssh <host>`로 그대로 전달 — `user@ip` 또는 ssh config 별칭 |
| `servers.<이름>.results` | `[]` | `cchub results`가 수집할 원격 글롭 패턴들 |
| `servers.<이름>.claude_dir` | `~/.claude` | 원격 Claude Code 데이터 디렉터리 |
| `servers.<이름>.skill_paths` | `[]` | tmux에 안 떠 있어도 skill을 스캔할 프로젝트 경로들 |

모든 로컬 상태는 `~/.cchub`에 저장됩니다 (환경변수 `CCHUB_DIR`로 변경 가능).

## CLI 사용법

### 세션 확인·제어

```bash
cchub sync                        # 전 서버 ~/.claude/projects 미러링 + 증분 인덱싱
cchub list                        # 서버별 live 세션: 번호·상태·프로젝트·제목
cchub send srv1 3 "실험 이어서 돌려줘"   # 서버1의 3번 세션에 프롬프트 전송
cchub tail srv1 3                 # 그 세션의 최근 대화 (인덱스 기반)
cchub tail srv1 3 --live          # tmux 화면을 그대로 캡처해서 보기
```

- `send`는 전송 직전에 대상 pane이 살아있는 Claude 세션인지 검증하고,
  전송 후 화면에 반영됐는지 1회 확인합니다.
- 세션이 작업 중(●)이어도 전송은 가능합니다 — Claude Code가 입력을 큐잉합니다.

### 이력·검색

```bash
cchub search "NUMA 실험"          # 전 서버 전체 이력 전문(FTS) 검색
cchub reindex                     # 로컬 미러에서 인덱스 전체 재구축 (SSH 불필요)
```

서버 쪽이 오래된 로그를 정리해도 로컬 미러에는 영구 보존됩니다
(`rsync --delete`를 쓰지 않음).

### 실험 결과 수집·종합

여러 서버의 실험 결과를 모아 로컬 Claude Code로 종합 리포트를 만드는 흐름:

```bash
# 1. config의 results 패턴에 따라 수집 (→ ~/.cchub/results/<server>/)
cchub results                     # 전체 서버 (또는: cchub results srv1)

# 2. 브리핑 생성 — 수집 파일 목록 + 서버별 최근 세션 요약을 md로 정리
cchub brief
# 출력된 프롬프트를 로컬 Claude Code 세션에 붙여넣으면 종합 리포트 작성 시작
```

cchub 자체는 LLM을 호출하지 않습니다 — 브리핑 파일과 붙여넣을 프롬프트만
만들고, 실제 분석은 사용자의 로컬 Claude 세션이 수행합니다.

### 파일 중계

```bash
cchub push srv1:~/exp/out.json ./results/     # 서버 → 로컬
cchub push ./data.json srv2:~/inbox           # 로컬 → 서버
cchub push srv1:~/exp/out.json srv2:~/inbox   # 서버 → 서버 (로컬 경유, 임시파일 자동 정리)
```

글롭(`srv1:~/exp/*.json`)은 원격 셸이 확장합니다.

### skill 통합 관리

여러 서버에 흩어진 Claude Code skill(`SKILL.md` 디렉터리)을 관리합니다.

```bash
cchub skills list                          # 로컬 라이브러리 + 전 서버 skill 조회
cchub skills pull srv1 e2e-run             # 서버 skill → 로컬 ~/.claude/skills/
cchub skills deploy my-skill srv1 srv2     # 로컬 skill → 서버들의 개인 skill로 배포
cchub skills copy srv1 e2e-run srv2 srv3   # 서버 간 복사 (로컬 경유)
cchub skills delete srv1 old-skill         # 개인 skill 삭제 (이름 재입력 확인, --yes로 생략)
```

동작 원칙:

- **조회는 개인 + 프로젝트 skill 모두**: 서버당 (1) `~/.claude/skills`,
  (2) 현재 tmux에 살아있는 claude pane들의 cwd 아래 `.claude/skills`,
  (3) config `skill_paths`에 등록한 경로 — 를 합쳐 스캔합니다.
- **쓰기(배포/삭제)는 개인 skill(`~/.claude/skills`)만**: 프로젝트 skill은
  해당 저장소의 git으로 관리되는 것이 원칙이라 cchub가 절대 만들거나 지우지
  않습니다 (쓰기 경로가 코드 수준에서 고정돼 있음).
- `pull`은 개인 skill을 우선 매칭하고, 없으면 프로젝트 skill 중 유일 매칭일
  때만 가져옵니다. 로컬에 이미 있으면 `--force`가 필요합니다.
- `deploy`/`copy`는 rsync 덮어쓰기(`--delete` 없음) — 로컬에서 지운 파일이
  서버에 남을 수 있습니다. 완전히 갈아엎으려면 `delete` 후 `deploy`.
- `delete`는 skill 이름 재입력 확인 + 이름 검증(`[A-Za-z0-9_-]+`) + 고정 경로
  프리픽스로 실수·경로 탈출을 차단합니다.

## TUI (`cchub tui`)

```
┌──────────────────────────────────────────────────────────────┐
│ srv1 ▂▄▆▂ 34% 12G/64G   srv2 ▆▇▇▆ 78% 41G/128G              │ ← CPU/메모리 (c 토글)
├───────────────┬──────────────────────────────────────────────┤
│ ▼ srv1        │  [live] 선택한 세션의 tmux 화면 또는 transcript │
│   1 ● proj-a  │                                              │
│   2 ◌ proj-b  │  ──────────────────────────────              │
│ ▼ srv2        │  > 프롬프트 입력 후 Enter로 전송               │
│   1 ▶ bench   │                                              │
└───────────────┴──────────────────────────────────────────────┘
  ● 작업중   ◌ 입력대기   ▶ 유휴
```

| 키 | 동작 |
|---|---|
| `↑↓` + `Enter` | 트리에서 세션 선택 |
| 입력창 `Enter` | 선택 세션에 프롬프트 전송 (작업중이면 y/n 확인 모달) |
| `t` | 상세 패널: live tmux 화면 ↔ 저장된 transcript 전환 |
| `f` | 팔로우 모드 — 2초마다 화면 자동 새로고침 토글 |
| `c` | CPU/메모리 바 토글 (끄면 폴링도 중단) |
| `/` | 전 서버 FTS 검색 → 행 선택 시 해당 세션 transcript 표시 |
| `h` | 전 서버 이력 타임라인 → 입력창으로 즉시 필터링 |
| `r` | 전 서버 실험 결과 수집 (`cchub results` 동일) |
| `A` | 종합 브리핑 생성 + 붙여넣을 프롬프트 표시 (`cchub brief` 동일) |
| `s` | 전 서버 skill 조회 (읽기 전용 — 배포/삭제는 CLI로) |
| `y` | 즉시 동기화 (기본은 `sync_interval`마다 자동) |
| `q` | 종료 |

서버가 접속 불가여도 TUI는 계속 동작합니다 — 해당 서버만 ⚠/⨯offline으로
표시되고 나머지는 정상 갱신됩니다.

## 로컬 Claude Code와 함께 쓰기

cchub의 CLI는 로컬 Claude Code 세션의 손발이 되도록 설계됐습니다.
로컬 Claude에게 이렇게 말하면:

> "서버 1의 3번 세션에서 배치 크기 128로 실험 다시 돌려줘"
> "srv2의 결과 파일들 가져와서 srv3에도 넣어줘"
> "e2e-run 스킬 전 서버에 배포해줘"

Claude가 `cchub send srv1 3 "..."`, `cchub push ...`, `cchub skills deploy ...`를
알아서 호출합니다. 결과 종합도 마찬가지로 `cchub brief`가 출력한 프롬프트를
로컬 Claude에 붙여넣는 것으로 이어집니다.

## 동작 원리

- **미러링**: 각 서버의 `~/.claude/projects/*.jsonl`(대화 로그, append-only)을
  rsync로 로컬 `~/.cchub/cache/<server>/`에 증분 복사. 원본은 항상 서버,
  로컬은 읽기 전용 사본이라 충돌이 없습니다.
- **인덱싱**: 미러된 JSONL에서 바이트 오프셋 기반으로 새로 늘어난 부분만
  파싱해 SQLite(FTS5)에 반영. 포맷이 낯선 줄은 조용히 건너뛰는 관대한
  파서라 Claude Code 버전이 바뀌어도 크래시하지 않습니다.
- **세션 매칭**: tmux pane 목록과 미러된 세션 파일을 프로젝트 경로(cwd)로
  매칭. 같은 cwd에 pane이 여러 개면 pane 생성 시각 ↔ 세션 시작 시각을
  가까운 순서로 페어링합니다.
- **모든 블로킹 작업**(ssh/rsync)은 TUI에서 백그라운드 스레드로 실행되고,
  서버 하나의 실패가 다른 서버나 UI를 막지 않습니다.

## 데이터 위치

`~/.cchub` (환경변수 `CCHUB_DIR`로 변경):

| 경로 | 내용 |
|---|---|
| `config.toml` | 설정 |
| `cache/<server>/projects/` | 서버별 미러링된 대화 로그 (영구 보존) |
| `index.db` | SQLite FTS5 세션 인덱스 (언제든 `reindex`로 재생성 가능) |
| `results/<server>/` | 수집된 실험 결과 |
| `results/briefing-*.md` | 생성된 브리핑 |
| `relay/` | 서버 간 전송 임시 경유지 (자동 정리) |

ssh ControlMaster 소켓은 AF_UNIX 경로 길이 제한을 피하기 위해 시스템 임시
디렉터리(`/tmp/cchub-cm-<uid>-<hash>/`)에 둡니다.

## 제약·알려진 한계

- tmux pane 중 `pane_current_command`가 `claude`/`node`인 것만 세션 후보로
  봅니다. 다른 명령이 도는 pane은 목록·전송 대상에서 제외됩니다.
- 같은 cwd에 claude pane을 여러 개 띄우면 시각 기반 페어링 휴리스틱을
  씁니다 — 거의 동시에 시작된 두 세션은 어긋날 수 있습니다.
  (`/proc` fd 기반 정확 매칭은 claude가 transcript fd를 상시 열어두지
  않아 불가능함을 실측으로 확인했습니다.)
- 전송 후 반영 확인은 화면 캡처 기반 best-effort라, 아주 긴 프롬프트가
  줄바꿈되면 "반영 미확인" 경고가 나올 수 있습니다 (전송 자체는 정상).
- 미러/배포 모두 `rsync --delete`를 쓰지 않습니다 — 이력은 영구 보존되고,
  skill 재배포 시 삭제된 파일은 서버에 남을 수 있습니다.

## 개발

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q          # 단위·TUI(headless pilot) 테스트
```

실물 통합 테스트는 `cchub-smoke`라는 ssh alias(자기 자신으로의 SSH)가 있을
때만 실행되고 없으면 자동 skip됩니다. 이 테스트는 실제 SSH·rsync·tmux 경로로
sync/list/search/TUI/skill 왕복까지 검증합니다.

## 라이선스

MIT — [LICENSE](LICENSE) 참고.

## 버전

- **0.4.0** — skill 통합 관리 (`cchub skills`, TUI `s`)
- **0.3.0** — 통합 이력(`h`)·검색(`/`), 결과 수집(`r`)·브리핑(`A`), 파일 중계(`push`), same-cwd 세션 페어링, 전송 전/후 검증
- **0.2.0** — TUI (트리·live 뷰·프롬프트 전송·CPU 바)
- **0.1.0** — 코어 CLI (sync/list/send/tail/search/reindex)
