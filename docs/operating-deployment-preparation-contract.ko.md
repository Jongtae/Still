# 소유자 승인 운영 배포 준비 계약

> **과거 참고용 한국어 번역본.** 내부 개발 기준은 [영어 원본](operating-deployment-preparation-contract.en.md)을 따른다.

## 상태와 결과

`OP-01`과 `OP-02`는 과거 preparation/remediation evidence일 뿐이다. `v1.0.4`가 지원 candidate라는 과거 주장은 stabilization과 isolated-engine 작업보다 앞선 tag이므로 unsupported이며 배포하지 말아야 한다. TOP-01은 이후 credential-free Compose lifecycle을 증명했고 TOP-02는 configured isolated-Codex connection path를 정정했다. 지원되는 exact candidate는 current verification evidence가 기록된 TOP-03 closeout에서만 명시한다. architecture는 별도 isolated engine, 전용 profile, capability-limited read-only MCP boundary, default-deny limited CONNECT egress proxy를 선언한다. engine에는 직접 external network가 없고 proxy만 provider-egress network로 연결되며, 정확히 owner allowlist에 포함된 DNS hostname의 443 port만 허용한다.

결과물은 credential 없이 재현 가능한 준비 패키지이다. 조건부 설치·시작·설정·건강 검사·중단·복구 절차, machine-readable 로컬 preflight, 그리고 local product/failure state의 fixture 증거를 제공한다. preflight의 `ready`는 architecture와 product fixture가 준비되었다는 뜻이다. owner allowlist가 설정되었거나 Docker image가 build되었다는 주장이 아니며 Telegram, Codex, Claude Code, official login, provider reachability 또는 어느 live provider가 소유자에게 운영 중이라는 주장도 아니다. 이 항목들은 보류된 operating gate다.

## 순서가 있는 소유자 절차

1. **진단과 선택:** doctor를 포함한 checkout에서 `python3 scripts/agentos_doctor.py --root .`을 실행한다. 필요 시 `--candidate COMMIT`으로 목표 SHA를 지정할 수 있다. checkout, Docker/Compose 가용성, loopback port, free disk, Compose configuration, secret-free product preflight만 검사한다. container를 시작하거나 state를 수정하지 않고 credential을 읽거나 external service에 접속하지 않는다. `--candidate`가 현재 checkout과 다르면 정확한 안전 작업 흐름을 명시한 `blocked` report를 반환한다.
2. **ready doctor report 및 owner 승인 뒤에만 설정과 시작:** owner는 정확한 provider DNS-host allowlist를 승인하고 설정한 뒤 명시적으로 `docker compose up -d --build`를 실행한다. owner는 local port를 고르기 위해 `AGENTOS_PORT`를 설정할 수 있다. public host, host-home mount, Docker socket mount, credential environment variable은 이 path에 포함되지 않는다. 새 empty runtime에는 initial archive가 필요 없고 personal data가 생긴 뒤 secret-free backup을 만든다.
3. **승인된 start가 성공한 뒤에만 health 및 local claim:** owner는 `http://127.0.0.1:${AGENTOS_PORT:-8787}/healthz`가 `{"ok": true}`를 반환할 때까지 기다리고, local URL을 열어 새 runtime을 claim하며, UI 지시에 따라 생성된 local recovery material을 보관한다. 이 cycle은 그러한 operating result를 기록하지 않는다.
4. **deployment 승인, owner allowlist 설정, image build 뒤에만 credential 및 live-provider gate:** owner만 isolated subscription CLI를 선택하고 approved engine profile을 통해 official login을 완료한 뒤 provider reachability와 live execution을 별도로 검증한다. Telegram이 필요하면 owner는 전용 bot을 별도로 만들고 token을 local private connection store에 입력한 뒤 owner pairing을 완료한다. 이 gate는 preflight의 일부가 아니며 이 cycle은 allowlist, image build, login, token, OAuth client, endpoint, live inference, external connection을 만들거나 증명하지 않는다.
5. **중단과 복구:** named data volume을 보존하려면 `docker compose down`으로 중단한다. 파괴적인 volume 교체 전에는 `scripts/compose-backup.sh ARCHIVE.tar.gz`를 사용한다. 서비스를 중단하고 빈 data volume으로 교체한 뒤 `scripts/compose-restore.sh ARCHIVE.tar.gz`를 실행하고 다시 시작한다. portable restore는 credential, session, local-folder grant, engine selection, Telegram pairing을 의도적으로 제외하므로 소유자가 복원된 runtime을 claim하고 다시 연결해야 한다.

