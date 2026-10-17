# MP1 D-04 — Compatibility A2A Delegation Contract

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d04-a2a-delegation-contract.en.md)을 따른다.

## 범위

I-04는 소유자가 명시적으로 요청한 하나의 제한된 위임을 검토된 로컬 compatibility A2A test peer에 보낸다. Still은 사용자 요청, Personal Space, 승인 이력, task 상태, artifact, 취소, evidence의 소유자다. 이 contract는 공개 A2A URL이나 상용 runtime 연결을 추가하지 않는다.

## 데이터 흐름과 위협 모델

1. 소유자가 명시적으로 위임을 요청하면 Still은 로컬 delegation record를 `requested` 상태로 만든다.
2. Still은 peer의 고정된 bundled Agent Card를 읽고, task 생성 전에 protocol version, 선언된 skill, input schema, artifact schema를 검증한다.
3. Still은 승인된 task prompt, opaque delegation ID, 선언된 최소 input만 peer에 보낸다. Personal Space 전체, 소유자 credential, OAuth token, connection secret, host path, filesystem grant, approval record는 보내지 않는다.
4. Peer는 결정적인 polling 또는 server-sent event로 task 상태를 반환한다. Still은 이를 신뢰하지 않는 evidence로 정규화하고 redacted lifecycle metadata만 기록한다.
5. 모든 artifact는 소유자에게 보이기 전에 Agent Card schema와 size/type 제한에 맞는지 검증한다. 유효하지 않은 artifact는 완료 주장이 아니라 `failed` evidence를 만든다.

로컬 peer라도 신뢰하지 않는다. Still은 알 수 없는 capability, malformed card, cross-owner ID, 요청하지 않은 artifact, 중복 terminal transition, 없는 correlation ID, timeout 또는 cancellation race를 거절한다.

## API와 저장 contract

`A2ADelegation`은 `discover()`, `delegate(owner_request)`, `status(delegation_id)`, `cancel(delegation_id)`, `artifact(delegation_id, artifact_id)`를 제공한다. 유효 상태는 `requested`, `working`, `input-required`, `completed`, `failed`, `canceled`, `timed-out`이며 terminal state는 다시 전이하지 않는다.

Delegation record는 `id`, 요청한 peer/skill, state, timestamp, redacted error class, artifact metadata, correlation ID를 소유자 로컬 저장소에 남긴다. Prompt와 artifact는 로컬 evidence로 남는다. Secret과 raw peer transport payload는 browser response, export, log, 외부 engine에서 제외한다.

`delegate`는 명시적인 소유자 요청과 catalogued peer skill만 허용한다. `cancel`은 idempotent이며 non-terminal일 때만 취소를 전달한다. `artifact`는 일치하는 task가 완료된 뒤에만 검증되고 제한된 artifact content를 돌려준다.

## Fixture와 자동 acceptance

I-04 mock peer suite는 valid Agent Card와 completed artifact, malformed/unsupported Card, 명시 요청 requirement, 최소 맥락 assertion, polling/SSE progress, timeout, owner cancellation, peer cancellation, 중복 terminal event, artifact schema/type/size 거절, correlation mismatch, redacted evidence/export 동작을 다뤄야 한다.

자동 acceptance는 local mock peer와 fixture만으로 Still adapter가 이 contract를 따르는지 증명한다. MP1 개발에는 live A2A endpoint, credential, provider account, 소유자 수동 test가 필요 없다. 실제 endpoint 설정은 MP1 뒤 소유자 제어 운영 모드 배포로 미룬다.

## 비목표

I-04는 자동 위임, 공개 peer discovery, arbitrary Agent Card URL, agent 설치, marketplace 검색, 결제, peer 측 tool grant, consequential action을 peer로 보내는 일을 범위에 넣지 않는다.
