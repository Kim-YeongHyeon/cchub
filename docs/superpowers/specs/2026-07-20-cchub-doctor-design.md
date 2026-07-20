# cchub doctor — 첫 실행 UX 진단 명령 설계

날짜: 2026-07-20
상태: 설계 승인됨

## 배경

첫 사용자가 sync 실패 시 보는 것은 원시 ssh/rsync stderr뿐이다
(CLI `srv1: 실패 — ssh: connect to host ...`, TUI 트리 `srv1 ⚠ ...`).
무엇을 점검해야 하는지 안내가 없어 "ssh: connect to host"만 보고 막힌
실사용 사례가 있었다. 능동 진단 명령 `cchub doctor`를 추가하고,
실패 메시지가 doctor를 가리키게 한다.

## 요구사항 (사용자 확정)

1. **`cchub doctor` 신설** — 서버별로 실제 점검을 수행해 ✓/✗/⚠ 체크리스트 출력.
2. **점검은 기본 4종만**: ssh 접속, 원격 rsync, `{claude_dir}/projects` 존재,
   tmux 서버. (로컬 환경 점검·ControlPath 길이 점검·claude 세션 감지는 범위 외.)
3. **실패 연동은 CLI+TUI 모두**: sync 실패 메시지에 doctor 안내 추가.
4. tmux 체크 실패는 **경고(⚠)** — sync·조회는 tmux 없이 동작하고
   전송·상태 감지만 불가하므로 실패(✗)가 아니다.

## 설계

### 1. 진단 로직 — 새 모듈 `src/cchub/doctor.py`

`Remote` 주입으로 테스트 가능한 순수 로직 (FakeRemote 재사용):

```python
@dataclass
class CheckResult:
    name: str      # "ssh 접속" 등
    status: str    # "ok" | "fail" | "warn" | "skip"
    detail: str = ""   # 실패 시 원시 에러 요약 (1줄 절단)
    hint: str = ""     # 원인별 맞춤 안내

def diagnose_server(remote: Remote, name: str, host: str,
                    claude_dir: str) -> list[CheckResult]
```

서버별 4개 체크, 순차 실행. **ssh 실패 시 나머지 3개는 status="skip"**
(접속 불가면 나머지는 판정 불가).

| # | 체크 | 방법 | 실패 시 status/힌트 |
|---|---|---|---|
| 1 | ssh 접속 | `remote.run(["true"])` rc==0 | fail. stderr 패턴 분류(아래) |
| 2 | 원격 rsync | `remote.run(["rsync", "--version"])` | fail. "서버에 rsync 설치 필요" |
| 3 | projects 존재 | 원격 `test -d {claude_dir}/projects` — 틸드 확장 위해 기존 skills.py의 `sh -c` 패턴 | fail. "이 서버에서 Claude Code 실행 이력이 없거나 claude_dir 설정 불일치" |
| 4 | tmux 서버 | `remote.run(["tmux", "list-sessions"])` | **warn**. "미설치"(`command not found` 계열)와 "서버 안 뜸"(`no server running`) 구분 문구 |

ssh stderr → 힌트 분류 (`_classify_ssh_error(stderr) -> str`):

- `Permission denied` → 키 기반(비밀번호 없는) 로그인 필요: `ssh-copy-id <host>`
- `Connection refused` / `timed out` → 호스트·포트 점검 (사내 서버는 22가
  아닐 수 있음 — `~/.ssh/config`에 `Port` 지정한 alias 권장)
- `Could not resolve hostname` → config의 host 문자열 / `~/.ssh/config` alias 확인
- 그 외 → 일반 안내: `ssh -o BatchMode=yes <host> true`로 직접 확인

### 2. CLI — `cmd_doctor`

```
$ cchub doctor
[srv1] host=sudal
  ✓ ssh 접속 (BatchMode)
  ✓ 원격 rsync
  ✓ ~/.claude/projects 존재
  ✓ tmux 서버 실행 중
[srv2] host=cigar
  ✗ ssh 접속 — Connection refused
    → 점검: 호스트/포트(사내 서버는 22가 아닐 수 있음), 키 기반 로그인(ssh-copy-id)
  - 원격 rsync (ssh 실패로 건너뜀)
  ...
```

- exit code: **✗(fail)가 하나라도 있으면 1**, ⚠(warn)만 있으면 0.
- config 없음 → 기존 ConfigError 경로 위임 (`cchub init으로 생성` 안내가 이미 있음).
- 서버 0개 → `config.toml에 [servers.*] 항목을 추가하세요` 출력 후 1.

### 3. 실패 연동

- **CLI `cmd_sync`**: 실패가 하나라도 있으면 마지막에 stderr 1줄:
  `→ cchub doctor 로 서버별 진단을 실행해 보세요`
- **TUI**: 트리 `⚠` 라벨은 유지(공간 제약). `apply_snapshots`에서 서버 에러가
  **새로 생기거나 내용이 바뀐 경우에만** notify 1회:
  `"{server} 동기화 실패 — cchub doctor로 진단해 보세요"`.
  직전 스냅샷의 에러 dict와 비교해 30초 자동 갱신마다 반복 알림 방지.
  에러가 사라지면 비교 상태도 정리(재발 시 다시 1회 알림).

### 4. 테스트

FakeRemote 기반:

- 4체크 전부 성공 / 각각 실패 조합, ssh 실패 시 나머지 skip
- `_classify_ssh_error` 패턴→힌트 매핑 (4분기)
- tmux 실패가 warn이고 exit code에 영향 없음
- `cmd_doctor` 출력 형식·exit code (fail=1, warn만=0, 서버 0개=1)
- `cmd_sync` 실패 시 doctor 안내 줄 (성공 시엔 없음)
- TUI: 동일 에러 반복 갱신 시 notify 1회, 에러 변경 시 재알림

### 5. 문서

README 명령 표에 `doctor` 추가, 첫 실행 절차(init → 서버 추가 → doctor → sync) 언급.

## 범위 외 (명시)

- 로컬 ssh/rsync 설치 점검, ControlPath 길이 사전 점검, 원격 claude 세션 감지
- 자동 수리(ssh-copy-id 실행 등) — 안내만 한다
