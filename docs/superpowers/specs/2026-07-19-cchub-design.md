# cchub — 멀티 서버 Claude Code 세션 통합 관리 도구 설계

날짜: 2026-07-19
상태: 설계 승인 대기

## 목적

여러 서버의 tmux 안에서 돌아가는 Claude Code 세션들을 로컬 머신 한 곳에서
조회·제어·기록하는 도구. Claude API(LLM 호출)를 일절 사용하지 않는다.

핵심 기능:

1. **결과 수집·종합** — 각 서버의 실험 결과를 로컬로 수집하고, 로컬 Claude Code
   세션에 넘길 종합 브리핑을 생성한다 (최종 artifact 작성은 로컬 Claude가 수행).
2. **원격 프롬프트 주입** — 로컬에서 특정 서버의 특정 세션에 프롬프트를 보내고
   결과를 확인한다.
3. **작업 이력 통합 조회** — 모든 서버에서 했던 작업을 검색·열람한다.
4. **서버 리소스 모니터링** — iTerm2 스타일의 CPU/메모리 실시간 바 (토글 가능).
5. **서버 간 파일 중계** — 서버 1의 결과를 서버 2 세션이 참조할 수 있게 복사
   (로컬 경유, 가끔 필요).

## 전제 및 제약

- 로컬 머신 1대, 서버 2~5대. 네트워크는 로컬→서버 SSH 단방향만 보장.
- 서버의 Claude Code 세션은 모두 tmux 안에서 인터랙티브로 실행된다.
- 서버에 아무것도 설치하지 않는다 (agentless). Claude Code의 로컬 데이터
  (`~/.claude/projects/*.jsonl`)와 tmux, `/proc`만 이용한다.
- transcript JSONL 포맷은 비공식이며 버전 간 변경될 수 있다 → 관대한 파서.

## 아키텍처 (Approach A: Agentless SSH-pull)

```
┌─ 로컬 머신 ──────────────────────────────────────────┐
│  cchub TUI (Textual)      cchub CLI (동일 코어 사용)   │
│        │                        │                    │
│  ┌─────┴────────────────────────┴─────┐              │
│  │ 코어 라이브러리                       │              │
│  │  · ServerPool: SSH ControlMaster    │              │
│  │  · SessionIndex: SQLite (검색/이력)   │              │
│  │  · SyncEngine: rsync 증분 미러링      │              │
│  │  · TmuxBridge: send-keys/capture     │              │
│  │  · StatsPoller: CPU/mem 폴링         │              │
│  └───────────────┬────────────────────┘              │
│  ~/.cchub/                                           │
│    config.toml   (서버 목록, 결과 경로 패턴)             │
│    cache/<server>/projects/...  (transcript 미러)     │
│    results/<server>/<project>/ (수집된 실험 결과)       │
│    index.db      (세션·이력 인덱스, FTS 검색)           │
└────────┬──────────────┬──────────────┬───────────────┘
     ssh(멀티플렉스)     ssh            ssh
      서버 1~N: tmux 내 claude 세션, ~/.claude/
```

- **기술 스택**: Python 3.13, Textual(TUI), SQLite(FTS5), OpenSSH
  ControlMaster + rsync (외부 프로세스 호출). 서버 쪽 요구사항: sshd, tmux,
  rsync, /proc — 전부 기본 존재.
- TUI와 CLI는 동일한 코어 라이브러리를 사용한다. CLI(`cchub send/list/tail/...`)가
  있으면 로컬 Claude Code 세션이 이를 호출해 "서버 1 세션 3에서 ~~ 실험 돌려줘"
  같은 자연어 라우팅이 가능해진다.
- 향후 실시간성이 더 필요하면 hooks 기반 푸시(Approach B)를 추가할 수 있는
  구조로 코어 이벤트 계층을 분리해 둔다.

## 세션 동기화

1. **원본은 항상 서버.** 서버의 `~/.claude/projects/<프로젝트>/<세션ID>.jsonl`이
   진실의 원천, 로컬은 읽기 전용 미러. 충돌 없음.
