# 원문 대조와 참고문헌

[주제별 목차](README.md)

**대조일: 2026-11-05.** 이 목록은 일곱 장의 주요 개념을 공개된 일차 자료와 비교한 기록이다. 원문이 말하는 내용과 Still에 적용한 해석, 아직 입증되지 않은 주장을 구분한다. 규격은 판본을 명시했으며 최신판이라는 의미는 아니다. 논문·규격을 설치하거나 벤치마크를 실행한 기록도 아니다.

일부 논문은 저자 측 공개 초록을 대조했다. 해당 항목에 그 범위를 적었으며, 읽지 않은 본문의 실험 절차나 결과까지 검증했다고 하지 않는다. 고정된 원연구·백서의 인용 전체를 다시 감사한 목록은 아니다.

<a id="r1"></a>
## R1. Solid Protocol 0.11.0

원문: [Solid Protocol, 2024-05-12 판본](https://solidproject.org/TR/2024/protocol-20240512). Draft Community Group Report. 대조 부분: Abstract, Status of This Document, Introduction.

**원문 범위:** 애플리케이션이 외부에 저장된 데이터에 권한을 확인하고 상호운용 가능한 방식으로 접근하도록 관련 규격을 연결한다. 이 판본은 W3C 표준이나 표준화 절차상의 문서가 아니라고 명시한다.

**적용과 한계:** 데이터 관리와 애플리케이션 선택을 구분하는 선례로 비교한다. Still의 작업 상태 이전, Solid 호환성, 별도 개인 에이전트 사업의 수요를 입증하지 않는다. 연결: [1장](ownership-and-continuity.ko.md), [7장](ecosystem-and-business.ko.md).

<a id="r2"></a>
## R2. OWL 2 Web Ontology Language Primer, Second Edition

원문: [W3C, 2012-12-11 판본](https://www.w3.org/TR/2012/REC-owl2-primer-20121211/). Recommendation으로 발행된 입문서이며 본문은 informative다. 대조 부분: Abstract, §2 What is OWL 2?, §3 Modeling Knowledge: Basic Notions.

**원문 범위:** 클래스·속성·개체와 논리적 진술을 통해 지식을 표현하고 추론한다. OWL은 일반적인 문서 형식 검증기나 데이터베이스 자체가 아니며, 기록되지 않은 사실을 곧 거짓으로 보지 않는 개방세계 가정을 설명한다.

**적용과 한계:** Still의 자연어 개념 모델과 형식 온톨로지 구현을 구분한다. 지식의 개방세계 가정을 실행 허가의 기본값으로 옮기지 않는다는 것은 이 해설의 설계적 구분이다. 연결: [2장](ontology-and-roles.ko.md).

<a id="r3"></a>
## R3. SKOS Simple Knowledge Organization System Primer

원문: [W3C, 2009-08-18 판본](https://www.w3.org/TR/2009/NOTE-skos-primer-20090818/). Working Group Note. 대조 부분: Abstract, §1 Introduction, §5.2 SKOS Concepts and OWL Classes.

**원문 범위:** 개념의 이름·분류·연관 관계 등 지식 조직 체계를 표현한다. SKOS 개념과 OWL 클래스를 구별하고 함께 사용하는 문제를 다룬다. 규범적 명세인 SKOS Reference와 이 입문서의 지위는 다르다.

**적용과 한계:** 용어를 정리하는 일과 도메인의 논리적 제약을 형식화하는 일을 구분하는 근거로 쓴다. SKOS에 의미 체계가 없다는 뜻이나, 어느 표현 방식이 항상 우수하다는 뜻은 아니다. 연결: [2장](ontology-and-roles.ko.md).

<a id="r4"></a>
## R4. FIBO Vocabulary

원문: [EDM Council의 FIBO Vocabulary 설명](https://spec.edmcouncil.org/fibo/page/vocabulary). 대조 부분: 본문의 어휘 표현과 FIBO Ontologies 비교. 특정 온톨로지 릴리스를 내려받아 검증한 것은 아니다.

**원문 범위:** FIBO Vocabulary는 SKOS로 배포되는 표현이며, 전체 FIBO 온톨로지의 논리적 풍부함을 모두 담지 않는다고 설명한다.

**적용과 한계:** 금융 용어집의 활용, 관계 모델의 재사용, 전체 온톨로지 도입을 별개의 선택으로 다룬다. 금융비서의 목표 관리, 승인·실행 구조나 전문 자격을 FIBO만으로 갖추었다고 해석하지 않는다. 연결: [2장](ontology-and-roles.ko.md).

<a id="r5"></a>
## R5. LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory

원문: Di Wu 외, [arXiv:2410.10813v2 HTML 본문](https://arxiv.org/html/2410.10813v2). 대조 부분: Abstract, §3.1–3.3, §4, Appendix B.

**원문 범위:** 정보 추출, 여러 대화의 종합, 시간 추론, 정보 갱신, 답변 유보를 구분한다. 색인·검색·읽기를 나누어 설계를 분석하며, AI가 제공한 정보의 회상도 일부 평가한다.

**적용과 한계:** Still의 저장·검색·사용을 분리해 점검하는 비교 틀이다. 벤치마크가 회상하는 정보의 범위가 Still의 사용자 사실 저장 정책을 정하지는 않는다. Still의 오프라인 실험과 데이터·판정·지표가 다르므로 점수를 직접 비교하지 않는다. 연결: [3장](memory-and-learning.ko.md).

<a id="r6"></a>
## R6. Generative Agents: Interactive Simulacra of Human Behavior

원문: Joon Sung Park 외, 2023, [저자 논문](https://arxiv.org/abs/2304.03442), DOI 10.1145/3586183.3606763. 대조 부분: 초록과 공개 본문의 Introduction, Architecture, Evaluation 설명.

**원문 범위:** 가상 환경의 에이전트에서 경험 기록·검색·성찰·계획을 결합하고 행동의 개연성을 평가한다.

**적용과 한계:** 기억을 다시 검토해 다음 행동에 활용하는 설계의 참고점이다. 실제 사용자에 관한 사실의 정확성, 안전한 기억 수정, Still의 dream 효과를 직접 검증한 연구가 아니다. 연결: [3장](memory-and-learning.ko.md).

<a id="r7"></a>
## R7. ReAct: Synergizing Reasoning and Acting in Language Models

원문: Shunyu Yao 외, [논문 arXiv:2210.03629](https://arxiv.org/abs/2210.03629), [저자 프로젝트 설명](https://react-lm.github.io/). 대조 부분: 논문 초록과 저자 페이지의 Abstract, ReAct Prompting 설명. 상세 수치나 표를 이번 비교에 사용하지 않았다.

**원문 범위:** 추론·행동·환경 관찰을 연결해 계획을 조정하는 접근을 제시하고 정해진 과제에서 평가한다.

**적용과 한계:** 실행 결과를 다음 판단에 사용하는 설계를 비교한다. Still의 별도 판단 모델이 더 낫다는 결론, 실행 권한의 보증, 숨은 추론 기록의 공개 의무로 확대하지 않는다. 연결: [4장](judgment-and-execution.ko.md).

<a id="r8"></a>
## R8. Model Context Protocol — Architecture

원문: [MCP 2025-06-18 아키텍처 문서](https://modelcontextprotocol.io/specification/2025-06-18/architecture). 대조 부분: Core Components, Design Principles, Capability Negotiation.

**원문 범위:** 호스트·클라이언트·서버의 책임과 프로토콜 기능 협상을 설명한다. 호스트가 연결 권한·동의·맥락을 관리하고 서버가 도구·자원·프롬프트를 제공하는 구조다.

**적용과 한계:** 연결 역할의 구분을 비교할 뿐, Still의 모든 경로가 그 경계를 구현했다고 보증하지 않는다. 프로토콜 기능 지원은 작업 성공·전문성·수요의 증거와 다르다. 연결: [4장](judgment-and-execution.ko.md), [7장](ecosystem-and-business.ko.md).

<a id="r9"></a>
## R9. “Like Having a Really Bad PA”: The Gulf between User Expectation and Experience of Conversational Agents

원문: Ewa Luger, Abigail Sellen, CHI 2016. [저자기관의 논문 소개·초록](https://www.microsoft.com/en-us/research/publication/like-having-a-really-bad-pa-the-gulf-between-user-expectation-and-experience-of-conversational-agents-2/). 이번 대조 범위는 공개 초록이다.

**원문 범위:** 대화형 에이전트 사용자 14명의 인터뷰에서 기대와 실제 작동의 간극을 보고한다.

**적용과 한계:** 능력에 맞는 기대를 형성해야 한다는 문제의식과 연결한다. 현대 LLM에 대한 통제 실험이나 Still의 Presence 효과를 입증한 결과로 취급하지 않는다. 연결: [5장](presence-and-collaboration.ko.md).

<a id="r10"></a>
## R10. Guidelines for Human-AI Interaction

원문: Saleema Amershi 외, CHI 2019. [저자기관의 논문 소개·초록](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/). 이번 대조 범위는 공개 초록이다.

**원문 범위:** 18개 지침을 제안하고, 실무자 49명이 20개 AI 제품에 적용하는 평가를 포함해 지침의 관련성을 검토했다.

**적용과 한계:** 인간–AI 상호작용의 설계 참고자료다. 특정 문구나 아키텍처의 효과 크기, 업무 정확도 또는 장기 신뢰의 인과적 개선을 이 초록에서 끌어내지 않는다. 연결: [5장](presence-and-collaboration.ko.md).

<a id="r11"></a>
## R11. ODRL Information Model 2.2

원문: [W3C Recommendation, 2018-02-15 판본](https://www.w3.org/TR/2018/REC-odrl-model-20180215/). 대조 부분: Abstract, §1, §2의 Policy·Constraint·Rule 정의.

**원문 범위:** 자원에 대한 허용·금지·의무와 조건을 표현하는 정책 정보 모델을 제공한다.

**적용과 한계:** 권한 조건을 명시하는 설계와 비교한다. 표현 규격을 참고했다는 사실에서 Still의 집행·호환성을 추론하지 않는다. “정책 표현과 실행 집행은 별도 확인 대상”이라는 구분은 이 해설의 적용 판단이다. 연결: [6장](authority-and-evidence.ko.md).

<a id="r12"></a>
## R12. PROV-AQ: Provenance Access and Query

원문: [W3C Working Group Note, 2013-04-30 판본](https://www.w3.org/TR/2013/NOTE-prov-aq-20130430/). 대조 부분: §1.3 Interpreting provenance records.

**원문 범위:** 출처 기록 자체가 정확하거나 권위 있다고 보장되지 않으며, 그 기록에 대한 신뢰는 별도로 판단해야 한다고 명시한다.

**적용과 한계:** 실행 기록의 존재와 요청한 결과의 확인을 구분하는 근거로 쓴다. Still의 Evidence가 PROV 규격을 구현했다거나, 기록만으로 외부 결과가 참이라고 보증하지 않는다. 연결: [6장](authority-and-evidence.ko.md).

## 내부 자료와의 대조

외부 참고문헌은 설계의 비교 대상이며 현행 구현 규칙을 대체하지 않는다. 내부 근거는 [문서 지도](../README.md)의 영문 기준 문서, [총람](../whitepapers/personal-agentos-compendium.ko.md), [원연구](../research/README.md), [공개 기억 실험](../research/memory-and-dream-experiments-2026-10-22-to-11-05.ko.md)이다. 실제 지원 여부는 [제품 현황](../product-status.en.md)과 해당 코드·테스트·관찰 기록으로 확인한다.
