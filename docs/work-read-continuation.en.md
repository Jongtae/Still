# Continuing conversational Work from verified read progress

## Scope

Issue #1007 extends the restart boundary for a foreground Work that has a host-owned checkpoint from a supported read. The owner goal, Work identity, verified evidence, user corrections and cumulative WorkLedger remain authoritative. The owner's chosen AI decides what remains useful after reading a checkpoint. A completed read does not imply completion of the original goal. The current result judgment and delivery path still decide and record the Work outcome.

This first slice is a bounded public byte-range tool. It returns after a verified range and gives the model a Work-local Artifact reference. Progress is available within the ongoing turn and on an eligible restart of the original Work. A model may request the next bounded range or inspect verified content. Limits apply even if the same Work resumes. Independent async jobs, standing triggers, post-effect reconciliation and autonomous account use are separate from this slice (#972, #1000).

## Authority and recovery

The public-destination, pinned transport, no-secret and work-mode checks apply to every outgoing hop. The adapter sends anonymous GET with a single byte range. A strong validator, canonical source identity, range and length must match before host staging advances. Input URLs and validators never become model-facing checkpoint facts. A bounded preview is external data, and is subject to the normal outside-content and redaction path.

A checkpoint is host-owned state tied to the Work and its active epoch. Committed coverage and its cumulative SHA-256 digest are the only portion visible to later calls. Private staging accepts a host-generated name rather than a model path. The transaction checks the epoch and previous revision before it publishes a durable offset. If a write outlives its transaction, the next authorized writer discards its unpublished tail. This direct API slice uses a 1 MiB per-call ceiling, a 64 MiB per-Work ceiling, and 256 MiB of install-local staging. When room is required, the cache can reclaim orphan staging and staged bytes from terminal Works. Portable export strips the checkpoint metadata and does not transfer staging. The tool never modifies owner files, grants or external accounts. A replacement worker only needs the verified offset. The stored address remains local, and every resumed request revalidates it against current authority before handing it to the transport.

Recovery atomically fences stale executions. Only a supported checkpoint and accounted read-only history can requeue a foreground Work with the same identity. Stop, expiry, authority revocation, legacy rows, pending owner actions and unknown effects preserve existing behavior. The read receipt cannot resolve an earlier possible write. Keep all prior attempt and turn accounting. The existing Plan and outcome judgment consume bounded verified state under its original authority. No vendor, scenario or domain method appears in core.

## Verification

Use #826's production recovery and transaction fixtures, with an injected public-range transport and fake clocks. Interrupt after a committed range, fence the old worker, and resume at the verified offset on the original Work. Compare output with uninterrupted execution. Reject stale commits, changed source, truncated ranges, unsafe targets, expired or stopped work and replay after an ambiguous external action. Verify a fresh planning turn receives milestone evidence as a host observation rather than new owner input. Report model-free and model operating evidence separately.

## Reuse

Adapt the documented long-running Activity/Signal and LangGraph checkpoint patterns to existing local SQLite, WorkLedger, queue and judgment. Adopt RFC 9110 Range and If-Range, the installed Python HTTP/OS facilities and the existing pinned transport. This adds no dependency or service. See #1007 for the complete Existing Solutions Review.
