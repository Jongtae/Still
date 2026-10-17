# Goal 실행 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](goal-execution-contract.en.md)을 따른다.

## 목적

이 계약은 활성 Still 작업 항목을 agent goal로 실행 가능하게 만든다. Goal은 문서 제목을 되풀이하는 것이 아니라, 권위 있는 출처·허용된 권한·관찰 가능한 증거·정직한 종료 조건을 갖는 제한된 약속이다.

새로운 활성 설계, 구현, release, remediation, 운영 모드 배포, 문서 iteration은 모두 이 계약을 사용한다. Historical record는 historical로 남으며, vision 문서와 reserved proposal은 입력일 뿐 실행 가능한 goal이 아니다.

## Goal-ready record

Goal을 활성화하기 전에 issue와 원본 계획은 다음을 모두 식별해야 한다.

| 항목 | 필요한 의미 |
| --- | --- |
| Objective | 완료 시 참이어야 할 범위를 포함한 하나의 사용자 표시 결과. 최상위 objective는 유한하고 순서 있는 substep을 이름으로 지정할 수 있다. |
| Source of truth | Issue, 활성 `delivery-plan.yaml` iteration, 적용 contract 문서. 활성 delivery plan이 순서를 정한다. |
| State와 선행조건 | `active` 작업은 의존성을 충족한다. `reserved`, `proposed`, archived, blocked 작업은 조용히 활성화하지 않는다. |
| Allowed authority | Goal이 바꿀 수 있는 file, runtime boundary, repository, 외부 system. Read-only inspection은 허용되지만 새 credential, 외부 action, scope 확장은 명시 contract가 필요하다. |
| Non-goals | Agent가 더 쉬운 결과나 더 넓은 결과로 대체하지 못하게 의도적으로 제외한 인접 작업 |
| Work units | 각각 관찰 가능한 결과를 갖는 작은 순서 있는 deliverable. 의존 구현 전에 설계 작업이 contract를 확정한다. |
| Evidence | 정확한 automated check, fixture, review artifact, 그리고 운영 모드에서만 deployment health evidence. Mock과 운영 증거를 따로 이름 붙인다. |
| 위임 기록 | 위임한 각 work unit의 배타적 file ownership, 요청한 model/reasoning, 가능한 경우 tool이 수락한 설정, 관찰한 결과, 독립 위임 사유 |
| 독립 검토 | 관련 security, recovery, external boundary, final-completion 작업의 필수 review artifact. Routine owner manual-test gate가 아니다. |
| Completion rule | merged artifact, 필수 CI, tracker/roadmap/ledger closeout을 포함해 약속한 모든 artifact, state transition, check를 증명하는 현재 요구사항-증거 audit |
| Blocked rule | 진행을 막는 구체적 외부 조건, 이미 시도한 recovery, 필요한 다음 권한 또는 state change |

## 실행 lifecycle

1. 현재 repository, issue, branch, plan, 이전 evidence를 검사한다. 이전 대화에만 의존하지 않는다.
2. Goal-ready record에서 checklist를 도출한다. 모든 명시 요구사항과 의존성을 보존한다.
3. 구현 또는 문서를 바꾸기 전에 필요한 issue와 목적을 나타내는 `<type>/<issue>-<slug>` branch를 만든다. Commit은 의도적이고 범위를 지킨다.
4. 순서 있는 work unit을 완료한다. Material change 뒤에는 다음 작업으로 가기 전에 관련 contract를 시험한다. 기록한 model, ownership, review 경계 안에서만 위임한다.
5. 원본 plan이 요구할 경우 plan/doc parity, local-link, ledger, full-suite를 포함한 선언된 전체 validation을 실행한다.
6. Automated evidence와 operating evidence를 구분한 PR을 만들고, merge·issue close·`TASKS.md`/`docs/roadmap.md`/ledger 갱신을 함께 수행한다.
7. Completion audit를 한다. 그 뒤에만 goal complete를 보고한다.

## 자율 개발 사이클 위임

소유자가 명시적으로 위임한 사이클에서는 Agent가 매 작은 work unit 뒤 소유자 리뷰를 기다리지 않는다. Agent는 명시적으로 active인 goal-ready iteration 또는 명시적으로 active인 최상위 Goal의 유한하고 순서 있는 substep을 design→implementation→automated validation→PR merge→closeout로 계속 실행할 수 있다.

이 위임은 자동 설치·자동 권한 상승·운영 모드 전환 권한이 아니다. Agent는 active 최상위 Goal의 이미 열거되고 dependency가 충족된 substep에 한해 issue와 `<type>/<issue>-<slug>` branch를 만들 수 있다. 열거되지 않은 후속 goal을 선택하거나 새 feature를 시작하거나 reserved proposal을 재활성화해서는 안 된다. credential 또는 OAuth 구성, 새 외부 connection/endpoint, permission/scope 확대, consequential external action, 개인 데이터 경계 확대, security/governance boundary 변경, 또는 Master Plan 사이클 완료는 해당 action이 active goal-ready record에서 정확히 명시 승인되지 않은 한 중단해야 한다. 다음 선택이 필요하면 구현을 시작하지 않고 후보와 근거를 보고한다.

## 위임 및 독립 검토

활성 issue는 독립 검토가 가능한 경우에만 역할 기반 model 위임을 기록한다. 기본 요청 역할은 주 작업자 Astra medium, 독립 탐색/문서 확인 Terra low 또는 medium, 제한된 구현 Sol medium, security/recovery/final completion 검토 Astra high다. 이는 요청한 model이 사용 가능했다는 주장이 아니다. 기록은 요청·수락·관찰 설정을 구분하고, 두 구현자가 같은 file을 동시에 수정하지 않도록 file을 배정한다.

