# cchub M4 — Claude skill 통합 관리 설계

날짜: 2026-07-20
상태: 설계 승인됨 (구두), 스펙 검토 대기

## 목적

여러 서버에 흩어진 Claude Code skill을 로컬에서 조회·수집·배포·삭제한다.
Claude API 호출 없음. M1~M3 코어(Remote fetch/push, TUI, CLI) 위에 얹는다.

## 요구사항 (사용자 확정)

1. **조회는 전체, 쓰기는 개인만**: 조회 대상은 개인 스킬(`~/.claude/skills/`)과
   프로젝트 스킬(각 저장소의 `.claude/skills/`). 배포/삭제는 **개인 스킬 디렉토리에만**
   수행한다 — 프로젝트 스킬은 git 관리 파일이므로 cchub가 절대 쓰지 않는다.
2. **배포 모델 둘 다**: 로컬 `~/.claude/skills/`를 원본 라이브러리로 하는
   pull/deploy + 서버 간 직접 복사 단축(copy, 내부는 로컬 경유).
3. **UI 범위**: CLI 완성 + TUI는 조회 전용 화면(`s` 키)만.

## 원격 스킬 발견 (스캔 방식 A — 승인됨)

- 개인 스킬: `~/.claude/skills/*/SKILL.md` 항상 스캔.
- 프로젝트 스킬: 현재 tmux에서 claude가 돌고 있는 pane들의 cwd에 대해
  `<cwd>/.claude/skills/*/SKILL.md` 스캔 (discover가 이미 pane cwd를 앎).
- 추가 경로: config `[servers.X] skill_paths = ["~/repo1", ...]` (선택) —
  tmux에 없는 프로젝트도 지정 가능.
- 홈 전체 find 스캔은 하지 않는다 (부하). 경로 인코딩 역산도 불가(비가역 — 실측).
- 스캔은 서버당 한 번의 POSIX sh 스크립트 호출로 수행하고, 각 SKILL.md의
  frontmatter에서 `description:` 첫 줄을 함께 추출한다 (head 기반, 관대한 파싱).

## 모듈: src/cchub/skills.py (textual 무관)

```python
@dataclass
class SkillInfo:
    server: str
    name: str
    scope: str        # "personal" | "project"
    path: str         # 원격 스킬 디렉토리 절대/틸드 경로
    description: str  # SKILL.md frontmatter description (없으면 "")

def valid_skill_name(name: str) -> bool   # ^[A-Za-z0-9_-]+$ — 경로 주입 차단
def scan_skills(remote, server, project_cwds, extra_paths) -> list[SkillInfo]
def local_skills(lib_dir: Path) -> list[SkillInfo]      # server="local"
def pull_skill(remote, info: SkillInfo, lib_dir) -> RunResult   # fetch → lib_dir/<name>/
def deploy_skill(remote, lib_dir, name) -> RunResult    # push → ~/.claude/skills/<name>/
def delete_skill(remote, name) -> RunResult             # rm -rf ~/.claude/skills/<name> (검증 후)
```

## CLI (`cchub skills ...`)

| 명령 | 동작 |
|---|---|
| `skills list [server]` | 서버×스킬 목록: 서버, 이름, scope, 경로, 설명. 로컬 라이브러리도 함께 표시 |
| `skills pull <srv> <name>` | 개인 스킬 우선, 없으면 프로젝트 스킬 중 유일 매칭 → 로컬 `~/.claude/skills/<name>/`. 로컬에 이미 있으면 `--force` 필요 |
| `skills deploy <name> <srv...>` | 로컬 라이브러리의 스킬을 서버들의 개인 스킬로 rsync (덮어쓰기, `--delete` 없음 — 원본에서 지운 파일이 서버에 남을 수 있음을 문서화) |
| `skills copy <src-srv> <name> <dst-srv...>` | pull→로컬 임시→deploy 단축. 임시 디렉토리는 try/finally 정리 (M3 push 패턴) |
| `skills delete <srv> <name>` | **개인 스킬만**. 대상 경로를 보여주고 `<name>` 재입력 확인 요구, `--yes`로 생략. 삭제 경로는 `~/.claude/skills/<name>`으로 고정 |

- 공통 에러 표면: 알 수 없는 서버/스킬은 stderr 한 줄 + rc 1 (기존 push/_resolve 관례).
- 스킬 라이브러리 위치: 로컬 `~/.claude/skills/` (환경변수 재정의 없음 — Claude가 읽는 실제 위치).

## TUI

- `s` 키 → `SkillsScreen(ModalScreen[None])`: 백그라운드 워커(`group="skills"`,
  thread=True, exit_on_error=False, is_cancelled 가드)로 전 서버 스캔 →
  DataTable(서버/이름/scope/경로/설명). 조회 전용, escape 닫기.
- 배포/삭제는 TUI에서 하지 않는다 (CLI 또는 로컬 Claude 세션에 자연어로).

## 안전장치

- `valid_skill_name` 통과 못 하면 pull/deploy/delete 전부 거부.
- delete의 원격 명령은 `rm -rf` 인자를 shlex 인용된 고정 경로
  (`$HOME/.claude/skills/<검증된 name>`)로만 구성. `~` 확장은 원격 셸에 맡기되
  name은 검증됐으므로 탈출 불가.
- 프로젝트 스킬 경로에는 어떤 쓰기 명령도 만들지 않는다 (deploy/delete 대상 아님).
- copy 임시 디렉토리는 `root/relay/` 아래 mkdtemp + try/finally rmtree.

## 에러 처리

- 스캔 실패(서버 다운): 해당 서버는 결과 없음 + list 출력에 `⚠` 한 줄. 다른 서버 계속.
- pull/deploy rsync 실패: rc≠0 → stderr 사유 + rc 1 (부분 성공 시 성공/실패 서버 나눠 표시).
- SKILL.md 없는 디렉토리는 스킬로 취급하지 않음.

## 테스트 전략

- skills.py: FakeRemote로 스캔 sh 스크립트 argv·출력 파싱 검증, fixture SKILL.md
  frontmatter 파싱, 이름 검증 경계.
- CLI: 기존 env fixture 확장 (FakeRemote fetch/push 기록 재사용).
- TUI: pilot로 `s` 화면 오픈/행 표시/escape.
- 실물 스모크: localhost의 실제 프로젝트 스킬(envector-msa 등) 조회 +
  스크래치 스킬 하나로 deploy→list→delete 왕복 (개인 스킬 디렉토리는 현재 비어
  있으므로 안전; 실제 스킬은 건드리지 않음).

## 범위 외 (YAGNI)

- 플러그인/마켓플레이스 스킬 관리 (읽기 전용 플러그인 캐시 — 대상 아님)
- 스킬 버전 관리/diff (필요해지면 M5)
- TUI에서의 배포/삭제
- 프로젝트 스킬 쓰기 (git 충돌 — 영구 제외)
