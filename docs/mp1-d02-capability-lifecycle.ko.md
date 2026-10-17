# MP1 D-02 — Capability Lifecycle 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](mp1-d02-capability-lifecycle.en.md)을 따른다.

## 사용자 결과와 범위

소유자는 검토된 소수의 MCP 도구, A2A Agent, 격리 runtime 목록을 보고 하나를 enable, pause, disconnect할 수 있다. I-02는 공개 URL, marketplace, 임의 설치, 자동 활성화를 추가하지 않는다.

## 계약

검토 descriptor에는 불변 id, kind, 선언 도구, 최소 scope, 고정 version, publisher 검토 기록, health-check 계약이 있다. 소유자 상태에는 descriptor id, lifecycle 상태, grant 참조, 시각, redacted audit event만 저장한다. credential은 private connection store에 남고 export, engine, audit view에 나타나지 않는다.

상태는 available, connected-disabled, enabled, paused, auth-required, error, disconnected이다. enable에는 정확한 scope의 소유자 승인이 필요하다. pause는 새 호출을 멈춘다. disconnect는 grant와 secret을 폐기하되 redacted audit metadata를 남긴다.

## 보안과 acceptance

Descriptor는 allowlist와 고정 version을 사용한다. Engine은 선언 도구와 승인된 최소 맥락만 받는다. Health check는 지속 외부 행동을 할 수 없다. Audit은 payload, credential, path, message text를 제외한다. 어떤 상태도 shell, mount, Docker access, 무제한 network access를 부여하지 않는다.

Fixture는 각 kind, 누락 secret, 실패 health check, pause 거절, disconnect 복구를 다룬다. 자동 테스트는 상태 전이, 인증, export 제외, redaction을 다룬다. Named live acceptance는 소유자, 선택한 검토 항목, 상태 전이, health 결과, pause/disconnect 관찰, 복구 결과를 기록한다. 이 계약이 병합된 뒤에만 I-02를 시작한다.
