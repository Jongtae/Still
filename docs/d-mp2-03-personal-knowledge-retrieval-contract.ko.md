# D-MP2-03 — 소유자 로컬 개인 지식 Retrieval 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](d-mp2-03-personal-knowledge-retrieval-contract.en.md)을 따른다.

## 목표와 범위

Still은 소유자가 이미 owner-local Personal Space에 저장한 memory, workspace result, 명시 승인된 local-context metadata를 투명한 source evidence와 함께 찾게 한다. Retrieval은 기존 소유자 data의 bounded read이며 automatic memory 생성, document ingestion, cloud synchronization, external search가 아니다.

이 설계는 vector service, provider, OAuth credential, endpoint, external action, operating deployment을 추가하지 않는다.

## 소유자 결과와 source selection

소유자는 “이 프로젝트에 대해 무엇을 저장했지?”처럼 bounded retrieval 질문을 한다. 정책 소유자는 해당 runtime 소유 record만 선택하고 source type, opaque local reference, capture/result time, match reason과 짧은 excerpt를 돌려준다. Context content는 capture됐다는 이유만으로 반환하지 않으며, 이후 별도 승인된 context-use contract가 있기 전에는 approved sharing state와 redacted metadata만 대상이다.

model instruction, Telegram message, web request는 다른 소유자의 data를 열거하거나 raw path를 검색하거나 새 memory를 만들거나 retrieval을 external sharing으로 바꿀 수 없다.

## Ranking과 read model

`PersonalKnowledgeOrchestrator`가 유일한 정책 소유자다. owner ID, channel, bounded query term을 받고 normalized term exact match, recency, stable opaque ID, result type 순서로 결정적으로 ranking한다. data embedding, model 호출, remote index는 하지 않는다.

read model에는 source label(`memory`, `workspace-result`, `approved-context-metadata`), opaque reference, bounded excerpt 또는 redacted metadata, timestamp, match reason, retrieval-safe recovery label이 있다. password, token, raw local path, full message history, unapproved context content, provider payload, approval material, internal ranking score는 제외한다.

## Owner, channel, sharing boundary

모든 요청은 authenticated owner에 묶인다. HTTP, paired Telegram, local companion은 같은 read operation을 호출하고 같은 source reference, ordering, redaction, error class를 받는다. rendering은 달라도 adapter가 storage를 직접 query하지 않는다.

Retrieval 자체는 sharing을 승인하지 않는다. 이후 assistant action이 selected result를 external engine/Agent에 보내려면 별도 정책의 exact owner-bound sharing preview를 만들어야 한다. 이 contract는 local evidence만 돌려주며 implicit retry나 external fallback이 없다.

## Audit, export, restore, 위협 모델

owner-local audit에는 timestamp, owner-scoped opaque retrieval reference, source category count, terminal state, redacted error/recovery class만 보관한다. query text, excerpt, runtime 밖에서 correlation 가능한 record ID, channel message body는 제외한다. portable export는 safe terminal category evidence만 보존하며 pending selection, unapproved context material, reconstructed retrieval history를 export하지 않는다.

구현은 invalid owner/channel binding, ambiguous/empty query, expired/deleted source, retrieved content의 prompt injection, replayed sharing proposal, 정책 소유자 우회를 fail closed해야 한다. missing source는 substitute 대신 recovery-safe result를 낸다.

## I-MP2-03 진입 계약

I-MP2-03는 이 design PR merge, issue close, 영어 원본 문서/plan/ledger check, 새 goal-ready issue 뒤에만 활성화한다. fixture-backed deterministic owner-local retrieval, source evidence/redaction, audit/export/restore safety, HTTP/Telegram/local-companion parity를 구현해야 한다. external index/provider/connection/credential/OAuth/automatic long-term memory/document ingestion/external sharing-action/operating-mode behavior는 모두 제외한다.
