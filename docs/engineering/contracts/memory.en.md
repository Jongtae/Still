# Memory and learning contract

This is the current reading of the Memory, upkeep, working-notes and dream rules formerly interleaved in the [Secretary Agency Contract](../../secretary-agency-contract.en.md). It consolidates the explicitly adopted changes; it does not grant new authority, select work or claim that every requirement has live operating evidence. [Current Context](../../current-context-contract.en.md) remains authoritative for temporary, source-bound state. [Presence](../../presence-experience-contract.en.md) governs presentation where it does not alter these rules.

The source baseline is `6edc3380b613ab0c2897ba7a913ab658150a68f4`, reviewed on 2026-11-05. The [pre-consolidation contract](https://github.com/Jongtae/Still/blob/6edc3380b613ab0c2897ba7a913ab658150a68f4/docs/secretary-agency-contract.en.md) preserves the original wording and chronology. Issue numbers before the Still import retain their predecessor meaning. The two documented source/text discrepancies below are exposed, not silently resolved by an editorial change.

## 1. Distinct records and writers

| Record or writer | Current rule |
| --- | --- |
| Work-scoped context | Temporary conditions and observations do not automatically become durable owner facts. |
| Canonical Memory | Retains fact content, provenance, currentness and replacement history under Still ownership. Inference is not relabelled as an owner's statement. |
| Owner's own worker or upkeep | Saves accepted facts directly under #918 slice (a), with attribution and the applicable notice/undo path. |
| Third-party package or delegated specialist | Proposes a pending MemoryCandidate under Constitution C5; it does not acquire the owner's direct-write path. |
| Working notes | One bounded, versioned document about how to work for this owner, not another namespace of personal facts. |
| Daily record | A dated retrieval aid for owner-typed exchanges, not a durable fact source or a memory-notice item. |

The writer boundary is determined by the host declaration, including `Capabilities.third_party_memory_write`, not a package identifier that a manifest can claim. A candidate is never described as remembered before it is accepted. A role, mandate, skill or model confidence grants no write authority.

## 2. Direct saves, third-party candidates and secret refusal

For the owner's own worker, both direct API execution and the supported CLI relay use the same direct-save rule. A kept upkeep proposal also becomes current Memory, attributed to its Work. The superseded `explicit_memory_request` / `explicit_memory_fact` per-fact judgment, value-coverage gate and owner-AI candidate ask are not part of this path. Stated and inferred facts can both be saved; their provenance remains different. The request sentence itself is not a lasting fact. A same-key replacement retains the previous row as history rather than overwriting it.

Every writer, including a third-party candidate, passes the deterministic stored-secret and credential-shape check on key and content. A hit is refused as `secret-shaped-value`, without echoing the value. Upkeep records a dropped key only as a digest. Listing and searching Memory apply the same secret filter before returning content to a model.

Third-party candidates keep the exact approval path: one natural ask per Work, values rather than internal keys, approval bound to the exact displayed/current candidate, and no saved-state claim before acceptance. The web approve/reject route remains separate from direct owner-AI saves.

**Unresolved threat boundary.** An owner worker can be induced by untrusted source text to propose a bad fact. Attribution, a save record, notice and retraction do not prove prompt-injection resistance. The earlier owner decision deferred additional injection/exfiltration hardening; this consolidation neither implements it nor declares it solved.

## 3. Currentness, invalidation, expiry and forgetting

Under #220, new facts are appended and earlier facts are invalidated explicitly. An upkeep proposal may name at most three current owner `profile.` rows in `invalidates`, each by its unambiguous eight-character short ID and a reason. Working-note versions are excluded. An unknown, ambiguous or out-of-scope ID rejects the whole proposal. A kept change marks the earlier row `superseded`, with `invalidated_by`, `invalidation_reason` and `invalidated_at`; it does not delete it. `supersedes_key` names only the proposal's own key; another key's row is addressed by ID.

A proposed `valid_until` is the last day the fact holds. After that day the row is excluded from all model snapshots, including literal query matches. The record remains stored and is shown as ended; expiry is not physical deletion. A future ending is visible in the snapshot. A lifecycle update that adds/changes an ending or invalidates another row must not be discarded merely because its fact text matches an existing row.

Forgetting and deletion are different. Under #367, `forget` invalidates use and retains an undoable record; it is not automatically purged after seven days. `delete` erases the item according to its supported scope. Transcript targets can be named as `message:<id>`. A later current version must not be overwritten by undoing an older change. These local operations do not establish removal from a remote service or every backup.

The seven-day lifetime of a memory-notice button is **not** the lifetime of every forget receipt. The old #19 statement that a forgotten working-note version is purged after seven days was superseded by #367; the [current owner-model code](../../../src/personal_agent/owner_model.py) explicitly records that distinction.

## 4. Foreground notice and exact undo

A direct foreground save durably holds its `memory_saved` notice in the same transaction as the Memory row. The notice is released after the reply settles as sent or unknown; it must not duplicate the reply. The existing sweep releases notices left held by a crash. A notice whose own delivery was unknown or interrupted may be retold at most once rather than silently disappearing.

Facts from one Work are combined into one quiet notice, with a separate undo per fact and an undo-all action. A late upkeep save can join an earlier notice only through a confirmed edit; its binding and undo window are updated then. A refused edit, an already settled notice or a lost notice uses the separate upkeep notice path. The notice shows bounded, redacted values, not keys, and does not ask again whether to remember.

The notice distinguishes a replacement under the same key, other facts retired alongside it, and values restored by undo. In Korean those are respectively “전에는 …”, “함께 정리한 기억: …” and “다시 …”. An unrelated retired fact is not presented as the previous value of the new fact. A deleted earlier value is not reconstructed merely to complete the sentence.

A notice-button undo is bound to the owner, private chat, generation, sent message, Memory IDs and content digests, and is consumed once per fact. `QuickStore.retract_memory` acts only if the row is still current with the bound content. A changed fact is left untouched and the limitation is reported. Retraction keeps history, restores a still-superseded predecessor where available, and revokes approvals against that key. The button acts for seven days; expiry removes/refuses that button, not unrelated forget receipts. The web's exact-delete path and conversational forget operations retain their own semantics.

`save_memory` records `saved`, `state: current` and `auto_saved: true`; undo records a `memory_undo` event in the same transaction as retraction. That event is not a Work step. Information-use records show the save/undo and any restored value truthfully. Non-profile values written by a Work remain in that Work's redaction set across superseded and retracted states. The worker reads a successful own save as remembered, not as an unapproved candidate.

## 5. Shared background queue and budgets

Upkeep, self-review and dream reuse `owner_model_upkeep`, keyed by Work and run kind. There is no second scheduler, evaluator or canonical memory store. Typed outcome/relation markers determine eligibility; no request-specific keyword classifier is added.

The existing tick claims at most one pending row, atomically with checking that no Work is queued or running. Execution is off the foreground work thread and single-flight. No pending row means no model call. A claimed run is never replayed after a restart; a stale claim expires as interrupted. Pending upkeep is newest-first, with an hour-old pending row expiring without a call. A stale claimed row also has a one-hour expiry in the reviewed implementation.

The owner pause switch and rolling 24-hour call cap are checked before each call and write. Upkeep defaults to enabled with 20 calls per rolling day. Count calls that reached the transport, not provider-unavailable answers issued before transport; the adapter's sent evidence governs, with conservative accounting when it is absent. Pausing enqueues/runs nothing further; a spent cap or deadline stops further calls/writes and records the reason. The old rule reserving up to six calls for a proposal plus per-fact judgments is retired: those owner-AI judgments were removed by #918.

Ordinary upkeep/review starts no call after its five-minute run deadline and remains subject to each adapter timeout. Dream has its separately bounded two-pass window described below. Missing structured-judgment support is recorded as unavailable, not replaced by a semantic rule tree. Owner controls remain on the existing `GET /api/owner-model` and `POST /api/owner-model/request` interfaces and supported Settings surfaces.

## 6. Upkeep after an owner request

A worker-answered Work that settles succeeded or partial records one upkeep row in the same transaction. Failed, cancelled and unknown Works, rule/command replies, preparation runs and location continuations do not create ordinary upkeep. Self-review has separate triggers.

Under #423, background owner-fact sources are owner-typed conversation Works or API calls declared owner-typed under an owner session. Evals, scripts and verification calls may receive answers but do not become owner memory. The judgment can read an answer excerpt as model-stated context; it must not save assistant-authored content as the owner's words. Redact stored secrets, credential shapes and the relevant Work values before constructing bounded facts.

The Judgment AI proposes at most five facts with the existing category/kind/key/content/evidence fields and optional replacement/invalidation/expiry data. The categories include identity, place, routine, schedule, preference and the owner's stated plans. Current bounds and duplicate/lifecycle exceptions are enforced by `owner_model.validate`; the reviewed source limits content and evidence to 200 characters each. Reject malformed, out-of-enum, wrong-namespace, secret-bearing and duplicate proposals; working methods belong in notes, not `profile.working.` rows.

Kept proposals use the direct-save rule, not a new candidate or per-fact approval chain. Sensitive-category guidance in the prompt is guidance, not a claimed deterministic sensitivity detector. Preserve that distinction when describing safety.

An upkeep event records status, counts, deciding route/model, input lengths and applied outcomes, with dropped keys represented only by digests. It does not expose source texts or change the Work's completion state. Historical event rows retain the old verdict fields they actually contained; they are not rewritten to match today's schema.

## 7. Self-review and working notes

### Triggers and purpose

One self-review row is recorded for a worker-answered failed Work, a typed correction/retry relation from a later owner turn, or an explicitly recorded observation through `enqueue_self_review`. A setup need resolved by the owner is not automatically a miss. The ordinary exclusions for command-only replies, preparation runs and location continuations remain. A refused retry recorded as a reference is not a retry trigger. Additional triggers for the same Work do not create duplicate runs.

The Judgment AI reads bounded, redacted request/answer/outcome facts, recorded goal judgments, the relevant owner correction or observation, the profile and current notes. A software blocker is kept only as Evidence on the Work; it does not become an owner preference, an externally filed issue, a code change or new authority.

### One document, not accumulated lesson facts

Under #19, learned working methods revise one document in `procedures.NOTES_SLOT` / `procedure_versions`. The judgment returns no change or the whole revised document plus a one-line change explanation in the owner's language. It does not append separate `profile.working.<topic>` Memory facts.

A revision is refused whole, not cut, when it exceeds 12 points, 200 characters per point or 1,500 total characters; merely changes spacing, markers, case or point order; includes a blocker; omits a change explanation; adds the request sentence or an existing owner fact as a working method; contains a secret; repeats a forgotten version; or is based on notes not seen whole or changed during judgment. Pause is checked again before applying it. Third-party writers do not inherit this direct-write path.

A kept version and its notice are queued atomically. Only the still-current version is announced. Briefs carry the whole document with its version, `procedure:<id>` and latest change. Forgetting that version restores the preceding applicable document, with the current #367 receipt semantics; later versions win over an older undo. Forgotten-content tombstones prevent the exact rejected document from being learned again unless the forget is undone.

### Existing lessons and migration

The existing one-time migration collects eligible `profile.working.` rows into the first notes version, newest-first within the bounds and displayed in chronological order with duplicates removed. Each moved row has its own content-free migration receipt in the same transaction as the version/config record. Secret-bearing, overlong or out-of-bound rows remain untouched. Undo does not rerun the migration. This consolidation adds no migration.

Self-review Evidence records the version/ref, previous version, counts, lengths, disposition and redacted blocker where applicable, not the full notes or source input. The old lesson-as-Memory event interpretation applies only to historical rows.

## 8. Dream: gather, consolidate and review

Dream reuses the same queue, pause, budget and single-flight mechanics, with its own switch. It uses the judgment route's strongest supported model, not a hard-coded model name. No external dreaming framework is introduced.

### Timing and an explicit source/text discrepancy

The reviewed `Upkeep.enqueue_dream_if_due` checks at most once per minute, requires no pending/claimed upkeep or active Work, at least three upkeep-eligible requests, remaining call budget and at least 20 hours since a prior dream. The first dream waits for the owner's local night, 01:00–05:59. The code permits a later dream outside that window after 36 hours.

The #422 prose said that every dream waits for night, while the #402 fallback and current code retain the 36-hour exception. **This is an unresolved documentation/source discrepancy, not a policy change made by this edit.** Do not promise night-only operation for later runs, and do not alter the scheduler from this editorial consolidation. An owner decision or a separately scoped implementation correction must settle the discrepancy. The newest eligible conversation Work is the anchor, falling back to the newest eligible Work when necessary.

### Inputs and two passes

Dream reads the settled eligible requests since its last period, no more than seven days and 40 requests, scrubbed per source Work and bounded in aggregate. Forgotten turns, owner-state management calls and excluded run types remain excluded. The current profile with short IDs, working notes and clock supply context, not permission to invent new facts.

The first pass gathers up to ten facts without endings. The second pass reads what survived validation and consolidates facts, notes and the daily record, adding a fact only when gathering missed it. No new call starts after the 600-second dream run window. Facts preserve the situation in which a preference holds; temporary conditions and passing reactions must not become universal traits.

Each proposal cites the turns or profile IDs it relies on. Without a turn source it can only restate or end the profile lines it names, not derive a new owner fact from the profile alone. Inferred facts need two separate turns. Validation also checks secret/forgotten-value/ID constraints and the stated-versus-inferred source record.

### Endings, held questions and quiet consolidation

An ended row must be cited by the same proposal. Its reason must be a restatement of that fact, a replacement supported by an owner turn, or recognition that the old row was not a lasting fact. Different subjects are not duplicates. An uncertain ending is held, not applied. The model proposes IDs and reasons; deterministic code checks the declared citation/shape boundary, not topic keywords.

When ending proposals would retire more than a quarter of current profile rows (with the established minimum of one), reject the ending proposals while retaining pure additions. A same-key replacement is distinguished from this mass-ending count.

Dream facts and daily records carry `origin: dream`, are inspectable with source and supported undo, and do not join the anchor's foreground saved-memory notice. Consolidation is quiet. A working-notes revision retains its own notice; this is not permission for every dream to send a summary.

A changed relied-on fact or a held ending creates a `MemoryRaise`. It is supplied to the AI brief through the existing request-relevant profile selection. The AI mentions a relevant change or asks one short question with its reason; it changes Memory through the ordinary tools on the owner's answer. The first Work that receives the item consumes it, consistently for its readers. An unraised item expires after seven days without changing the stored fact. No background memory push is inferred from that item.

### Learning from observed misses

The #496 refinement supplies typed Work outcomes and correction/retry links. At most twelve eligible misses from the earlier seven-day window may support working notes only. A notes revision may cite two misses on different owner dates, at least one from the current period, alongside the existing two-turn/two-day rule. Earlier `m` references never support facts, invalidations, preparation suggestions or the diary. Existing notes bounds, secret checks and refusal of overly broad revisions remain; the Evidence records counts and disposition, not earlier text.

### Daily record and bounds

The daily record is a dated retrieval aid outside `profile.`, under `diary.<owner date>`, with one dated line per event. It is not shown as a remembered fact and is not a source for new owner facts. The worker brief may include the last two owner days and up to three older request-matched records, whole within 4,000 characters, after recalled owner words; owner-model judgments and preparation Works do not receive that same block.

The old #402 prose specified 800 characters, while the later #442 implementation's `DIARY_CHARS` is 1,500 in the reviewed source. **This numeric source/text discrepancy is recorded explicitly; this edit does not change either the runtime limit or an approval boundary.** Source-sized records must not be described as satisfying an 800-character limit. Pause and secret checks still apply.

## 9. Recall, explanation and inspection

A bounded profile snapshot identifies omitted rows. The worker can use owner-scoped `search_memory` to retrieve current facts beyond that snapshot, choosing query terms by judgment. The rebuildable FTS5 index is not canonical owner state; the bounded literal fallback is not a semantic-memory performance claim. Search returns saved time and source references and is included in the per-Work information-use audit. It does not change write, forgetting, approval or egress policy.

When a fact materially affects an answer, explain it in ordinary terms so the owner can correct it. Treat inferred or possibly stale facts tentatively; ask when the cost of being wrong warrants it. Do not report a stored key instead of explaining its relevance. Quiet background consolidation must remain inspectable and correctable.

## 10. Evidence, scope and provenance

An upkeep, self-review or dream event is not an external task-completion event. Record the run kind, period/anchor, transport-backed calls, route/model, counts, outcomes, bounds and digests without source text. Paused, expired, unavailable and refused outcomes are not successes.

The [published experiments](../../research/memory-and-dream-experiments-2026-10-22-to-11-05.ko.md) distinguish replay/model-judged results from the small owner-use observation. They do not establish a universal accuracy score or validate this contract merely by existing.

| Earlier source | Current home |
| --- | --- |
| #918 slice (a), #818/#836 third-party remainder | Sections 1–4: writer boundary, direct save, candidates, notice and undo |
| #220, #367 | Section 3: append/invalidate, expiry, forget versus delete |
| #805, #876 | Sections 5–6: queue, transport-counted budget, upkeep and expiry |
| #998, #19 | Section 7: self-review, one working-notes document and migration |
| #402, #416, #420, #422, #423–#426, #429, #442, #493/#495, #496 | Section 8: two passes, sources, consolidation, daily record, lessons and the disclosed discrepancies |
| #862 | Section 9: bounded recall and attributable use |

The original amendment text remains at the pinned source revision; it is not copied into a second living contract. Secrets, third-party authority, payment rules, Work outcomes and the original experiment evidence are not widened by this consolidation.