## 건강, 실패, 복구 계약

`/healthz`는 유일한 unauthenticated local health endpoint이다. Compose는 이 endpoint에 닿지 못하면 health check가 실패해야 한다. preflight는 fail-closed다. version mismatch, non-loopback port, missing named volume, missing non-root runtime, missing health check, missing recovery script, missing isolated-engine boundary, missing limited engine-egress policy는 이름 있는 recovery action을 가진 non-ready 결과다. owner allowlist가 없으면 빈 policy가 모든 destination을 안전하게 거부하므로 대신 보류된 operating gate가 된다. preflight는 Docker 작업, credential read, 외부 provider call을 수행하지 않는다. 유일한 network activity는 temporary AgentOS와 engine HTTP server 사이의 loopback fixture round trip이다.

지원되는 중단/복구 경로는 `down` 뒤에도 named volume을 보존하며 AgentOS가 실행 중이면 restore를 거절한다. backup과 portable restore는 계약상 secret-free다. 중단되었거나 불확실한 작업은 owner-local queue에 남아 기존 recovery surface로 처리된다. 이 준비 cycle은 engine 재시도나 Telegram 작업 재전송을 하지 않는다.

## 위협 및 데이터 경계

AgentOS container는 전용 `agentos` 사용자로 실행되며 내부 `/state` owner volume만 받는다. 별도 engine service는 `/engine-profile`만 받고 owner-state mount, host port, 직접 provider-egress network가 없으며, read-only filesystem과 read-only Codex sandbox를 사용하고 internal gateway 및 authenticated `list_notes`-only MCP callback을 통해 AgentOS와 통신한다. HTTP(S) proxy 설정은 443 port의 정확히 allowlist에 포함된 DNS name에 대한 `CONNECT`만 지원하는 별도 proxy를 통과하도록 강제한다. 설정이 없거나 비어 있으면 모든 destination을 거부한다. proxy는 IP literal, 다른 method, 다른 port를 거부하고 tunnel에 bound를 적용하며 request line, header, tunneled byte를 log하지 않는다. host에는 loopback AgentOS port 하나와 backup/restore 중 owner-chosen archive directory만 노출된다. host home, arbitrary host directory, Docker socket, public tunnel, broad network policy, credential, live provider payload는 추가하지 않는다.

preflight report에는 version, 구조적 readiness boolean, recovery identifier만 있다. owner data, private connection file, environment secret, 외부 CLI credential은 읽지 않는다.

## 자동 증거와 보류된 운영 증거

fixture는 isolated gateway/sidecar/read-only-MCP product path, version mismatch, unsafe binding, missing health check, missing runtime user, recovery behavior, named missing-egress-policy blocker, default-deny limited proxy policy를 다룬다. Recovery fixture는 incomplete work quarantine, automatic replay 거부, duplicate safety, portable restore의 engine profile/configuration 제외를 계속 검증한다. 저장소 CI는 기존 plan/document/ledger 검사와 전체 test suite에 이 suite를 포함한다. Static Compose check와 fixture execution은 development evidence이며 owner allowlist, Docker image build, official login, provider reachability 또는 owner operating evidence를 주장하지 않는다.

운영 증거는 의도적으로 보류된다. OP-02 closeout과 명시적인 owner 승인 뒤에 owner는 실제 선택한 release, 정확한 provider-host allowlist, Docker image build/start, local health 결과, official login, provider reachability/live execution, 별도로 설정한 connection health를 기록할 수 있다. 그 전에는 preflight readiness는 architecture-and-fixture readiness일 뿐 *deployment-ready*도 *operating-configured*도 아니다.

## 비목표

OP-01과 현재 remediation preparation은 owner provider allowlist를 설정하거나 Docker를 install/build하거나 subscription CLI를 설치하지 않고, credential 입력, official login, OAuth 설정, Telegram bot 생성, external connection activation/test, live provider inference, public endpoint 배포, connector/A2A peer/marketplace capability 추가, 무관한 UI 변경을 수행하지 않는다.
