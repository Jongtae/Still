# MP1 D-06 — Integrated ReAct Policy and Release Contract

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d06-react-release-contract.en.md)을 따른다.

## 정책

Personal Assistant Core는 제한된 ReAct loop를 따른다. 소유자 결과를 해석하고, 소유자 로컬 Personal Space를 살피고, enable된 검토 capability만 선택하고, redacted evidence를 관찰한 뒤 답변·승인 요청·복구 중 하나를 한다. Model 제안을 A2A 호출이나 Calendar 생성 권한으로 취급하지 않는다.

Drive는 읽기 전용 evidence에만 쓰고, A2A는 명시 위임 뒤에만 실행하며, Calendar는 로컬 draft를 만들고 D-05의 정확한 승인을 요구한다. capability가 없거나 paused/disconnected/invalid/timed-out/failed이면 꾸며낸 결과 대신 정직한 로컬 fallback과 recovery action을 제공한다.

## 통합 acceptance

Release fixture는 소유자 로컬 Personal Space evidence를 만들고, mocked Drive source를 얻고, compatibility peer에 명시 위임하고, mock Calendar event 하나를 draft/approve하고, capability를 pause/disconnect하며, 소유자 상태를 export/restore한다. Source redaction, secret export 없음, 승인 전 외부 write 없음, idempotent Calendar 결과 하나, 남은 recovery evidence를 검증한다.

MP1 release 완료는 자동 contract/fixture acceptance다. 실제 credential과 외부 활성화는 소유자 운영 모드 배포로 미루며 별도로 기록하고 PR별 수동 gate로 만들지 않는다.

## 비목표

자동 위임, Calendar 수정/삭제, email, marketplace, 공개 endpoint, arbitrary runtime, 실제 외부 서비스 설정 주장은 범위 밖이다.
