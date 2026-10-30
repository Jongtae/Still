# 연구 기록: 기억을 어떻게 알리고 다룰까 (2026-10-30)

이 문서는 #422의 근거 기록이다. 구현을 활성화하지 않는다. 채택된 규칙은 [Secretary Agency Contract](../secretary-agency-contract.en.md)의 #422 개정과 [Presence Experience Contract](../presence-experience-contract.en.md) 원칙 7에 있다. 소유자의 개인 정보는 이 문서에 적지 않는다.

## 1. 계기

실제 설치에서 처음 돈 밤사이 정리(#402 dream)는 저녁, 재시작 직후에 실행됐다. 소유자에게 "기억했어요" 알림 하나를 보냈는데, 그 안에 성격이 다른 세 가지가 섞여 있었다.

- 같은 사실을 겹쳐 적은 줄을 합친 정리. "X (전에는 X, Y)"처럼 읽혔다.
- 주제가 다른 사실 하나를 "중복"이라며 끝낸 것. 이 사실은 지금 기억에서 빠졌다.
- 하루 기록(`diary.<date>`). 개발 세션이 `POST /api/chat`으로 보낸 검증 요청(`request_key`가 `verify-`로 시작)을 소유자가 "요청했어요"라고 단정했다.

소유자는 기억한 내용 자체는 괜찮다고 봤다. 문제는 알리는 방식과 다루는 방식이었다. 참고로 다른 비서 앱의 대화 화면을 들었다. 그 앱은 친구를 추가하는 일 중에 관계를 묻고("알아두면 일정 조율에 도움이 돼요"), 답을 듣자 예전 기억과 이어 이해했음을 보인 뒤("그때 챙기셨던 분이 그분이셨군요"), 하던 일을 계속했다.

## 2. 질문

1. 밤사이 정리한 결과는 알려야 하나, 알린다면 언제 어떻게 알려야 하나?
2. 기억을 끝내거나 바꿀 때 묻지 않고 해도 되나, 언제 물어야 하나?
3. 기억한다는 것을 보고하는 것과 써서 보여주는 것 중 무엇이 나은가?
4. 무엇이 소유자의 말로 인정되어 기억이 될 수 있나?

## 3. 근거

표기: [PR] 동료 심사 논문, [PP] 심사 전 논문, [VD] 제품 문서, [STD] 업계 표준, [BL] 블로그·기사. "초록"은 초록이나 요약만 확인했다는 뜻이다.

### 3.1 설계 지침

- Amershi 외, *Guidelines for Human-AI Interaction*, CHI 2019 [PR, 전문]. 관련 지침은 다음과 같다. https://www.microsoft.com/en-us/research/project/guidelines-for-human-ai-interaction/
  - G3 상황에 맞춰 시점 정하기
  - G4 지금 하는 일과 관련된 정보만 보이기
  - G12 최근 상호작용 기억하기
  - G14 조심스럽게 갱신하기
  - G16 사용자 행동의 결과 알리기
- Horvitz, *Principles of Mixed-Initiative User Interfaces*, CHI 1999 [PR]. 자동화가 실패하는 흔한 원인은 잘못된 추측, 손익을 따지지 않은 행동, 나쁜 타이밍이다. 덜 방해되는 때로 미루는 것이 이득이다. https://www.microsoft.com/en-us/research/publication/principles-mixed-initiative-user-interfaces/
- Google PAIR, *Feedback + Controls* [VD]. 암묵적으로 배운 것은 보고 고칠 수 있어야 한다. 변화가 언제 반영되는지 알려야 한다. https://pair.withgoogle.com/chapter/feedback-controls/

### 3.2 제품과 기억 엔진

- ChatGPT [VD]: 저장한 그 답변에 "Memory updated"를 표시한다. 기억 관리 화면에서 기록을 보고 이전 버전으로 되돌릴 수 있다. https://openai.com/index/memory-and-new-controls-for-chatgpt/ , https://help.openai.com/en/articles/11146739-how-does-reference-saved-memories-work
- Claude [VD]: 대화 중 주제별로 저장하고, 설정에 요약을 둔다. 요약은 대화로 고칠 수 있다. https://support.anthropic.com/en/articles/11817273-how-does-claude-s-memory-work
- Letta sleep-time 에이전트 [VD]: 쉬는 시간에 기억을 정리한다. 확인 단계는 선택이며, 그마저 다른 에이전트가 검토하는 것이지 사용자가 아니다. https://docs.letta.com/guides/agents/sleep-time-agents
- LangMem [VD]: 백그라운드 모드에서 사용자에게 알리거나 묻는 방식은 문서에 없다. https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Zep/Graphiti [PP]: 모순되는 사실을 지우지 않고 무효 시점을 기록한다. 사용자는 관여하지 않는다. https://arxiv.org/html/2501.13956
- Mem0 [PP, VD]: ADD/UPDATE/DELETE/NOOP 중 하나를 고르고, 변경마다 그 변경을 낳은 입력 메시지를 이력에 남긴다. https://arxiv.org/html/2504.19413 , https://docs.mem0.ai/api-reference/memory/history-memory
- Gemini 임시 채팅 [VD]: 임시 채팅은 개인화에 쓰이지 않는다. 세션을 기억에서 분리하는 제품 사례다. https://blog.google/products-and-platforms/products/gemini/temporary-chats-privacy-controls/

**요약.** 확인한 시스템 중 백그라운드 정리 결과를 변경마다 사용자에게 알리는 곳은 없었다. 대화 중 저장은 그 자리에 작게 표시하고, 나머지는 설정 화면에서 보고 고친다.

### 3.3 연구

- Jo 외, CHI 2024 (CareCall) [PR, 초록]. 지난 통화의 기억을 다시 꺼내자 친근함과 자기 개방이 늘었다. 만성 질환처럼 민감한 기억은 다시 꺼낼 때 조심해야 했다. https://arxiv.org/abs/2402.11353
- Bae 외, EMNLP Findings 2022 [PR]. 기억 갱신을 PASS/APPEND/DELETE/REPLACE로 나눈다. https://aclanthology.org/2022.findings-emnlp.276/
- ForgetMeNot, IJHCS 2014 [PR, 초록]. 틀린 기억은 잊는 것보다 더 답답하고 덜 믿음직하게 느껴졌다. 잊는 것은 자연스럽게 받아들여졌다. https://researchers.mq.edu.au/en/publications/forgetmenot-what-and-how-users-expect-intelligent-virtual-agents-/
- HAI 2023 (3주, 169명) [PR, 초록]. 지난 대화를 언급하면 더 지적이고 흥미롭게 보였다. 그대로 인용하면 사생활 걱정이 늘었다. https://arxiv.org/abs/2308.04879
- Lee 외, HRI 2012 [PR, 전문]. 작은 취향을 기억한 로봇이 친밀감과 협력을 높였다. https://minlee.ischool.utexas.edu/materials/Publication/2012-HRI-personalization.pdf
- Zhang 외, 2025 (18명 인터뷰) [PP]. 무엇이 대체됐는지 알고 싶어 했다. 추론한 기억은 추론이라고 밝히고 지울 수 있기를 원했다. https://arxiv.org/html/2508.07664v1
- Chen 외, CHI 2026 (20명 인터뷰) [PR, 대학 소개 글로만 확인]. 기억된 내용을 뒤늦게 알고 부정적으로 놀랐다. 더 잘 보이고 통제할 수 있기를 원했다. https://grady.uga.edu/?p=33317
- Monteiro 외, 2026 (추론 215건, 18명) [PP]. 추론을 대체로 호기심으로 받아들였다. 불편함은 틀렸거나 예상 밖으로 쓰일 때 생겼다. https://arxiv.org/abs/2605.10013
- 묻기에 관한 연구:
  - 좋은 질문 하나가 결과를 크게 높인다. [PR, SIGIR 2019] https://arxiv.org/pdf/1907.06554
  - 품질이 낮은 질문은 오히려 해롭다. [PR, TOIS 2023, 초록] https://research.tudelft.nl/en/publications/users-meet-clarifying-questions-toward-a-better-understanding-of-
  - 불확실한 경우만 골라 물으면 무작위로 묻는 것보다 이득이 두 배였다. [PR, NAACL Findings 2025, 초록] https://arxiv.org/abs/2311.09469
  - 확인할지 말지를 정보 가치로 판단한다. Paek & Horvitz [PR, 초록] https://www.erichorvitz.com/grounding.htm
- 시점에 관한 연구:
  - Iqbal & Bailey, CHI 2008 [PR, 초록]: 작업의 끊김 지점으로 미룬 알림이 덜 답답했다. https://chi2008.acm.org/program/122.html
  - Mehrotra 외, CHI 2016 [PR, 전문]: 유용한 내용도 방해가 된다. https://discovery.ucl.ac.uk/1502364/1/CHI16.pdf
  - Fitz 외, 2019 [PR, 초록]: 하루 세 번 묶어 보내면 통제감이 높았고, 알림이 아예 없으면 불안이 컸다. https://scholars.duke.edu/publication/1402953
- 그라운딩. Clark & Brennan 1991 [PR, 전문]. 이해했다는 가장 좋은 증거는 그에 맞는 다음 발화다. 양쪽 모두의 수고를 최소로 하려 한다. https://web.stanford.edu/%7Eclark/1990s/Clark,%20H.H.%20_%20Brennan,%20S.E.%20_Grounding%20in%20communication_%201991.pdf

### 3.4 출처와 기억 오염

- OWASP Agentic Top 10 2026, ASI06 "Memory & Context Poisoning" [STD, 전문]. https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
  - 출처를 기록한다.
  - 세션과 맥락을 분리한다.
  - 에이전트가 만든 출력을 신뢰하는 기억에 다시 넣지 않는다.
  - 되돌릴 수 있게 한다.
- Google, *Secure AI Agents* [VD, 전문]: 같은 사용자라도 맥락 사이에 기억을 분리하고, 사용자가 기억을 보고 통제할 수 있어야 한다. https://services.google.com/fh/files/misc/googles_approach_for_secure_ai_agents.pdf
- Microsoft, *AI Recommendation Poisoning* (2026-02) [VD]: 사용자 지시와 외부 내용을 구분해야 한다. https://www.microsoft.com/en-us/security/blog/2026/02/10/ai-recommendation-poisoning/

### 3.5 비서 실무

전문 비서가 무엇을 조용히 처리하고 무엇을 올리는지에 대한 실증 연구는 IBM의 CSCW 2008 하나뿐이었고, 초록만 확인했다. 나머지는 실무자 의견이다. 이 영역은 근거로 쓰지 않고 제품 선택으로 남긴다.

## 4. 종합: Still에 적용할 규칙

| 규칙 | 근거 | Still에서 쓸 기존 장치 |
|---|---|---|
| 소유자가 직접 한 말만 기억이 된다. 평가·스크립트·검증 요청은 답하지만 기억하지 않는다 | OWASP ASI06, Google, Microsoft, Gemini 임시 채팅, Mem0 이력 | `owner_typed`, upkeep·dream 선택 |
| 기억을 끝낼 때는 근거가 있어야 한다. 같은 사실을 다시 적었거나 소유자의 말이 바꿨을 때만 끝내고, 확신이 없으면 보류한다 | Bae 2022, Zep/Mem0, ForgetMeNot, Amershi G14 | #220 append + invalidate, 25% 상한 |
| 정리는 조용히 하고, 내 기록에서 볼 수 있게 한다 | Letta, LangMem, Mem0, Mehrotra 2016 | 내 기록, `retract_memory` |
| 소유자가 의지하는 사실이 바뀌면 다음 대화의 관련 있는 순간에 한 번 말한다 | Iqbal & Bailey, Horvitz, Amershi G3, Zhang 2025 | `owner_acts`, ATTN-WAIT-01, `watch_notification_needed` 같은 판단 |
| 확실하지 않은 것만, 이유를 붙여 한 번 묻는다 | SIGIR 2019, TOIS 2023, NAACL 2025, Paek & Horvitz | `owner_act_answer` |
| 알릴 때는 바뀐 종류를 쉬운 말로 말한다(새로, 바뀜, 합침, 끝냄) | Bae 2022, Zhang 2025 | `messages.json` |
| 기억은 써서 보여준다. 지나가듯, 바꿔 말해서 쓴다 | Clark & Brennan, HAI 2023, Lee 2012, Amershi G12 | 프로필 스냅샷, 공통 답변 지침 |
| 추론하거나 오래된 기억은 단정하지 않는다 | ForgetMeNot, Monteiro 2026, 역할 계약(추론을 사실처럼 말하지 않기) | 스냅샷의 출처와 날짜 |
| 하루 기록은 사실의 근거가 아니며, 알리지 않는다 | OWASP(자기 출력 재투입 금지) | `diary.<date>` 검색 전용 |

**Adopt / Adapt / Build.** Adopt다. Presence 원칙 7·8과 역할 계약의 소통 규칙이 이미 같은 방향을 말한다. #402와 #918의 전달 방식만 그 원칙에 맞게 고친다. 새 저장소, 스케줄러, 외부 프레임워크는 필요 없다.

## 5. 남은 질문(소유자 결정)

1. 대화 중 저장 알림(#918)도 기억을 써서 보여주는 쪽으로 더 옮길까, 아니면 종류만 밝힌 짧은 알림으로 남길까?
2. 다음 대화에서 꺼낼 항목은 7일이 지나면 그대로 만료해도 될까?
3. 밤사이 정리 결과를 원하면 아침에 묶어서 보는 선택지를 줄까? Fitz 2019에 따르면 묶어 보내기는 도움이 되지만, 이번 사례에서는 기본값이 아니다.

## 6. 한계

- 인터뷰 연구 대부분은 18~20명 규모이고, 일부는 심사 전 논문이다. 방향을 잡는 근거로 쓰되, 효과의 크기를 주장하지 않는다.
- 제품 동작은 공개 문서와 기사로만 확인했고, 직접 관찰하지 않았다.
- 비서 실무 근거는 약하다.
