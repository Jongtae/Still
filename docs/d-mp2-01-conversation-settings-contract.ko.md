# D-MP2-01 — 대화 우선 설정 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](d-mp2-01-conversation-settings-contract.en.md)을 따른다.

## Objective와 범위

Still은 안전한 설정 읽기와 검토된 lifecycle 변경의 기본 intent surface를 대화로 만들고, 수동 확인·관리를 위한 Chrome Settings형 로컬 보조 화면을 유지한다. 이 설계는 contract만 정의하며 UI, parser, credential, OAuth 흐름, provider connection, 외부 action을 구현하지 않는다.

승격 근거는 MP1의 mock-contract 개발 완료와 issue #230에서 기록한 소유자의 명시 선택이다. 이는 live provider 사용이나 운영 모드 evidence 주장이 아니다.

## 소유자 결과와 vocabulary

첫 구현은 의도적으로 작은 검토 vocabulary를 지원한다.

| Intent class | 소유자 표현 | 결과 |
| --- | --- | --- |
| Read | “무엇이 연결되어 있어?”, “Drive 상태 보여줘”, “무엇을 바꿀 수 있어?” | Redacted settings summary 또는 category read model. State mutation 없음. |
| Draft change | “Drive를 일시 정지해줘”, “A2A peer를 연결 해제해줘”, “이 검토된 연결을 다시 시작해줘” | 정확한 pending-change preview. Mutation 없음. |
| Confirm | `Confirm <short draft ID>` 또는 해당 ID를 담은 owner-bound UI action | 만료되지 않은 정확한 preview를 한 번만 적용한다. |
| Cancel/recover | `Cancel <short draft ID>`, “Drive를 어떻게 복구해?” | Draft를 취소하거나 이름 있는 recovery action을 돌려준다. Implicit retry 없음. |
| Sensitive handoff | “Drive 연결해줘”, “Calendar를 다시 인증해줘” | Scope를 설명하고 명시 확인 뒤 인증된 로컬 browser/web handoff를 연다. 대화는 secret이나 OAuth artifact를 받지 않는다. |

모호한 표현, model이 제안한 action, 단순 “예”, 검토되지 않은 setting name은 state를 바꾸지 않는다. Assistant는 검토된 intent를 요청하거나 수동 settings 경로를 돌려준다.

## Risk tier와 authority

| Tier | 예시 | 대화 동작 |
| --- | --- | --- |
| R0 read | Status, category navigation, audit/recovery 설명 | 즉시 redacted answer |
| R1 reversible local lifecycle | 현재 enable된 검토 capability pause | 정확한 preview와 명시 소유자 확인 |
| R2 connection-affecting lifecycle | Capability disconnect, 이미 승인된 capability resume | 정확한 preview와 명시 소유자 확인, reconnect/recovery 설명 |
| R3 sensitive 또는 consequential | 새 OAuth scope, credential 입력/교체, provider endpoint, 외부 write, 결제 | 대화 mutation 범위가 아니다. 별도 설계된 인증 로컬 handoff가 필요하거나 unsupported로 남는다. |

기존의 검토된 lifecycle transition만 R1/R2 후보가 된다. 대화 setting change는 새 scope 부여, capability 설치, arbitrary runtime 선택, capability registry 우회를 할 수 없다.

## Pending-change state machine

`SettingsOrchestrator`가 유일한 정책 소유자다. 인식한 intent와 현재 소유자 로컬 state에서 정확한 canonical change를 만든다.

```text
drafted -> awaiting-confirmation -> applied
             |        |                |
             |        +-> expired      +-> idempotent applied result
             +-> canceled
             +-> failed (stale state / lifecycle rejection / internal error)
```