Goal이 security, recovery, external boundary, automation/authority control, completion claim을 변경하면 complete 전에 독립 review artifact가 필요하다. Reviewer는 현재 diff와 evidence를 점검하고 미해결 finding을 이름으로 남기며, 가짜 product success나 routine owner manual test를 대신하지 않는다.

하나의 기존 delivery automation은 contract를 읽고 issue, branch, plan, task state를 확인한 뒤 이름 붙은 active goal만 재개할 수 있다. 이미 열거된 substep만 진행할 수 있고, 다른 automation을 만들거나 이미 active인 task와 동시에 실행해서는 안 된다. Active 최상위 Goal이 없거나 최상위 closeout 뒤에는 paused 상태로 유지하고, retry에는 의미 있는 조건 변화가 필요하다.

## 종료 상태 규율

- Goal은 현재 요구사항-증거 audit가 모든 완료 항목, merged artifact, 필수 CI 결과, tracker/roadmap/ledger closeout을 증명할 때만 **complete**다. 최상위 Goal은 모든 열거된 substep과 requirement가 complete, owner-setting-only, 또는 별도 decision-required임도 증명한다. Intent, 부분 fixture, closed issue, 병합되지 않은 branch, 좁은 test는 더 넓은 주장을 증명하지 못한다.
- 안전한 다음 행동이 남아 있으면, 작업이 어렵거나 미완성이라도 goal은 **active**로 남는다.
- 같은 구체적 외부 blocker가 세 goal turn에 걸쳐 반복되고 의미 있는 안전한 진행이 없을 때만 **blocked**다. 보고에는 blocker, evidence, 필요한 최소 다음 입력을 적는다.
- Goal은 routine owner manual test, 실제 credential, live provider를 개발 blocker로 취급하지 않는다. 활성 goal이 운영 모드 작업을 명시 승인하지 않는 한 이는 별도 운영 모드 배포에 속한다.

## 설계–구현 추적성

완료된 설계 contract는 interface와 acceptance boundary가 명세되었다는 증거일 뿐 capability가 존재한다는 증거는 아니다. 모든 Master Plan 설계 entry는 의존 구현 entry와 영어 원본 contract를 이름으로 지정해야 한다. 구현 entry는 설계로 다시 연결되고 automated evidence를 선언해야 하며, goal-ready issue가 권한을 부여할 때까지 비활성으로 남는다.

Delivery-plan verifier는 이 mapping이 없는 설계를 거절한다. 또한 선언된 모든 구현이 mapped automated evidence와 함께 documented complete가 아니면 Master Plan 또는 capability의 `development_complete` claim을 거절한다. `design_complete`, `in_progress`, `requires-goal-ready-issue`는 의도적으로 더 좁은 상태이며, 사용자 표시 capability 완료 또는 운영 claim으로 표현해서는 안 된다.

## 문서 routing

| 문서 종류 | Goal 동작 |
| --- | --- |
| Vision | 방향과 non-goal을 제공하며, 단독으로 작업을 활성화하지 않는다. |
| Master Plan | Phase, 완료 기준, 설계/구현 순서를 제공한다. 활성 iteration은 여전히 delivery plan에서 선택되어야 한다. |
| 활성 delivery plan | 다음 실행 가능한 iteration과 선언된 validation command를 선택한다. |
| 설계 contract | 선행조건이 완료됐을 때만 설계 goal이 되며, 의존 구현의 contract와 fixture를 정의해야 한다. |
| Issue | Goal-ready record와 PR closeout evidence를 담는다. |
| Reserved/proposed proposal | 후보와 빠진 승격 evidence를 기록하며, 명시 승격 전에는 구현 작업을 만들 수 없다. |
| 운영 모드 runbook | 개발 완료 뒤, 명시된 소유자 제어 구성 권한 안에서만 실행 가능하다. |

## Goal prompt template

Goal을 활성화할 때 다음 template을 사용한다.

```text
<권위 있는 issue와 delivery-plan entry>의 <iteration ID와 사용자 결과>를 실행하라.

명시된 선행조건, non-goal, 데이터/권한 경계, 운영 모드 분리를 보존한다. 문서화된 authority 안에서만 작업한다. 순서 있는 work unit을 구현한 뒤 선언된 모든 validation을 실행하고, 현재 repository와 PR state를 기준으로 요구사항별 completion audit를 수행한다.

Issue, branch, PR merge, 필수 CI, 요구사항-증거 audit, tracker/roadmap/ledger closeout, 모든 선언된 evidence가 현재 상태가 되기 전에는 goal complete로 표시하지 않는다. 이 active goal만 계속하고 후속 goal을 선택해서는 안 된다. 이 특정 goal이 운영 모드 작업을 명시 승인하지 않는 한 live credential, 실제 provider, owner 수동 검증은 범위 밖이다. 안전한 recovery를 시도한 뒤 하나의 외부 blocker가 세 goal turn 동안 지속되면 evidence와 함께 blocked로 표시하고, 그렇지 않으면 계속 진행한다.
```

## 필요한 최종 보고

최종 보고는 결과, 병합된 PR/issue, 정확한 validation evidence, 데이터/보안 영향, 알려진 한계, 남은 운영 모드 구성을 적는다. Mock-contract evidence만 있을 때 외부 capability가 live라고 주장하지 않는다.
