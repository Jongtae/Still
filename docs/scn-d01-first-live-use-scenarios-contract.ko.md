# SCN-D-01 — 첫 실제 사용 시나리오 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](scn-d01-first-live-use-scenarios-contract.en.md)을 따른다.

## 목표와 범위

이 계약은 소유자가 의도적으로 수행하는 두 Still 여정을 정의한다. 이는 설계 계약일 뿐이다. 로컬·출처 표시·승인 경로와 구현 증거를 정하지만 provider 구성, 로그인, connection 생성, 메시지 전송 또는 외부 서비스의 실사용을 주장하지 않는다.

시나리오 1은 **조사에서 실행까지**다. 소유자는 paired Telegram에서 미팅 준비를 요청하고, 선택적으로 관련 Google Drive context를 승인하고, 출처가 있는 brief를 받으며, 하나의 Calendar event 생성 draft를 승인할 수 있다.

시나리오 2는 **검토 후 공유**다. 소유자는 뉴스, 블로그, 동영상 또는 그 bounded text/transcript를 제공하고, 번역 또는 요약을 선택하며, KakaoTalk 수신자나 대화방을 지정하고, 정확한 발송 문구를 검토한 뒤 하나의 전송을 별도로 승인한다.

## 시나리오 1 — 조사에서 실행까지

소유자가 미팅 준비 요청을 보낸다. Still은 provider access를 암시하지 않고 요청을 확인하며, source label이 있는 eligible read-only context만 제안하고, 정확한 context selection의 소유자 승인을 기다린다. 출처가 있는 Drive reference와 local result reference를 포함하는 간결한 brief를 생성한다. event 생성을 요청하면 canonical Calendar draft(제목, 시간, timezone, 참석자, 설명)를 표시하고 별도의 1회성 생성 승인을 요구한다.

권한 부재, Drive/Calendar capability 미사용 가능, source 만료, 잘못된 시간, 모호한 timezone, 중복 승인, 취소, provider failure는 redacted recovery state로 이름 붙여 반환한다. 미팅이 언급되었다고 source를 조용히 대체하거나 context를 external engine으로 보내거나 event를 만들지 않는다.

## 시나리오 2 — 검토 후 공유

소유자는 content 또는 허용된 bounded source reference를 명시적으로 제공하고 `summarize` 또는 `translate`를 고른다. Content는 untrusted evidence이므로 policy를 무시하거나 수신자를 선택하거나 tool use를 요청하거나 전송을 일으킬 수 없다. 생성 draft에는 source evidence, 요청한 언어/형식, redaction 결과, stable draft reference가 포함된다. 소유자가 시작한 revision만 편집할 수 있고 그 경우 이전 승인은 무효가 된다.

수신자 resolution은 capability가 소유하는 별도 preview다. 소유자는 정확한 KakaoTalk 대화방 label 또는 사람 이름을 제공할 수 있다. 승인된 단 하나의 exact resolution만 preview할 수 있다. 일치 없음·부분 일치·다중 일치는 관련 없는 대화 상세를 노출하지 않고 disambiguation을 요청해야 한다. Preview는 정확한 destination label, 변경 불가능한 정확한 outgoing text, 1회성 final-send action을 보여준다. draft/destination 변경, 만료, 승인 replay, 취소, capability 미사용 가능, uncertain outcome은 이 action을 무효화한다.

구현은 offline fallback을 제공해야 한다. 검토한 메시지와 destination label, 소유자 수동 전송 안내를 copy/export한다. 이 fallback은 KakaoTalk 수신 증거가 아니다. 별도 승인된 KakaoTalk capability가 모호하지 않은 delivery result를 반환할 때만 provider receipt를 기록할 수 있다. 그렇지 않으면 terminal state는 `not-sent`, `failed`, 또는 `delivery-uncertain`이며 `delivered`라고 하지 않는다.

## 권한, 개인정보, 외부 경계

Still은 local state, source evidence, draft, approval, terminal outcome, recovery record를 보유한다. Export나 log에 raw credential을 저장하지 않으며 state transition 증명에 필요한 redacted audit metadata만 기록한다. 수신자 이름, 대화방 label, source content, outgoing text는 personal data다. 기본적으로 local에만 두고 UI rendering을 bounded하게 하며 portable evidence에서 제외하고, preview 생성만으로 외부에 공유하지 않는다.

이 설계는 비공식 KakaoTalk history scraping, background capture, automatic recipient inference, automatic messaging, arbitrary web retrieval, contact enumeration, broad host access, policy owner를 우회하는 sender를 제외한다. 미래 KakaoTalk 구현은 검토되고 허용된 capability를 사용해야 하며 별도의 명시적인 connection, scope, credential, operating-mode authority가 필요하다.

## 상태, 감사, 복구 계약

두 여정은 owner-and-channel-bound state transition을 사용한다: `requested` → `context-preview`(해당할 때) → `draft-ready` → `action-preview` → `approved-once` → terminal state. Terminal state는 `completed`, `cancelled`, `rejected`, `failed`, `not-sent`, `delivery-uncertain`이다. 어떤 adapter도 더 성공적인 상태를 지어낼 수 없다.

Audit는 opaque request/draft/action reference, source/capability category, approval/terminal timestamp, redacted recovery class를 보관한다. message body, recipient identity, conversation history, raw source content, token, authorization material, provider payload는 제외한다. Restore는 승인된 external action을 replay하지 않는다. 불완전하거나 uncertain한 request는 새 preview로 돌아간다.

## SCN-I-01 진입 계약

SCN-I-01은 이 영어 원본 계약이 merge되고 #301이 close되며 영어 원본 contract/plan check가 통과하고 새 goal-ready implementation issue가 생성된 뒤에만 활성화할 수 있다. owner/channel binding, source eligibility/attribution, summary/translation draft generation, recipient ambiguity, exact-preview binding, final-approval one-time use, duplicate/cancel/expiry, unavailable capability, send failure, delivery uncertainty, audit/export redaction, restore/no-replay, Telegram/local-companion parity에 대한 deterministic fixture를 구현해야 한다. 별도 승인된 operating-mode evidence 없이는 실제 KakaoTalk integration을 활성화하거나 운영해서는 안 된다.