2. SyncEngine이 주기적으로(기본 30초, 수동 새로고침 키 제공) `rsync -az`로
   각 서버의 `~/.claude/projects/`를 `~/.cchub/cache/<server>/`에 증분 동기화.
   JSONL은 append-only라 전송량이 매우 작다.
3. 동기화 직후 인덱서가 새로 추가된 줄만 파싱해 SQLite에 반영:
   세션ID, 프로젝트 경로, 첫 사용자 프롬프트(제목 대용), 마지막 활동 시각,
   마지막 메시지 역할, 사용자 프롬프트/어시스턴트 텍스트(FTS용), 툴 사용 카운트.
4. **세션 상태 추정**: 마지막 메시지가 assistant이고 tmux pane이 유휴 → 입력
   대기(◌), transcript가 최근 갱신 중 → 작업 중(●), 장시간 무활동 → 유휴(▶).
5. **"서버1 세션3" 매핑**: 사용자에게는 서버별 번호 + 프로젝트명으로 표시.
   내부 식별은 (서버, tmux pane ID, 세션 UUID) 튜플로 고정 추적.
   pane→세션 UUID 연결은 pane 프로세스 트리에서 claude 프로세스를 찾고
   `/proc/<pid>/fd`가 가리키는 transcript 경로(불가 시 pane cwd + 최신 활성
   jsonl)로 매칭한다.
6. **관대한 JSONL 파서**: 아는 필드만 추출, 모르는 줄은 무시하고 카운트만 기록.
   인덱스는 언제든 미러에서 전체 재생성 가능 (`cchub reindex`).

## TUI 레이아웃

```
┌──────────────────────────────────────────────────────────────┐
│ srv1 ▂▄▆▂ 34% 12G/64G   srv2 ▆▇▇▆ 78% 41G/128G   srv3 ▂▂ 8% │ ← CPU바 (c 토글)
├───────────────┬──────────────────────────────────────────────┤
│ 서버/세션 트리   │  선택된 세션 뷰                                │
│ ▼ srv1        │  [live] tmux capture-pane 실시간 출력          │
│   1 envector● │   (2초 갱신, f키 팔로우 모드)                   │
│   2 hem     ◌ │  ──────────────────────────────              │
│   3 es2-msa ▶ │  > 프롬프트 입력창 (Enter로 전송)               │
│ ▼ srv2 ...    │                                              │
├───────────────┴──────────────────────────────────────────────┤
│ [s]end [h]istory [r]esults [y]sync [c]pu [/]검색 [q]uit       │
└──────────────────────────────────────────────────────────────┘
● 작업중  ◌ 입력대기  ▶ 유휴
```

## 기능별 설계

### 원격 프롬프트 주입 / 결과 확인

- 전송: `tmux send-keys -t <pane> -l '<프롬프트>'` 후 별도 `send-keys Enter`.
  `-l`(literal)로 특수문자 오해석 방지.
- 전송 전 검증: pane 존재 + pane 프로세스가 claude + capture-pane 마지막 부분에
  입력 프롬프트 UI 존재 확인. 작업 중(●) 세션에는 확인 다이얼로그
  (Claude Code가 입력을 큐잉하므로 차단하지 않고 경고만).
- 전송 후 capture-pane으로 입력 반영 1회 확인.
- 결과 확인 2계층: **live 뷰**(capture-pane 원본 그대로, 즉시성)와
  **transcript 뷰**(동기화된 JSONL에서 구조화된 메시지/툴 요약, 스크롤·검색용).
- CLI 동치: `cchub list`, `cchub send <srv> <n> "..."`, `cchub tail <srv> <n>`.

### 결과 수집 → 로컬 종합

- config에 서버·프로젝트별 결과 경로 glob 패턴 등록.
- 수집: rsync로 `~/.cchub/results/<server>/<project>/`에 미러 (스냅샷 폴더
  없음 — 실험 산출물이 대개 타임스탬프 파일명을 가지므로 미러로 충분).