각 pending record는 opaque ID, owner ID, originating channel, canonical target/action/before/after/effect/recovery field, digest, creation/expiry timestamp, terminal state를 가진다. 10분 뒤 만료한다. Confirm은 일치하는 opaque ID와 digest를 제공하고 같은 owner에 묶이며, 적용 직전에 현재 state를 다시 확인한다. Apply key는 owner와 digest에서 결정적으로 만들며 retry는 기록된 applied result를 돌려주고 transition을 두 번 실행하지 않는다. Cancel은 idempotent다. Stale 또는 foreign confirm은 fail closed하며 recovery-safe 설명만 보여준다.

## Interface와 channel parity

정책 interface는 다음과 같다.

```text
read(owner, category?) -> redacted settings read model
draft(owner, channel, intent) -> pending preview
confirm(owner, channel, draft_id, digest) -> applied/idempotent/fail-closed result
cancel(owner, draft_id) -> canceled/idempotent result
recovery(owner, subject) -> named next action
```

HTTP와 Telegram은 같은 operation을 호출하고 같은 semantic state, draft ID, expiry, effect, recovery, error class를 받는다. Rendering은 달라도 두 channel 모두 adapter나 `CapabilityRegistry.transition`을 직접 호출하지 않는다. 로컬 settings 보조 화면은 같은 settings read model을 읽고 수동 change를 같은 draft/confirm controller로 보낸다.

## Data, audit, export, threat model

Read model은 capability ID, 검토 state, 선언 scope, redacted health category, recovery label만 노출한다. Token, authorization code, password, raw path, message body, Personal Space 내용, pending confirmation material, 전체 approval record는 절대 노출하지 않는다.

Owner-local audit record는 제한된 timestamp, owner-scoped opaque draft reference, intent class, target category, before/after lifecycle state, terminal state, redacted error/recovery class를 보관한다. Evidence와 portable export는 confirmation ID, canonical payload, channel message body, secret, raw path, 민감 connection record를 제외한다. Restore는 안전한 historical state만 보존하며 pending change를 다시 활성화하지 않는다.

구현은 모호한 자연어, prompt/model confused-deputy 동작, channel/owner 혼동, replay, stale preview, 직접 HTTP/Telegram bypass, secret exfiltration, 두 번째 control plane을 만드는 수동 UI를 막아야 한다. 모든 mutation은 policy-owned, owner-bound, exact, short-lived, auditable, recoverable해야 한다.

## 로컬 보조 화면 정보 구조

인증된 로컬 보조 화면은 두 번째 runtime이 아닌 Chrome Settings형 navigation과 search model을 따른다.

1. **Assistant** — 선택한 assistant profile, 기본 동작, read-only 설명
2. **Connections** — 검토 capability status, scope summary, health category, connect/re-auth handoff, pause/resume/disconnect draft
3. **Data & privacy** — Personal Space boundary, source sharing policy, export/restore, redaction 설명
4. **Approvals & activity** — redacted pending/terminal setting activity와 recovery link
5. **Runtime & recovery** — local runtime health, diagnostics, backup/restore, 이름 있는 recovery action

Search는 label과 redacted description만 색인한다. 모든 수동 control은 같은 preview/confirm contract를 열며, chat transcript에서 secret을 받거나 Still 밖에서 state를 중복하지 않는다.

## I-MP2-01 진입 계약

I-MP2-01은 이 D-MP2-01 PR이 merge되고 issue가 close되며 영어 원본 문서/plan/ledger check가 통과하고, 새 goal-ready 구현 issue가 생긴 뒤에만 활성화할 수 있다. 범위는 R0 settings read, R1/R2 lifecycle draft·confirm·cancel·expiry·idempotency·audit·recovery, HTTP/Telegram parity, 로컬 보조 화면 read/navigation model이다.

I-MP2-01은 실제 OAuth/credential/provider activation, 새 scope, capability 설치, marketplace 동작, arbitrary setting, 외부 action, 모든 R3 mutation을 제외한다. Automated fixture는 vocabulary class별 동작, ambiguity, foreign/expired/replayed confirmation, stale state, duplicate submission, lifecycle rejection, channel parity, redaction, export/restore, manual-view/controller parity를 다뤄야 한다. 운영 모드 구성은 이후 별도로 승인된 goal에 남긴다.
