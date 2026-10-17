# MP1 D-02 — Capability Lifecycle Contract

## Outcome and scope

Owners review a small built-in catalogue of MCP tools, A2A agents, and isolated runtimes, then enable, pause, or disconnect one without broad host access. I-02 adds no public URL, marketplace, arbitrary install, or automatic activation.

## Contract

A reviewed descriptor has an immutable id, kind, declared tools, minimum scopes, pinned version, publisher review record, and health-check contract. Owner state stores descriptor id, lifecycle state, grant references, timestamps, and redacted audit events. Credentials remain in the private connection store and never appear in exports, engines, or audit views.

States are available, connected-disabled, enabled, paused, auth-required, error, and disconnected. Enable requires exact owner scope acknowledgement. Pause stops new calls. Disconnect revokes grants and secrets but keeps redacted audit metadata.

## Security and acceptance

Descriptors are allowlisted and pinned. Engines receive only declared tools and approved minimum context. Health checks cannot cause persistent external actions. Audit excludes payloads, credentials, paths, and message text. No state grants shell access, mounts, Docker access, or unrestricted network access.

Fixtures cover every kind, missing secrets, failed health checks, pause rejection, and disconnect recovery. Automated tests cover transitions, authentication, export exclusion, and redaction. Named live acceptance records owner, selected reviewed item, state transitions, health result, pause/disconnect observation, and recovery. I-02 begins only after this contract merges.