- 종합(`A` 키): 수집 폴더 경로 목록 + 관련 세션들의 최근 작업 요약(transcript
  추출)을 담은 브리핑 파일 `~/.cchub/results/briefing-<날짜>.md` 생성 후,
  로컬 Claude Code 세션에 붙여넣을 프롬프트를 화면에 출력. 로컬 claude를
  자동 spawn하지 않는다 — 어느 세션에서 종합할지는 사용자가 통제.

### 작업 이력

- `h`: 전체 서버 통합 타임라인 (시각, 서버, 프로젝트, 제목, 툴 활동 수).
  서버/프로젝트/기간 필터.
- `/`: SQLite FTS5 전문 검색 → 매칭 세션 선택 시 transcript 뷰 진입.
- 서버 쪽 30일 자동 정리와 무관하게 로컬 미러에 영구 보존.

### CPU/메모리 바

- StatsPoller가 SSH 마스터 연결로 2초마다 `cat /proc/stat /proc/meminfo`
  실행, diff로 사용률 계산. 서버당 스파크라인(최근 30샘플) + 현재 % + 메모리.
- `c` 키로 표시와 폴링을 함께 on/off (off 시 SSH 트래픽 중단).
- 단절 서버는 회색 표시 + 자동 재연결.

### 서버 간 파일 중계

- `cchub push <src> <dst>` (src/dst는 `로컬경로` 또는 `srv:경로`).
  서버→서버는 로컬 경유(pull 후 push). TUI에서는 결과 브라우저에서 파일
  선택 → 대상 서버 지정.

## 설정

```toml
# ~/.cchub/config.toml
[general]
sync_interval = 30        # transcript 동기화 주기(초)
stats_interval = 2        # CPU 폴링 주기(초)

[servers.srv1]
host = "user@10.0.0.11"   # ssh 접속 문자열 (~/.ssh/config alias 가능)
results = ["~/envector/results/**", "~/bench/*.json"]
```

첫 실행 시 config 부재면 대화형 생성.

## 에러 처리

- **SSH 단절**: 서버별 독립 동작. 단절 서버는 회색 표시, 지수 백오프
  (5s→최대 60s) 재연결. 캐시 데이터는 계속 조회 가능(마지막 동기화 시각 표시).
- **전송 실패**: 사전 검증 실패 시 전송하지 않고 사유 표시.
- **파싱 실패**: 해당 줄 스킵 + 카운트 로그. `cchub reindex`로 전체 재생성.
- **rsync 실패**: 다음 주기 재시도, 결과 수집 실패는 실패 패턴 명시.

## 테스트 전략

- 코어(JSONL 파서, 상태 추정, 인덱서, config): 실제 transcript 샘플 fixture로
  단위 테스트 (로컬 `~/.claude/projects/`에 실물 데이터 풍부).
- SSH/tmux 계층: 인터페이스 분리 후 fake로 단위 테스트, 통합 테스트는
  localhost를 서버 삼아 실행.
- TUI: Textual headless pilot으로 핵심 화면 전환 검증.

## 구현 마일스톤

1. **M1 — 코어+CLI**: config, SSH pool, rsync 동기화, JSONL 인덱싱,
   `cchub list/send/tail`. 이 시점부터 로컬 Claude 자연어 라우팅 가능.
2. **M2 — TUI**: 트리 + live 뷰 + 프롬프트 입력 + CPU 바.
3. **M3 — 이력/검색 + 결과 수집·종합·중계**.

## 범위 외 (YAGNI)

- Claude API 호출, 자동 요약 생성 (모든 요약은 기존 transcript에서 추출)
- hooks 기반 실시간 푸시 (구조만 열어둠)
- 웹 대시보드
- 서버→서버 직접 전송 최적화 (로컬 경유로 충분)
- 다중 사용자/권한 관리 (개인 도구)
