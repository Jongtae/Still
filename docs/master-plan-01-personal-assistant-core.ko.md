# Master Plan 1 — Personal Assistant Core

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](master-plan-01-personal-assistant-core.en.md)을 따른다.

## 상태와 목표

**상태: development complete (mock-contract validated).** D-01부터 I-06은 historical boundary 작업으로 남고, 이어서 R-01부터 R-05가 의존 순서대로 병합되어 자동 fixture로 정책 소유 개인 비서 흐름을 증명했다. 해당 근거는 [MP1 보완 계획](mp1-remediation.ko.md)에 기록한다. UX-05의 남은 수동 Telegram 관찰은 소유자 결정으로 해당 release gate에서 제외됐으며, 이 결정은 해당 관찰이 통과했다는 주장이 아니다. 외부 credential과 실제 connection 활성화는 소유자 제어 운영 모드 배포로 미룬다.

목표는 한 명의 사용자가 Still 개인 공간에서 자연어로 결과를 요청하고, Still이 개인 상태·승인·증거·복구를 소유한 채 검토된 연결만 조율하는 개인 비서 코어를 검증하는 것이다.

## 고정 기본값과 경계

- 기본 2nd brain은 외부 서비스가 아닌 Still 개인 공간이다.
- 첫 외부 지식 연결은 Google Drive 읽기 전용이다.
- 첫 A2A 연결은 특정 상용 runtime이 아닌 호환성 test peer다.
- 외부 Agent는 사용자가 명시 요청할 때만 호출한다.
- 첫 승인형 외부 행동은 Google Calendar의 새 일정 생성이다.
- Calendar 수정·삭제, 이메일 발송, 공개 MCP/A2A URL, arbitrary runtime 설치, marketplace는 MP1 범위 밖이다.

## 설계와 구현의 반복 규칙

각 Phase는 `D-*` 설계 iteration과 `I-*` 구현 iteration으로 나뉜다. `D-*`는 사용자 결과, 데이터 흐름, 위협 모델, API/저장 contract, fixture, automated acceptance를 확정한다. 정상 PR 병합과 정의된 자동 검증을 통과하면 해당 `I-*` 구현 iteration을 활성화할 수 있다.

각 iteration은 GitHub 이슈, `codex/` 브랜치, 작은 의도적 커밋, PR, 자동 검증을 갖는다. 완료 작업은 `TASKS.md`, `docs/roadmap.md`, `docs/issue-branch-ledger.jsonl`에 함께 기록한다.

향후 remediation 또는 운영 모드 iteration을 활성화할 때 issue는 [Goal 실행 계약](goal-execution-contract.ko.md)에 따라 goal-ready가 되어야 한다. 이 Master Plan은 범위와 완료 기준을 제공하고, `delivery-plan.yaml`은 실행 순서를 제공한다.

외부 연동과 Agent는 문서화된 contract, fixture, mock 기반 자동 검증으로 개발한다. OAuth client, provider credential, 외부 endpoint, 실제 활성화는 의도적으로 뒤로 미룬다. Master Plan 전체가 완료된 뒤 소유자가 Still을 운영 모드로 배포하면서 한 번 설정한다. Mock 성공은 MP1 개발 작업을 완료하지만, 해당 외부 서비스가 이미 운영 중이라는 주장은 하지 않는다.

## Phase sequence

| Phase | 설계 iteration | 구현 결과 |
| --- | --- | --- |
| 1. Personal Space | D-01 개인 기억·원문·작업 증거의 경계 | I-01 기본 개인 공간과 단일 비서 UX |
| 2. Capability lifecycle | D-02 MCP/A2A/runtime registry와 on/off 상태 | I-02 검토된 카탈로그, enable/pause/disconnect, audit |
| 3. Drive knowledge | D-03 Google OAuth·읽기·공유 경계 | I-03 Google Drive 읽기 전용 connector |
| 4. A2A delegation | D-04 Agent Card·task·artifact·취소 contract | I-04 호환성 test peer와 명시 위임 |
| 5. Calendar action | D-05 incremental OAuth·미리보기·단발 승인 | I-05 Google Calendar 새 일정 생성 |
| 6. Core release | D-06 통합 ReAct 정책·fallback·acceptance | I-06 개인 비서 코어 release |

### Phase 1 — Personal Space

D-01은 명시 저장 기억, 안전한 작업 요약, 원본 source of truth, 작업 증거의 경계를 결정한다. I-01은 연결 선택을 강요하지 않는 단일 비서 UX와 기억·근거·삭제·내보내기 흐름을 제공한다.

### Phase 2 — Capability lifecycle

D-02는 capability descriptor, 상태(`available`, `connected-disabled`, `enabled`, `paused`, `auth-required`, `error`, `disconnected`), grant, secret 분리, audit contract를 결정한다. I-02는 검토된 목록에서 연결을 enable/pause/disconnect하고 실행 근거를 보는 관리 흐름을 제공한다.

### Phase 3 — Drive knowledge

D-03은 OAuth Authorization Code + PKCE, 최소 읽기 scope, 비밀값 보관, 검색 후 선택 읽기, 외부 모델 공유 경계를 결정한다. I-03은 전체 동기화 없이 Google Drive 검색·읽기·출처·health check·연결 해제를 제공한다.

### Phase 4 — A2A delegation

D-04는 test peer의 Agent Card, 명시 위임, task 상태, polling/SSE, 취소, timeout, artifact 검증을 결정한다. I-04는 A2A 결과를 신뢰하지 않는 evidence로 정규화하고, Still 비밀값·로컬 경로·전체 기억을 제공하지 않는 adapter를 제공한다.

### Phase 5 — Calendar action

D-05는 Drive와 분리된 incremental OAuth write scope, 기본 primary calendar, event preview, owner-bound exact approval, payload hash, idempotency를 결정한다. I-05는 새 이벤트 생성 하나만 제공하며 수정·삭제·참석자 초대·자동 충돌 해결은 제공하지 않는다.

### Phase 6 — Core release

D-06은 개인 공간, Drive 근거, 명시 A2A 검토, Calendar 초안을 조율하는 ReAct 정책과 연결이 없을 때의 fallback을 결정한다. I-06은 새 설치부터 pause/disconnect와 export/restore까지의 end-to-end release acceptance를 실행한다.

## 완료 증거

MP1 개발은 보완 순서가 정책 소유 흐름으로 개인 공간, Drive 근거, 명시 A2A 위임, Calendar 생성 승인, pause/disconnect invocation gate, export/restore를 증명할 때만 완료된다. 이후 소유자는 별도의 운영 모드 배포에서 실제 OAuth client, credential, endpoint, connection을 설정한다. 그 배포는 자체 증거를 남기며 MP1 개발 완료 여부를 소급하여 바꾸지 않는다.

MP1 완료 뒤의 다음 선택은 [Master Plan 2](master-plan-02-proposal.ko.md) 절차를 따른다.
