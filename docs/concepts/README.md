# Still의 생각: 주제별 읽기

**한국어 개념 해설 · 2026-11-05 · 비규범 문서**

Still을 이해하기 위해 과거 이슈의 순서를 따라갈 필요는 없다. 이 문서 묶음은 기존 총람, 연구와 현재 계약에 흩어진 생각을 일곱 질문으로 다시 엮는다. 구현 지침의 정본은 해당 영문 계약이며, 실제 지원 범위는 [제품 현황](../product-status.en.md)에서 확인한다. 여기의 설명은 새로운 기능, 권한이나 개발 목표를 활성화하지 않는다.

## 하나의 중심 논지

**지능과 실행 수단을 빌려도, 사용자와 이어온 관계의 상태와 통제권은 사용자에게 남아야 한다.**

그 관계는 친근한 말투만으로 유지되지 않는다. 무엇을 알고 있는지, 무엇을 맡았는지, 무엇을 해도 되는지, 무엇이 실제로 끝났는지를 모델 밖에 남겨야 한다. 온톨로지는 그 상태의 의미를, 기억은 시간에 따른 변화를, 판단과 실행은 일을 수행하는 과정을, Presence는 그 과정에 사용자가 참여하는 방식을 설명한다. 권한과 증거는 모든 영역을 가로지르는 경계다.

이는 기존 자료의 편집적 종합이지 하나의 새 런타임 설계가 아니다. 아래의 일곱 장도 일곱 개 서비스나 데이터베이스를 만들라는 요구가 아니다.

## 읽는 순서

| 질문 | 본문 | 연결되는 구현 규칙 |
| --- | --- | --- |
| AI를 바꾸어도 무엇이 내게 남아야 하는가? | [1. 소유와 연속성](ownership-and-continuity.ko.md) | [Architecture](../personal-agentos-architecture.en.md), [Owner Control](../owner-control-contract.en.md) |
| 같은 현실을 어떻게 이해하고 역할별로 해석하는가? | [2. 온톨로지와 역할](ontology-and-roles.ko.md) | [Professional Roles](../professional-roles-and-communication-contract.en.md) |
| 무엇을 기억하고 어떻게 고쳐 배워야 하는가? | [3. 기억과 학습](memory-and-learning.ko.md) | [Secretary Agency](../secretary-agency-contract.en.md), [Current Context](../current-context-contract.en.md) |
| 고정 시나리오 없이 어떻게 일을 끝내는가? | [4. 판단과 실행](judgment-and-execution.ko.md) | [Decision](../decision-layer.en.md), [Execution](../assistant-execution-contract.en.md) |
| 일을 하는 동안 어떻게 같은 관계를 유지하는가? | [5. Presence와 협업](presence-and-collaboration.ko.md) | [Presence](../presence-experience-contract.en.md), [Professional Roles](../professional-roles-and-communication-contract.en.md) |
| 허용된 행동과 확인된 결과를 어떻게 구별하는가? | [6. 권한과 증거](authority-and-evidence.ko.md) | [Owner Control](../owner-control-contract.en.md), [Connection and Delegation](../connection-delegation-contract.en.md) |
| 이 구조는 어떤 생태계와 사업을 가능하게 하는가? | [7. 생태계와 사업 가설](ecosystem-and-business.ko.md) | [Platform Foundation](../agent-distribution-platform-foundation.en.md); 사업성의 증명은 아님 |

각 장은 문제, 핵심 구분, 선택의 이유와 남은 질문을 설명한다. 자세한 계약 문구와 매번 변하는 지원 목록을 복사하지 않는다. 장의 근거 링크를 통해 원연구와 현재 규칙을 따로 읽을 수 있다.

## 자료의 역할

[2026-10-14 총람](../whitepapers/personal-agentos-compendium.ko.md)은 이번 편집의 출발점이자 고정된 역사 판본이다. 새 본문은 그 판본의 주장을 현재 기능으로 승격시키지 않으며, 이후 기억·dream·협업 관련 변경은 해당 계약과 [연구 기록](../research/README.md)에 따로 연결한다. [백서](../whitepapers/README.md)는 산업 가설과 출판 기록으로 남는다. 두 영역을 모두 또 하나의 최신 개념 설명서로 갱신하지 않는다.

검토 기준은 저장소 커밋 `fda6e41591d69100d2155d24b08e955a98858fac`이다. 공개된 저장소 자료만 사용했으며, 비공개 대화·실계정 데이터·개인 연구 입력을 추가 공개하지 않았다. 외부 논문을 새로 검증한 문헌 리뷰나 독립 전문가 검토도 아니다.

**설계 원칙, 채택된 계약, main 구현, 배포본, 실사용 관찰은 서로 다른 상태다.** 본문은 그 차이를 지운 단일한 '완성된 이론'이 아니라, 무엇을 지향하며 무엇을 아직 확인해야 하는지 읽는 경로다.

[전체 문서 지도](../README.md)
