# 개발 거버넌스 — Contract-First Delivery

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](development-governance.en.md)을 따른다.

## 목적

Still은 contract-first delivery와 자동 품질 gate를 사용한다. 모든 변경에서 사람이 모든 기능이나 third-party 서비스를 직접 반복 시험하지 않아도 빠르고 재현 가능하게 개발하기 위한 방식이다.

## 방법론

이 거버넌스는 서로 보완하는 네 가지 방식을 결합한다.

1. **Contract-first development.** 모든 capability는 구현 전에 사용자 결과, 경계, 입력, 출력, 실패 상태, 데이터 분류, 비목표를 정의한다.
2. **Consumer-driven contract testing.** Still adapter는 자신이 소비하는 정확한 외부 요청/응답 contract를 표현한 versioned fixture와 test double로 검증한다.
3. **Test-pyramid automation.** 대부분의 검증은 작은 unit·component test에 두고, 더 적은 수의 test로 로컬 통합과 제품 end-to-end 흐름을 확인한다. UI나 수동 broad-stack test는 기본 release gate가 아니다.
4. **Continuous-integration quality gate.** 모든 pull request는 merge 전에 결정적인 formatting, 영어 원본 문서 존재, local-link, contract-fixture, 관련 자동 테스트를 실행한다. 과거 번역본은 영어 원본 링크를 유지하되 번역 일치는 merge gate가 아니다.

## 개발 완료 규칙

[Goal 실행 계약](goal-execution-contract.ko.md)은 활성 plan/issue가 실행 가능한 goal이 되는 방식, active/blocked 상태를 유지하는 방식, 완료 전 필요한 audit를 정의한다.

Iteration은 다음이 issue와 pull request에 기록될 때 완료된다.

- 문서화된 contract와 위협/데이터 경계 결정;
- 해당하는 경우 versioned 성공, 거절, timeout, malformed-response, recovery fixture;
- 해당 fixture 또는 mock을 상대로 제품 코드를 실행하는 자동 테스트;
- 통과한 필수 CI check; 및
- mock 증거와 운영 증거의 정확한 구분 및 알려진 제한.

개발 iteration을 완료하기 위해 사람이 반복적인 수동 acceptance test를 하거나, third-party provider에 로그인하거나, provider token을 만들 필요는 없다. 사람의 검토는 반복 테스트 gate가 아니라 제품 및 보안 설계 활동으로 남는다.

## 외부 연동과 Agent

MCP server, A2A peer, runtime, OAuth provider, 외부 action API는 Still이 소유하는 adapter 뒤에 구현한다. 개발 contract는 허용 scope, 요청/응답 schema, timeout, cancellation, idempotency, redaction, approval 동작, error mapping, disconnect/recovery 동작을 정의해야 한다.

개발은 로컬 mock peer와 fixture만 사용한다. live credential, live URL, 실제 provider account, 실제 action을 요구하지 않는다. Mock은 선언되지 않은 요청을 거절해야 adapter가 검토되지 않은 의존성을 우연히 늘리지 못한다.

## 운영 모드 배포

Master Plan 전체가 완료된 뒤에만 소유자가 Still을 운영 모드로 배포하고 실제 credential, OAuth client 등록, endpoint, enable한 connection을 설정한다. 이는 모든 개발 PR에 대한 소급 수동 acceptance test가 아니라 구성과 활성화다.

배포된 runtime은 자동 startup 및 health check를 사용하고, 설정이 누락되었거나 유효하지 않으면 fail closed하며, evidence에서 secret을 redaction하고, machine-readable deployment report를 남겨야 한다. 외부 connection은 자동 health check가 성공했을 때만 configured라고 설명할 수 있다. Mock 증거는 개발 증거로 표시하며 외부 서비스가 live라는 증거로 사용하지 않는다.

## 거버넌스 통제

- 모든 iteration에는 GitHub issue, 목적을 나타내는 `<type>/<issue>-<slug>` branch(`feat`, `fix`, `docs`, `refactor`, `test`, `chore` 등 Conventional Commits type이며 AI 도구 이름은 쓰지 않는다), 집중된 commit, PR이 필요하다. 과거의 `codex/` branch 이름은 기록으로 유효하다.
- CI는 자동 gate 실패 시 merge를 막으며, 사람이 반복적인 live test를 할 때까지 기다리지 않는다.
- Contract 변경은 같은 PR에서 fixture와 mock suite도 함께 변경해야 한다.
- 새 scope, 외부 write, credential, 데이터 class, recovery 의미 변경은 구현 전에 contract와 위협 모델을 갱신해야 한다.
- Test는 가능한 한 결정적이고 hermetic해야 하며 개인 데이터나 외부 credential 없이 안전하게 실행되어야 한다.
- Capability는 선언된 자동 gate가 통과한 뒤에만 *development-complete*라고 설명한다. 이후 자동 배포 health check가 성공했을 때만 *operating-configured*라고 설명한다.

## 비목표

이 정책은 mock이 vendor 가용성, 계정 entitlement, 네트워크 도달성, 실제 provider 동작을 증명한다고 주장하지 않는다. 또한 문서화되지 않은 외부 호출, arbitrary runtime 설치, 승인 및 local-first 데이터 경계 우회를 허용하지 않는다.
