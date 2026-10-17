# OP-02 운영 준비 정정 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](op-02-operating-readiness-remediation-contract.en.md)을 따른다.

## 정정과 결과

OP-01은 당시 통과한 정적 검사에 대한 과거 증거로 남는다. 그러나 준비 완료 주장을 증명하지 못했다. release tag `v1.0.4`는 subscription-engine stabilization과 preflight 변경보다 앞서며, Compose image에는 지원되는 subscription-engine executable이나 지속되는 공식 로그인 위치가 없었다.

OP-02는 historical remediation evidence이며 deployment-ready closeout이 아니다. isolated-engine architecture와 제한된 default-deny egress proxy를 제공했고 TOP-01과 TOP-02가 이후 lifecycle 및 configured-connection evidence를 더한다. preflight의 `ready`는 architecture와 credential-free product fixture가 준비되었다는 뜻이며 owner operating configuration이나 live access가 준비되었다는 뜻이 아니다. current verification 뒤 TOP-03만 재현 가능한 immutable deployment candidate를 명시할 수 있다. 그 전까지 `v1.0.4`나 현재 branch는 지원되는 owner deployment candidate가 아니다. 이 작업은 owner allowlist를 설정하거나 operating Docker image를 build하지 않으며 owner deployment, credential configure, provider login을 수행하지 않고 live provider execution을 증명하지 않는다.

## 지원 실행 경로

isolated-engine architecture는 이제 존재한다. Compose는 별도 `engine` service와 전용 engine-profile volume을 선언한다. 이 service에는 owner-state mount와 host port가 없고, read-only filesystem과 dropped capability로 실행되며, Still과 internal network만 공유한다. Still은 gateway를 통해 capability-bound task 하나를 보낸다. sidecar는 빈 working directory와 read-only sandbox에서 pinned Codex를 실행하며, authenticated MCP callback은 `list_notes`만 노출한다. host CLI나 same-container CLI는 지원 경로가 아니다.

engine에는 직접 external network가 없다. HTTP 및 HTTPS proxy 설정은 별도의 limited egress proxy를 가리키며 이 proxy만 `engine-internal`과 `provider-egress`에 함께 연결된다. proxy는 HTTP `CONNECT`만 지원하고 정확히 allowlist에 포함된 DNS hostname의 443 port만 허용하며 IP literal과 다른 모든 method 또는 port를 거부한다. 또한 connection, idle, byte bound를 적용하고 request line, header, tunneled byte를 log하지 않는다. owner allowlist가 없거나 비어 있으면 기본적으로 모든 destination을 거부한다.

이제 preflight는 이 narrow policy를 인정하고 모든 architecture check와 credential-free product fixture가 통과하면 `ready`를 보고한다. owner allowlist가 없으면 `provider_egress_allowlist_configured: false`와 `provider_egress_reachable: false`를 별도로 보고한다. 이 observation은 architecture failure가 아니라 보류된 operating gate다. image build, official login, provider reachability 또는 live inference를 증명하지 않는다.

## 준비된 순서 절차

1. `python3 scripts/operating_preflight.py --root .`를 실행한다. 현재 architecture에서는 structural check와 credential-free product fixture가 통과한 뒤 `ready`를 보고한다. owner allowlist가 없으면 보류 gate인 `owner provider egress allowlist operating configuration`도 보고하고 `provider_egress_reachable`은 false로 유지한다.
2. local-only Compose service, data-volume lifecycle, health/stop path, secret-free recovery behavior는 임시 격리 store에서 product-path test를 거친다.
3. isolated-engine acceptance가 완료되고 closeout commit이 기록된 뒤에만 owner procedure를 다음과 같이 확정할 수 있다. immutable commit checkout, 정확한 provider-host allowlist 승인 및 설정, Compose build와 start, local claim, isolated engine official login의 명시 승인과 실행, 별도 live provider 검증, 필요한 경우 별도 Telegram configure. 현재는 그 어떤 operating result도 주장하지 않는다.
4. restore는 stopped-only 및 empty-target-only를 유지한다. data overwrite, unfinished work 자동 replay, engine profile copy, credential recreate는 절대 하면 안 된다.

## 제품 경로 preflight

Preflight는 text가 아닌 product behavior를 검증한다. disposable isolated store에서 first claim, local service health와 clean stop, authenticated read-only MCP callback을 통과하는 실제 gateway-to-sidecar fixture round trip, archive/restore의 engine material exclusion, unfinished work quarantine, duplicate-safe queue behavior를 검증한다. 또한 separate service, profile, owner-state exclusion, internal network, read-only service, 설정된 gateway/callback 구조와 limited egress-proxy 구조를 검사한다. 각 failed architecture 또는 product check에는 named recovery action으로 fail closed해야 한다. `ready` 결과는 이 check만 보증한다. allowlist configuration과 provider reachability는 별도로 보고되며 fixture readiness를 결정하지 않는다.

외부 provider access와 공식 CLI authentication은 실행하지 않으며 성공한 것으로 표현하지 않는다. test는 소유자 credential을 사용할 수 없다. fixture Codex process는 외부 network call 없이 Still의 실제 gateway, sidecar command boundary, read-only MCP bridge, queue, evidence, restore code를 실행한다. 이는 development evidence일 뿐 Docker image build, official login, live provider result가 아니다.

## 경계와 비목표

host home directory, Docker socket, arbitrary host mount, public endpoint, broad network capability, credential/OAuth 입력, 외부 connection activation, Telegram setup, 운영 배포, 새 connector, marketplace, A2A capability를 추가하지 않는다. 유일한 persistent engine 위치는 전용 internal named volume이다. 추가된 provider-egress 경로는 default-deny CONNECT proxy로 제한된다. 정확한 hostname allowlist는 operating time에 명시적으로 owner 승인을 받아야 한다. credential 입력과 모든 외부 activation은 이후 owner decision으로 남는다.

## 완료 증거

완료에는 v1.0.4 candidate defect, missing engine path, isolated-engine authentication boundary, default-deny limited external-provider egress policy, startup/health, first claim, selected-engine bounded execution/tool round trip, stop/start, secret-free restore, duplicate terminal work에 대한 회귀 test가 필요하다. Goal은 독립 Terra version/path 검토, Sol 구현, Astra recovery/security 검토를 모두 통과할 때까지 active다. CI, merge, tracker/roadmap/ledger closeout은 정확한 merge commit을 참조해야 한다. 그 뒤에만 plan은 `requires-explicit-owner-approval`로 들어갈 수 있으며 owner allowlist configuration, Docker image build, official login, provider reachability, live provider evidence는 별도의 owner-approved operating evidence로 남는다.
