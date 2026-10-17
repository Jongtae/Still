# MP1 보완 계획 — 제품 통합 요구사항

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-remediation.en.md)을 따른다.

## 상태

**상태: complete remediation (mock-contract validated).** 기존 D-01부터 I-06 작업은 경계 contract와 분리된 mock adapter를 만들었다. 이어서 R-01부터 R-05가 각각의 자동 acceptance와 함께 병합되어 정책 소유 개인 비서 제품 흐름을 증명했다. 이는 개발 근거일 뿐이며, 운영 모드 배포는 별도다.

## 누락 분석

| 요구사항 | 기존 증거 | 빠진 제품 동작 |
| --- | --- | --- |
| ReAct assistant | 직접 adapter fixture | enable된 capability를 선택하고 승인·evidence·fallback/recovery를 적용하는 정책 소유 orchestrator |
| Capability lifecycle | Registry 상태 전이 | 실제 adapter 사용에서 검증되는 invocation gate, health contract, secret revoke, export 제외 |
| Drive | PKCE/read adapter mock | 선택 발췌문 승인 경계와 runtime wiring, 만료/re-auth 실패 복구 |
| A2A | 직접 compatibility-peer adapter | Agent Card 거절 matrix, polling/SSE, timeout/cancellation race, 최소 맥락 assertion, 소유자 표시 evidence |
| Calendar | 직접 create-only adapter | scope/error matrix, 정확한 payload 변경 거절, 만료, 다른 owner, timeout/malformed-response 복구, assistant wiring |
| Core release | 직접 fixture 하나 | 실제 orchestration 경로로 새 설치부터 pause/disconnect, portable export/restore까지의 소유자 여정 |

## 순서가 있는 보완 iteration

| ID | 의존성 | 결과 | 자동 acceptance |
| --- | --- | --- |
| MP1-R-01 ReAct orchestration | I-06 | 요청을 분류하고 Personal Space를 읽고 enable된 검토 capability만 선택하며, 승인형 Calendar draft·redacted evidence·결정적인 fallback/recovery를 만드는 Still 소유 `PersonalAssistantOrchestrator`를 추가한다. | 로컬 답변, disabled/paused/disconnected capability, Drive evidence, 명시 A2A만, 승인 전 Calendar draft, 알 수 없는 요청 matrix. 어떤 adapter 호출도 정책을 우회하지 않는다. |
| MP1-R-02 A2A contract completion | R-01 | Orchestrator를 통해 로컬 compatibility peer를 연결하고 Card, progress, timeout, cancellation, artifact, evidence 동작을 완성한다. | unsupported/malformed Card, polling/SSE event, timeout, owner/peer cancellation, duplicate terminal event, correlation mismatch, type/size/schema 거절, 최소 맥락 spy, redacted export. |
| MP1-R-03 Calendar contract completion | R-02 | Orchestrator를 통해 Calendar draft와 정확한 owner approval을 연결하고 failure/recovery matrix를 완성한다. | scope 없음/거절, 만료/다른 owner 승인, 변경된 canonical payload, 중복 submit, timeout/malformed response, 일치 승인 뒤 POST 한 번, secret 없는 evidence/export. |
| MP1-R-04 Drive contract completion | R-03 | Drive 검색 및 선택 읽기 evidence를 문서 승인 경계에 연결하고 auth recovery를 완성한다. | 만료 token/re-auth, scope 거절, 선택 발췌문만, 승인 전 engine 공유 없음, disconnect/reconnect, redacted health/evidence/export. |
| MP1-R-05 Core release acceptance | R-04 | Orchestrator와 portable-state 경계를 통해 새 owner-local end-to-end 흐름을 실행한다. | 새 설치, Personal Space, Drive source, 명시 A2A, Calendar preview/approval, enabled→paused→disconnected invocation gate, export/restore의 secret 제외/evidence 유지, fallback마다 recovery action 하나. |

## 변경할 수 없는 contract

- Adapter method는 내부 경계다. Assistant와 HTTP/Telegram surface는 orchestrator와 lifecycle gate를 통해서만 capability를 호출한다.
- 모든 상태 전이, capability 호출, 승인, 결과, fallback, recovery는 secret을 redaction한 owner-local evidence를 남긴다.
- Mock peer는 선언되지 않은 요청을 거절하고 inspect 가능한 request log를 남겨 test가 최소 맥락과 숨은 외부 호출 없음을 증명한다.
- 외부 provider 설정은 운영 모드 배포로 계속 미룬다. Mock acceptance는 개발 증거이며 live-service 주장이 아니다.
- 기존 MP1 비목표는 그대로다. 공개 URL, marketplace, arbitrary runtime 설치, Calendar 수정/삭제, email, 자동 consequential action은 없다.

## 완료 규칙

R-01부터 R-05가 순서대로 병합되고, 선언된 자동 acceptance가 모두 통과하며, tracker/ledger 기록이 갱신되고, 문서에 모순된 완료 주장이 없을 때만 MP1은 development complete다. 운영 모드 배포는 별도의 이후 설정 단계다.
