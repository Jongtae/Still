# OP-02 Operating Readiness Remediation Contract

## Correction and outcome

OP-01 remains historical evidence of its then-passing static checks. It did not prove its readiness claim: release tag `v1.0.4` predates the subscription-engine stabilization and preflight changes, and the Compose image contained no supported subscription-engine executable or durable official-login location.

OP-02 is historical remediation evidence, not a deployment-ready closeout. It supplied the isolated-engine architecture and limited default-deny egress proxy; TOP-01 and TOP-02 add later lifecycle and configured-connection evidence. Preflight `ready` means that architecture and credential-free product fixtures are ready; it does not mean that owner operating configuration or live access is ready. Only TOP-03 may name a reproducible immutable deployment candidate after current verification. Until then, neither `v1.0.4` nor a current branch is a supported owner deployment candidate. This work does not configure an owner allowlist, build an operating Docker image, perform an owner deployment, configure credentials, log in to a provider, or prove live provider execution.

## Supported execution path

The isolated-engine architecture now exists. Compose declares a separate `engine` service and dedicated engine-profile volume; the service has no owner-state mount or host port, runs with a read-only filesystem and dropped capabilities, and shares only an internal network with Still. Still sends one capability-bound task through the gateway. The sidecar launches pinned Codex in an empty working directory and read-only sandbox, and its authenticated MCP callback exposes only `list_notes`. A host CLI or same-container CLI is not a supported path.

The engine has no direct external network. Its HTTP and HTTPS proxy settings point to a separate limited egress proxy that alone joins `engine-internal` and `provider-egress`. The proxy supports only HTTP `CONNECT`, accepts only exact allowlisted DNS hostnames on port 443, rejects IP literals and all other methods or ports, applies connection, idle, and byte bounds, and does not log request lines, headers, or tunneled bytes. A missing or empty owner allowlist denies every destination by default.

Preflight now recognizes this narrow policy and reports `ready` when all architecture checks and credential-free product fixtures pass. It separately reports `provider_egress_allowlist_configured: false` and `provider_egress_reachable: false` when no owner allowlist is present. Those observations are deferred operating gates, not architecture failures. They do not prove an image build, official login, provider reachability, or live inference.

## Ordered prepared procedure

1. Run `python3 scripts/operating_preflight.py --root .`. On the current architecture it reports `ready` after the structural checks and credential-free product fixtures pass. With no owner allowlist it also reports the deferred gate `owner provider egress allowlist operating configuration` and keeps `provider_egress_reachable` false.
2. The local-only Compose service, data-volume lifecycle, health/stop path, and secret-free recovery behavior are product-path tested in a temporary isolated store.
3. Only after isolated-engine acceptance and a closeout commit are recorded may the owner procedure be finalized as: check out that immutable commit; approve and configure the exact provider-host allowlist; build and start Compose; claim locally; explicitly approve and complete the isolated engine's official login; verify the live provider separately; and optionally configure Telegram separately. None of those operating results is claimed now.
4. Restore remains stopped-only and empty-target-only. It must never overwrite data, replay unfinished work automatically, copy the engine profile, or recreate credentials.

## Product-path preflight

Preflight validates product behavior rather than text alone. In a disposable isolated store it verifies first claim, local service health and clean stop, an actual gateway-to-sidecar fixture round trip through the authenticated read-only MCP callback, archive/restore exclusion of engine material, quarantine of unfinished work, and duplicate-safe queue behavior. It also checks the separate service, profile, owner-state exclusion, internal network, read-only service, configured gateway/callback structure, and limited egress-proxy structure. It must fail closed with a named recovery action for each failed architecture or product check. A `ready` result certifies only those checks; allowlist configuration and provider reachability remain reported separately and do not determine fixture readiness.

External provider access and official CLI authentication are not exercised or represented as successful. Tests may never use an owner credential. The fixture Codex process exercises Still's real gateway, sidecar command boundary, read-only MCP bridge, queue, evidence, and restore code without an external network call. This is development evidence only; it is not a Docker image build, official login, or live provider result.

## Boundaries and non-goals

No host home directory, Docker socket, arbitrary host mount, public endpoint, broad network capability, credential/OAuth entry, external connection activation, Telegram setup, operating deployment, new connector, marketplace, or A2A capability is added. The only persistent engine location is the dedicated internal named volume. The added provider-egress path is limited to the default-deny CONNECT proxy; the exact hostname allowlist must be explicit and owner-approved at operating time. Credential entry and all external activation remain later owner decisions.

## Completion evidence

Completion requires regression tests for the v1.0.4 candidate defect, missing engine path, isolated-engine authentication boundary, default-deny limited external-provider egress policy, startup/health, first claim, selected-engine bounded execution/tool round trip, stop/start, secret-free restore, and duplicate terminal work. The goal remains active through independent Terra version/path review, Sol implementation, and Astra recovery/security review. CI, merge, and tracker/roadmap/ledger closeout must reference the exact merge commit. Only then may the plan enter `requires-explicit-owner-approval`; owner allowlist configuration, Docker image build, official login, provider reachability, and live provider evidence remain separate owner-approved operating evidence.
