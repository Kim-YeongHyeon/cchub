# cchub M5 — 상세 패널 2분할 + 클립보드 내보내기 설계

날짜: 2026-07-20
상태: 설계 승인됨 (구두)

## 목적

TUI에서 (1) 지금 보고 있는 세션 내용을 로컬 Claude Code에 즉시 넘길 수 있게
클립보드로 복사하고, (2) 세션 두 개를 한 화면에서 나란히 볼 수 있게 한다.

## 요구사항 (사용자 확정)

1. **`x` = 클립보드 복사만.** 파일 저장은 하지 않는다(사용자 지적: transcript는
   이미 로컬 캐시에 있어 중복). 가치는 (a) live 캡처는 로컬에 파일로 존재하지
   않음, (b) 보고 있는 화면을 그대로 붙여넣는 즉시성.
2. **`|` = 상세 패널 좌/우 2분할 토글.** 트리 선택은 활성 패널에 할당,
   활성 패널 전환 키, f/t/x/프롬프트 전송은 활성 패널 대상, 패널별 독립 팔로우.
3. 로컬 서버 등록은 기능이 아니라 가이드(README)로 해결 — config에
   localhost alias 서버 추가.

## 설계

### 패널 상태 모델 (기존 단일 상태의 일반화)

기존 `selected/transcript_mode/follow_on` 단일 상태를 패널 단위로 일반화:

```python
@dataclass
class PaneState:
    session: LiveSession | None = None
    transcript_mode: bool = False
    follow_on: bool = False
    text: str = ""          # 마지막으로 표시한 내용 (x 복사용)
```

- `CchubApp.panes: list[PaneState]` (길이 1 또는 2), `active: int` (활성 인덱스)
- 기존 `self.selected`는 활성 패널 세션을 가리키는 property로 유지
  (send/reconcile 등 기존 코드·테스트 호환)
- `_reconcile_selection`은 모든 패널의 세션을 재조정 (pane 소실 시 해당 패널만 None)
- 팔로우 타이머는 기존 2초 단일 타이머가 패널들을 순회 (`follow_on and not
  transcript_mode`인 패널만 갱신)
- `refresh_detail` 워커는 패널 인덱스를 받아 해당 패널만 갱신
  (`group=f"detail-{i}"`로 패널별 exclusive)

### 키

| 키 | 동작 |
|---|---|
| `x` | 활성 패널의 현재 내용(text)을 클립보드로 복사 (`App.copy_to_clipboard`, OSC52). 내용 없으면 warning notify. 성공 시 "복사됨 (N자)" notify |
| `\|` (pipe) | 상세 패널 2분할 토글. 분할 해제 시 두 번째 패널 상태는 버리고 첫 패널 유지 |
| `o` | 활성 패널 전환 (분할 상태에서만 의미). **주의: 설계 논의에선 Tab이었으나 Textual이 Tab을 포커스 이동에 예약하므로 `o`로 확정** — README/키맵에 명시 |
| Enter(트리) | 선택 세션을 활성 패널에 할당 |
| `f`/`t` | 활성 패널의 팔로우/transcript 토글 |
| 입력창 Enter | 활성 패널 세션으로 전송 |

### 레이아웃

- 단일: 기존과 동일 (#detail-0 RichLog + 입력창)
- 분할: Horizontal 안에 #detail-0, #detail-1 나란히. 활성 패널은 테두리 색 강조
  (`.active-pane` CSS 클래스)
- 분할/해제는 두 번째 RichLog를 mount/remove (Textual 동적 마운트)

### 클립보드

- `self.copy_to_clipboard(text)` — OSC52라 SSH 세션·tmux passthrough 지원
  터미널에서 동작. 미지원 터미널이면 조용히 무시될 수 있음을 notify 문구에
  담지 않는다 (텍스트는 PaneState.text로 항상 보존되므로 재시도 가능).

## 에러 처리

- 분할 상태에서 두 패널이 같은 세션을 가리켜도 허용 (각자 갱신).
- 활성 패널에 세션이 없을 때 f/t/x/전송 → 기존 "세션을 먼저 선택하세요" 계열 notify.
- 워커 규약은 기존과 동일 (thread, exit_on_error=False, is_cancelled, call_from_thread).

## 테스트 전략

- PaneState 일반화가 기존 테스트(선택·전송·팔로우·reconcile)를 깨지 않는지 =
  기존 스위트가 회귀 게이트.
- 신규: x 복사(모니키패치로 copy_to_clipboard 캡처), 분할 토글(위젯 수),
  o 전환(active 인덱스·CSS 클래스), 분할 상태에서 선택→활성 패널 할당,
  패널별 독립 transcript 모드, 분할 해제 시 상태 정리.
- 실물 스모크: 분할 + 실제 세션 2개 각각 표시.

## 범위 외 (YAGNI)

- 3분할 이상, 상하 분할
- 파일 내보내기 (사용자 결정으로 제외)
- 패널별 개별 프롬프트 입력창 (입력창은 하나, 활성 패널로 라우팅)
