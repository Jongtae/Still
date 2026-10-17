# 구독 엔진 Telegram 안정화 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](subscription-telegram-stabilization-contract.en.md)을 따른다.

## 목표와 범위

이미 소유자가 확인한 구독 엔진을 선택한 소유자는 paired Telegram 대화에서 승인된 개인 메모의 요약을 요청할 수 있다. Still은 정확히 승인된 메모와 선언된 도구만 bounded engine turn에 제공하고, 결과와 redacted 실행 근거를 보존하며, 거절·timeout·malformed output·재시작·중복 전달 뒤에도 안전하게 복구한다.

이 안정화 사이클은 provider credential, OAuth 구성, live external activation, connector catalogue, A2A capability, permission scope, host mount, arbitrary shell access, broad network access을 변경하지 않는다.

## 입력, 승인, 정책 경계

Paired Telegram 소유자는 명시적인 `/summarize` command를 호출한다. 요청은 paired channel, chat, queue job, 선택된 engine에 묶인다. Still은 해당 owner runtime에 이미 저장된 note만 읽으며, memory를 새로 만들거나 다른 runtime을 열거하거나 arbitrary context를 붙이거나 선택된 bounded engine 외 provider에 note를 보내지 않는다.

Engine input은 원래 command만이 아니라 승인된 note 요약 instruction과 bounded note payload를 포함해야 한다. Note는 untrusted data이므로 그 안의 지시는 실행되지 않는다. Engine은 store handle, home directory, credential, raw data path, document root, arbitrary environment를 받지 않는다.

## 선언된 도구 왕복

Still은 per-turn MCP bridge를 통해 `list_notes`, `save_note`, `web_search`만 노출한다. Engine configuration은 반드시 그 bridge를 참조한다. 모든 tool call은 Still이 검증하고 기존 capability facade를 통해 실행하며, queue job 아래 redacted start/terminal evidence를 기록한다. Bridge는 per-turn private local endpoint를 가지며 temporary run directory가 제거되기 전에 닫힌다.

Codex는 명시적인 per-turn MCP configuration override를 받고 Claude Code는 strict MCP configuration을 유지한다. 지원하지 않는 engine은 tool support를 주장하지 않는다. Fixture engine은 실제 request → declared tool invocation → result response round trip을 보여야 하며, tool-list 존재나 fabricated final answer만으로는 충분하지 않다.

## 결과, 실패, 복구

Terminal result는 job과 하나의 assistant message에 저장된다. Telegram delivery는 하나의 terminal bubble로 유지한다. 거절된 command, timeout, malformed engine output, tool error, bridge protocol error는 redacted failed job과 named recovery action을 만들며 external engine을 암묵적으로 재시도하지 않는다.

Restart recovery는 기존 queue와 delivery semantics를 보존한다. Job은 한 번만 claim되고 duplicate Telegram update는 기존 request key를 유지하며 terminal job은 engine 또는 tool bridge를 다시 실행할 수 없다. Evidence에는 engine lifecycle과 declared tool lifecycle을 기록하되 note body, prompt, credential, raw path, socket location, provider payload는 제외한다.

## 검증과 완료

Automated fixture는 수정 전 command-only prompt 결함, 수정 뒤 approved-note input, declared MCP configuration 사용, tool round trip, result/evidence persistence, 거절, timeout, malformed output, restart, duplicate suppression을 증명한다. CI는 complete pytest suite와 plan/document/ledger check를 실행한다. `validate` GitHub check는 `main`으로 가는 pull request의 필수 조건이다.

개발 완료는 mock/fixture evidence만 의미한다. Operating validation은 별도이며 owner-selected subscription engine과 paired Telegram deployment가 필요하다. 이 사이클은 둘 다 구성하거나 주장하지 않는다.
