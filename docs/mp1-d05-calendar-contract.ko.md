# MP1 D-05 — Google Calendar Create-Event Approval Contract

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d05-calendar-contract.en.md)을 따른다.

## 범위

I-05는 mock 기반 adapter로 소유자의 primary Google Calendar에 새 event 하나만 만든다. Drive와 분리된 Calendar write scope를 요청하지만 소유자가 정확한 event payload를 미리 보고 승인한 뒤에만 실행한다. 수정, 삭제, 초대, 참석자 조회, 충돌 해결, 자동 일정 관리는 범위 밖이다.

## 흐름과 보안

Assistant는 summary, start/end, timezone, location, description, primary-calendar target을 담은 로컬 draft를 만든다. Still은 timestamp를 검증하고 canonical payload hash를 계산한다. Redacted preview를 보여주고 owner-bound one-time approval record를 만든다. 일치하는 approval, payload hash, owner identity, 만료되지 않은 요청만 `create`를 호출할 수 있다.

Adapter는 approval ID와 payload hash에서 나온 idempotency key와 최소 event payload를 Calendar mock에 보낸다. 반복 제출은 같은 로컬 결과를 반환하며 두 번째 event를 만들지 않는다. Token은 private connection store에만 남고 preview, approval, audit event, export에는 포함되지 않는다. scope 누락, 만료 approval, 변경된 payload, 다른 owner, malformed response, provider timeout은 fail closed하며 redacted recovery evidence를 남긴다.

## API와 저장

`CalendarCreate`는 `draft(input, owner_id)`, `preview(draft_id, owner_id)`, `approve(draft_id, owner_id)`, `create(draft_id, approval_id, owner_id)`, `status(draft_id, owner_id)`를 제공한다. 상태는 `awaiting-approval`, `approved`, `created`, `failed`, `expired`이며 terminal `created`는 바뀌지 않는다. 정책 소유 orchestrator만 승인과 생성을 위한 service/channel 진입점이 된다. 소유자 로컬 record는 canonical draft hash, 단발 approval ID, idempotency key, result metadata, redacted error class를 보관하고, portable export에는 내용이 없는 복구 metadata만 남긴다.

## Fixture와 자동 acceptance

Fixture는 valid preview/create, approval 없음, 다른 소유자 승인, 만료 승인, 변경된 draft/hash, 중복 submit, OAuth scope 거절, malformed provider result, timeout, 승인 전 write 없음을 다룬다. Test는 로컬 Calendar mock만 사용하며 유효 승인 뒤 정확히 한 번의 `POST /calendars/primary/events` 요청을 증명한다.

MP1 개발에는 live Calendar account, OAuth client, 수동 acceptance가 필요 없다. 운영 설정은 저장소 개발 거버넌스에 따라 MP1 뒤에 한다.

## 비목표

Calendar 수정/삭제, email, invitation, attendee, public calendar, recurring event, arbitrary calendar, 자동 외부 행동은 범위 밖이다.
