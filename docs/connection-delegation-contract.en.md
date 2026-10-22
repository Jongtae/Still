# Connection and Delegation Contract

## Status and precedence

CONN-DELEG-01 [#1221](https://github.com/Jongtae/agentos/issues/1221). Owner direction 2026-10-09 (design conversation): the concepts below are to be written down concretely enough that any session or AI can be briefed against them and its result judged by them.

This is a current product contract. It issues no authority and claims no shipped behavior. Capability claims still need merged code and named evidence (C6). It does not select a goal; [`delivery-plan.yaml`](../delivery-plan.yaml) does.

Precedence:

- The [Development Constitution](development-constitution.en.md) governs this contract.
- The [Secretary Agency Contract](secretary-agency-contract.en.md) **pilot posture** governs where the two differ: secrets never enter a model prompt, a log or Evidence; payment needs per-action owner approval; non-payment mutations on services the owner connected need no per-action approval. Nothing here adds a request-time protection layer that the pilot posture removed.
- The [Owner Control Contract](owner-control-contract.en.md) (effects, approvals, execution location, disconnect/revoke/forget) and the [architecture](personal-agentos-architecture.en.md) (kernel primitives, Runtime boundary) stay in force. This contract applies them to connections Still does not hold and to delegation.

Sources it adopts rather than restates:

- [Role, mandate and authority research (2026-10-05, ko)](research/role-ontology-and-mandate-2026-10-05.ko.md): Role, Mandate and Authority are distinct; the execution-boundary intersection; the asset-management and discretionary-management case.
- [Whitepaper "Whose Agent?" (ko)](whitepapers/whose-agent.ko.md), §8–§9: a distributable skill format is not a trustworthy capability; connectivity is not owner independence. Both documents remain non-normative research; only what this contract states is current.

## 1. Who holds what

| Part | Holder | Replaceable? |
| --- | --- | --- |
| Brain (the model doing the work) | the owner's chosen AI: Codex, Claude Code, or a configured model API (C16) | yes |
| Hands (connections to services) | the AI's own connectors/MCP/plugins, Still's own connections, or the browser | yes |
| Methods (how a kind of task is done) | skills: public first, Still-reference second (C16 #974) | yes |
| **The owner's way** (which service, which option, what counts as news) | Still, as owner state (§7) | no: it is the owner's |
| **Memory, judgment records, history, decisions** | Still (C1): Memory, the #886 judgment journal, Work/Event/Evidence, the information-use audit, approvals, trust records (§6) | no |

**Switch test.** If the owner drops one AI subscription tomorrow, the bottom two rows stay whole. Hands are re-attached through another AI or through Still's own connections. A design that fails this test is rejected.

Still stays the commander even when it holds none of the hands. It decides which hands may act, approves what needs approval, defines what is delegated, judges results, and keeps the record.

## 2. Capability discovery and route choice

When a request needs a capability the current Work does not have, the owner's AI collects candidate routes first and then chooses one by judgment, as an experienced developer does after reading the manuals. The decision model (C16, #710) picks a worker able to do that. Amended by CONNECT-CODE-01 #1380: the earlier rule "move to the next step only when the current one has nothing usable" forbade choosing a lighter route over a usable but costly one (for example an MCP that needs Docker when the vendor's REST API serves the request).

Candidate sources. The list order is where to look, not what to pick:

1. **The AI ecosystem's capabilities**: connectors, MCP servers and plugins the owner's AI already has, or that the official MCP Registry or the vendor's directory offers. A capability is "what can be done".
2. **The vendor's own developer documentation and API**, and maintained public implementations of it.
3. **Skills**: a public skill, then an Still reference skill. A skill is "how to do it". It never grants authority.
4. **Generic channels**: the signed-in browser (#680, #939), an authenticated API read by secret-slot reference (#1218), public page reads, or a sandboxed code run (`code_run`, #1380) in which the AI calls a documented API with code it wrote. The [code-run design](connect-code-run-design.en.md) states its sandbox, egress and secret boundaries; reads only until secret sets and the effect policy land.
5. **Build**: only for a concrete unmet contract, through the normal C15 review. Never a task-specific feature in core code (C16).

Selection criteria, weighed together:

- what the owner must install, register or operate (extra runtimes such as Docker, developer-portal registration, repeated sign-in);
- whether the route is official, standard and maintained;
- whether it allows the needed scope, read-only when only a read is asked;
- how secrets are handled (never in conversation, model prompt, log or Evidence);
- reliability (a session that expires often versus a token the AI can renew).

List position only breaks ties. A saved owner route choice still wins (§3). When the AI uses or proposes a connection route, its reply says briefly which candidates it found, why it set the others aside and why it chose this one. The outcome judgment treats a limitation or an owner-burdening proposal without that statement, or without observed discovery, as not serving the request.

Generating a skill from scratch inside a Work is not part of this order. Curated procedural skills outperform naive self-generation (FinSkillBench, as summarised in the 2026-10-09 review).

**Experience-verified function libraries** are different (owner decision 2026-10-13, PROC-MEM-01 #1482, #1486). They are a connection's procedural memory, kept as code; this is not naive generation:
- The owner's AI may save the functions it used in a `code_run` on one code-run secret set.
- A save is kept only when that run succeeded and passed a parameter-flow check. The check calls each function with two example argument sets in the sandbox, records state-changing requests without sending them, and refuses a function whose arguments do not reach its requests.
- It becomes the set's next version only when the Work's outcome judgment is `REACHED`; otherwise nothing is saved.
- The owner is told afterwards and can remove it (`forget_record`, `procedure:<id>`, with undo).

The library is owner data: versioned, with per-function call counts. It is removed with its secret set and listed with that set in the context, so the AI calls the functions instead of re-reading the vendor's documentation. Still stores, verifies and lists the library; it never selects functions for a request or reads what they are for. A function is a method, never a permission: every state-changing request still needs the owner's exact approval. A failed function is fixed by the AI and saved again only through the same gate.

`capability_search` is metadata discovery on the existing owner-state tool relay.
`configured` reads local AI configuration names without starting a server;
`registry` sends only a short public server-name token to the official Registry.
The Registry's search is a name substring match, not an exhaustive semantic
capability search. Results are bounded, cached, untrusted metadata; a publisher
namespace does not establish safety, and an unreported licence stays unknown.
Registry failure leaves the next search steps available. The Work's information-use
audit distinguishes an outbound lookup from a cached or local configuration read.
Discovery neither installs nor connects anything and never establishes authentication,
tool availability, scope or action authority. Supported setup/authentication/readiness
and automatic continuation are separate obligations of CONNECT-FIND-01 #1299;
a discovered result alone cannot complete that issue or the original request.

## 3. Connection precedence

For each service, the hand used is chosen in this order:

| Order | Hand | When |
| --- | --- | --- |
| 1 | **AI-side connection**: the subscription AI's connector, MCP server or plugin | default |
| 2 | **Still-owned connection**: own Google client (#1172), secret-slot API (#1218), an MCP server Still connects to itself | the AI side offers no decision point for the effects needed (§4), the owner wants full control of that service, or the AI side is unavailable |
| 3 | **Browser**: the owner's signed-in session, with phone sign-in and AutoFill (#939, #1170) | neither 1 nor 2 exists for the service |
| 4 | **None** | say so plainly, and guide the owner to connect in conversation; never guess (#1197 acceptance) |

Rules:

- **The owner's choice per service is the owner's way (§7).** "I want Still to hold this one" is stored and not asked again.
- **One subscription is enough.** With only Codex, only Claude Code or only a model API, the same order applies. Rows that subscription lacks are skipped.
- **Connections are shown where the owner manages everything else: in conversation first (#880).** Each service shows which hand it uses: AI connection, own connection, browser, or none. That is the #1197 Settings rule, generalised.
- **AI-side credentials stay where they are.** Still never copies an AI vendor's connector tokens. Still-owned secrets stay in the owner-local encrypted store with a Keychain-held key, and are referenced, never shown to a model.
- **Disconnect, revoke, remove and forget keep their Owner Control Contract meanings for every hand.** Disconnecting an AI-side connector (for example `claude mcp logout`) is not revocation at the provider and is described as such.

The generic API input path uses immutable encrypted credential references in
the existing owner-local secret file. The existing macOS Keychain primitive
holds its encryption key; neither the model nor slot metadata receives that key.
Ciphertext binds the slot name, revision, destinations, header/scheme, subject,
money marking and freshness bound. Publication compares current slot metadata
inside the existing SQLite transaction; a failed or stale publication discards
its new reference and preserves the prior credential. Staging alone grants no
authority. Successful encrypted migration removes the legacy plaintext value;
cleanup failure explicitly distinguishes published state from completed cleanup.
Recovery can finish that cleanup under the same slot-revision transaction before
queuing the original Work; a stale receipt cannot remove a later connection's
credential, and recovery does not repeat the readiness request.
Reads with a missing key fail without generating a replacement key. Active,
staged and retained encrypted values participate in secret redaction; unreadable
ciphertext withholds context instead of silently skipping that boundary. A
connection revision change during a call withholds its response; after an
approved external change the effect remains unknown, never safe to replay.
These storage/transport properties alone do not complete the input-page,
readiness or original-request continuation criteria of #1300.

The input path's readiness boundary checks an unpublished encrypted candidate
through the existing API transport, without resolving an older same-named slot.
It permits only reads and applies the existing destination, subject, required
field, consistency and freshness checks. Authentication failure or an unusable
response leaves the old connection intact. Owner/Work validity is checked before
each transmission and after the response; stale metadata is rejected again at
publication. The existing SQLite transaction can record the input's publication
receipt alongside the new reference, so either both commit or neither does.
That receipt is not a completed task or permission to replay an action. Legacy
credential cleanup and the current continuation prerequisites must also finish
before reporting the input as ready. These are model-free transport/storage
properties; the contextual page and end-to-end continuation require their own
integration evidence.

`settings_change(api_connections, connect)` carries only public slot metadata
and the required read probe. Exact owner confirmation opens the existing
one-time input surface locally, or sends its tunneled link only to the original
paired Telegram owner. The page binds its code/cookie to the original Work,
owner generation, request and draft digest. `awaiting-input` keeps that Work
parked. Authentication failure permits corrected input on the same page;
expired/restarted pages can be renewed for the same original request. Three
input checks and three link starts per Work bound this path independently of
the common continuation cap. Publication and its receipt share SQLite; restart
may observe a committed receipt without repeating its API read. Missing keys,
changed requests/accounts, stale pages and cancelled inputs cannot queue Work.
Temporary key unavailability or incomplete legacy cleanup keeps a current
publication waiting instead of consuming its continuation; recovery resumes it
once the key and cleanup are available. Changed owner/request/connection identity
does not receive this recovery exception.
The static-key and OAuth local HTTP fixtures reach the original substantive worker result
with no second owner request. The remaining whole-program acceptance audit is
not established by that fixture.

The optional OAuth form adopts the installed `oauthlib` `BackendApplicationClient`
for RFC 6749 client-credentials request construction and token response parsing.
Still adapts it to the existing no-redirect HTTP transport, encrypted references
and current-connection checks; it adds no dependency or separate OAuth store.
The confirmed public metadata names the HTTPS token endpoint, Basic or form-body
client authentication, and requested scope. The one-time page takes the key pair.
The supported input values are single-line strings of 8–4096 characters; issued
tokens must be Bearer tokens with a supplied lifetime greater than five seconds
and at most 31 days. Missing expiry, changed scope, unsupported token types and
issuer redirects are refused without logging the issuer body or retrying issuance.
Other issuer contracts retain the AI-side/browser fallback, not a provider branch.

The encrypted bearer cache binds the exact credential reference, connection
metadata and expiry. A successful readiness probe publishes that cache with the
new connection, so the original request can use it directly. Expired tokens are
reacquired through oauthlib using the current key pair; a 401 invalidates the
cache without replaying the API action. Cache renewal does not change the owner
credential revision or authorize an action. Money-capable calls still need their
exact one-use payment approval before any renewal or action. Concurrent cache
renewal alone does not invalidate a pending connection input; credential or
configuration changes still do. Active and retained
tokens and both key-pair values use the existing redaction boundary; retained
encrypted values alone grant no authority. Model-free fixtures cover Basic/body
forms, local HTTP transport and redirects, the actual input page, cache reuse,
renewal in the resumed original worker, issuer/scope/account changes, and payment approval. These are not live
provider/account observations or proof that every API uses this OAuth shape.


A legacy API slot without a `money` classification is conservatively treated as
money-capable for non-read actions. Reads remain available. Saving it without a
classification preserves that approval floor; the owner may explicitly classify
an unmarked legacy slot through the existing registration/input boundary. An
existing explicit money mark still cannot be lowered by re-registration.

API dispatch follows the pilot distinction above: a connected nonpayment
mutation needs no per-action approval; a payment or non-read on a money-marked
slot still requires exact one-use approval before credential renewal or action.
The method supplies the existing mutation floor. A payment card preserves the
bounded exact body/query; a secret-bearing or overlong description is refused
instead of asking the owner to approve hidden/truncated input. API payment
bindings use the new `api-payment-v2` state prefix; older API approvals must be
shown and approved again. Browser/connector binding bytes remain unchanged.

Both kinds of API effect use the existing Work tool-event store for a committed
dispatch claim immediately before transport. SQLite serializes admission against
other effect claims. The installation-keyed input reference binds the original
host-validated request, slot, method, transmitted URL and exact body; it excludes
connection revision and the model's effect label, so reconnecting or changing
workers cannot license the same dispatch again. Different owner requests have
different remote idempotency keys. Only minimal destination/operation/revision
metadata and keyed input references enter claims, never raw body/query/credentials.
This protects exact dispatches; semantically equivalent aliases or reordered
payloads are not claimed to be universally identical remote operations.

A pending claim survives a crash and blocks additional API/AI-connector changes.
A received usable response is an observation, not proof of the original goal;
continuation still reports authoritative data and its freshness/completeness.
An asynchronous 202 or subject mismatch remains incomplete with unresolved
effects. Timeout, arbitrary transport failure, redirects, other error responses
and unreadable results are not retried. HTTP 401/403 is recorded as authentication
refusal without an accepted effect, permitting corrected connection input;
that transport is never automatically retried. Current Work and slot checks run
before dispatch, and an in-flight slot change withholds the response. The
claim cannot cancel a request already sent or establish universally exactly-once
remote execution. Unresolved outcomes need reconciliation, not another approval.
Successful read/readiness paths never acquire effect claims.

`test_connect_api_effects.py` covers generic nonpayment dispatch, money floors,
real payment callbacks and queue/worker continuation to the original result,
parallel attempts, restart/reconnect/host-retry no-replay, cross-route unknown
outcomes, cancellation/revocation, response qualifications and claim audit.
Existing API/input/OAuth/connector/skill fixtures remain regression evidence.
These tests use injected transports and temporary stores, not live services.

Native OAuth completion resolves the initiating identity from the existing
signed pending state against the installation's current local/paired identities.
A later request on another channel cannot redirect the callback or its resume
receipts. Identity resolution exposes only the owner reference; it does not
consume valid state, exchange a token or grant authority. Completion still
checks expiry, current revision, exact grant and single use. Expired authentic
state can identify its owner so the ordinary refusal remains correctly bound.
`test_connect_oauth_owner.py` covers both channel orders, simultaneous flows,
restart, denial/expiry/forgery/replay and Calendar read/write/Drive separation
using fixture transports, including the original Gmail worker result. It is
not a live provider observation.

## 4. Control levels

Still controls a hand at one of three levels:

| Level | What Still does | Applies to |
| --- | --- | --- |
| **Mediate** | Still makes the call itself: secrets injected, destination allowlist, payment guard, response provenance (§8), budget | Still-owned connections, the browser |
| **Decide per call** | the AI's CLI stops before each tool call and Still allows or refuses it, seeing the tool name and the actual input; then observes and records the call | AI-side connections, where the CLI offers a decision point (Claude Code `--permission-prompt-tool`, listed in 2.1.280's help; Codex: to be verified) |
| **Delegate** | Still defines the mandate, judges the result and keeps the trust record (§5, §6); it does not see each step | other agents (A2A), external services acting on their own |

What each effect class needs, under the pilot posture:

| Effect | Mediate | Decide per call | AI-side, no decision point | Delegate |
| --- | --- | --- | --- | --- |
| read | yes, audited | yes, audited | read tools only, audited from the CLI's tool events (#1197 baseline) | yes; returned data is audited |
| mutate (non-payment) on a service the owner connected | yes, told after (pilot) | yes, told after (pilot) | **not offered**: Still could neither stop nor reliably record it | only inside the mandate's stated scope |
| payment / money movement: payments, securities orders, transfers | per-action owner approval bound to exact parameters (Owner Control Contract) | per-action owner approval, decided at the CLI's decision point with the exact input | **not offered** | **never delegated** in the pilot; each such effect returns to the owner |

Classifying an effect:

- **Deterministic guards apply where they can see the effect.** Examples:
  - the card and one-time-code field guard (#698);
  - #1172's Drive transport, which refuses every method except GET;
  - #1218's API transport, which treats only GET and HEAD as reads and floors every other method to at least mutate.

  An HTTP method is not proof of no side effect: a GET that changes server state is a known #1218 follow-up.
- **A label can only add a requirement, never remove one.** This holds for the model's effect label and for an MCP server's `readOnlyHint`/`destructiveHint` annotations. Annotations are untrusted hints.
- **The owner can mark a connection as money-capable.** A brokerage is the obvious example. On a money-capable connection, a tool call counts as read only when an Still-owned transport limits it to a read method (GET or HEAD), or when the owner reviewed that tool as read. Every other call there is payment-class.
- **Semantic effect judgment stays with the model; deterministic code enforces only these invariants.** There are no keyword rules (Decision Layer).

Still-owned API slots carry a boolean money-capable marking. Every non-GET/HEAD
call on a marked slot is payment-class regardless of the model's declaration.
The existing exact one-use approval binds the slot revision and effective class;
an earlier approval cannot cover a changed marking or slot. Registration cannot
lower an existing true marking, and omission preserves it. Invalid stored
markings withhold the slot instead of coercing a string or number to a boolean.
Legacy slots without the field retain their prior effect floor; the new owner
registration surface must collect the marking explicitly. This transport rule
alone does not establish contextual registration or secret-input continuation.

Decision points must be proven before they are relied on. A compatibility test shows that the CLI actually stops at the decision point for an AI-side connector's write tool, and that Still's refusal holds. Until that observation exists, the column "AI-side, no decision point" applies.

Observed decision points (#1296):

- **Claude Code** (2.1.280): `--permission-prompt-tool` answers every AI-side tool call that is not pre-approved: claude.ai connectors (#1197) and the MCP servers the owner confirmed for Works. Observed 2026-10-10: under `--setting-sources project` a user-scope server in `~/.claude.json` is not started, and the same launch without that flag starts it. Only the confirmed entries reach a Work, through `--mcp-config`.
- **Names.** No owner server may be named `agentos` or start with `claude_ai`, so none can carry the bridge's or a claude.ai connector's tool name.
- **Codex** (codex-cli 0.153.4, observed 2026-10-10 on real turns with a local test server):
  - a session-flag `PreToolUse` hook receives the MCP tool name and its actual input;
  - a `deny` holds: the server never received the call;
  - empty output lets the call run; `allow` is unsupported and blocks the call;
  - without `--dangerously-bypass-hook-trust` the hook is skipped and the call runs, so Still passes that flag whenever it loads an owner server.
- **Fail closed after the fact.** A call the worker's own stream shows completing without an Still `allowed` decision is recorded, and the owner's servers are switched off for every engine until the owner confirms them again.
- **Which servers.** Only servers the owner confirmed for an engine load into a Work (empty by default). Their definitions are read from the owner's CLI at launch and never stored by Still. For Codex, values that may be secret travel only in the CLI's environment and are withheld from the model's shell.

## 5. Delegation

The mandate fields adopt the research §06 list. A delegation states:

- **who**: principal (the owner via Still) and delegate (an agent, AI or service);
- **purpose and completion condition**;
- **scope**: data, hands, effect classes;
- **exclusions**;
- **budget and validity**;
- **what to report**;
- **when to come back to the owner**.

A clear one-off request is its own mandate. The owner fills in no form. Only standing delegations need an explicit scope, expiry and end condition.

Rules:

- **Nested authority is a subset.** A delegate never gets more than the delegating Work has, and two delegates' scopes do not add up (architecture; research §06).
- **Context, not conclusion.** When the purpose is an independent judgment (a second opinion), the brief carries goal, facts and criteria, not the delegator's preferred answer. A brief that carries a conclusion must say so, and the result is not reported as independent.
- **Still judges and declares completion.** The result is judged against:
  - the owner's way (§7);
  - the constitution and contracts (the owner's durable values);
  - the domain standard a loaded skill or cited source provides;
  - observed evidence (§8).

  The decision model may adjust and re-delegate within the existing #710 bound (at most twice, never after an effect). Where a delegate's answer conflicts with the owner's criteria, the conflict is shown to the owner rather than silently resolved.
- **Revoking a mandate stops new calls; it does not undo effects already made** (Owner Control Contract, OC-05).
- **Phase-2 family delegation** keeps its own contract (A2A D-04, Secretary Agency Contract §Family collaboration).

## 6. Trust ladder and trust record

One ladder for every third-party hand, skill or agent:

1. **Report**: read and summarise.
2. **Propose**: recommend an action; the owner acts.
3. **Act after per-action approval.**
4. **Act within a standing mandate.**

Rules:

- **Starting rung.** A newly introduced third-party hand, skill or agent starts at the rung the owner chooses when it is introduced. The recommended default is rung 1 for a money-capable domain and rung 3 otherwise. The owner's own AI working on owner-connected services already runs at the pilot defaults (§4); the ladder does not lower them.
- **Introduction is conversational.** The AI introduces the hand, skill or agent: what it is, its source and licence, what it can and cannot do, the proposed rung. It can run a trial that performs no effects first: reads only, or a provider's paper/sandbox mode.
- **Raising a rung is an owner decision, never automatic.** Lowering may be automatic after a failure or an owner correction, and the owner is told.
- **Payment never reaches rung 4 in the pilot.**
- **The trust record is owner state.** It holds, per hand, skill or agent: what was delegated, what was observed, owner corrections, and the owner's final decisions. Popularity, install counts and review scores never move a rung (C9).

## 7. The owner's way

The owner's way is how this owner wants things done: which service, which option, what time margin, what counts as worth an alert, which hand per service. "Personalisation" is not a goal here. Following the owner's way is the default.

Rules:

- **Precedence.** The owner's way, then a loaded skill's default, then Still's default. No vendor, skill or package default silently replaces a known owner choice.
- **Slots first.** A ready-made method declares the slots it honours. Examples: the map-directions skill's "preferred map service", or a parameters file next to a skill such as `invest.md` in public investment skills.
  - When the owner's way needs something outside the slots, the AI first tries another hand that can honour it. The example is setting a route option in the map service through the browser, which `references/kakaomap.md` links cannot express.
  - If no hand can honour it, the AI says exactly what it could not honour.
- **Learning.** The owner's way is learned from what the owner states, from corrections, and from Still's own records. It is saved under the #918 slice (a) rule: saved, told, exact undo. It is corrected and forgotten under #794.
  - When it is unknown and the answer would really change, the AI asks once and saves the answer. This is the map-directions pattern.
  - Wholesale observation of the owner outside Still is not a learning source.

Per-service connection choices use the same canonical profile Memory, for example
`profile.preference.connection.<service>`, with the owner's value in their own
terms. Before worker selection, the original request retrieves current profile
matches through the existing bounded literal Memory search. Those matches precede
the remaining profile rows within the same snapshot limit, for both the early
plan and ordinary planning/worker context. Non-profile memories do not consume
this retrieval limit. Omitted choices remain retrievable through Memory search;
literal matching does not establish semantic recall for arbitrary wording. An available alternate
connection does not overwrite the choice. If the preferred route is unavailable
or cannot perform the required operation, the AI explains the observed limitation
and uses or offers an alternative within existing authority and the owner's
choices, without silently switching accounts. Temporary fallback changes neither
the saved preference nor connection grants. Correction, exact undo and forgetting
use the ordinary Memory lifecycle. Model-free fixtures establish this state and
context path, including synthetic API selection/fallback; they do not establish
how a live model will judge an arbitrary preference.

### Goal-first alternatives (#1301)

The decision model interprets the desired outcome and constraints before selecting
tools, including useful routes the owner did not name from the first attempt.
This adapts the existing alternatives rule; novelty is not a requirement and an
alternative never overrides a required method, identity, refusal or later correction.
The original owner message remains the request. Optional orchestration notes carry
relevant factual goal context and observed discovery outcomes, not a rewritten goal
or a second set of completion criteria (#820).

The chosen worker discovers and performs the work. Both API and CLI workers receive
the same generic guidance: lead with what was actually done or found, then, if a
prerequisite remains, the best supported route and one concrete next step the owner
can settle in one answer. State the unmet condition briefly; never claim an unobserved
capability, effect or future follow-up. A plain offer creates no durable continuation.
The existing input/Settings/approval flow must bind any promised automatic resumption
to the original Work. Useful partial work and setup receipts are not the original result.

The existing `goal_reached` judgment assesses alternative-route usefulness against
the original message, observations and constraints; there is no extra route judge.
For an execution request, a proposal, prerequisite question or setup-only receipt
without the original result must receive a negative completion verdict, even when
useful. A bound prerequisite establishes waiting, not completion. A request solely
for advice, a proposal or setup is judged on its own actual scope.
`tests/test_connect_goal_routes.py` exercises the four §11 inputs and a held-out
synthetic capability through injected decision/worker transports, preserving the
original request, discovery notes and single outcome-judgment interface. It also
checks re-planning after a bare limitation and truthful incompletion when a proposed
route has no bound continuation or observed result. These tests establish the prompt
contract and handoffs, not live model quality or real route availability. Predecessor
connection/approval continuation fixtures remain the evidence for actual callbacks;
this prompt change does not complete the CC1–CC8 audit by itself.

### Live corrective continuation (#1375)

Owner direction 2026-10-11 adds operating acceptance after the earlier fixture
closeout: the selected AI must discover and prepare a usable connection, bind
necessary owner input and return the original substantive result on the same Work.
An uncoached service request is required; setup instructions embedded in the
request or service-specific core guidance cannot establish this acceptance.
Tests, discovery, setup readiness and an actual account read remain separate
evidence classes. No order, transfer, payment or schedule is authorized by this
read-only validation.

The orchestration effect guard reuses the host's existing bookkeeping exclusion
and `unrun_page_step` predicate. An internal continuity record or a declared read
refused before dispatch does not prohibit another worker. Actual and unknown
effects still prohibit replay. A known effect does not exempt a final answer from
the existing completion judgment; an unobserved effect or skipped judgment cannot
establish success. No new evaluator, store, protocol implementation or service
branch is introduced by this correction. Local-package preparation and credential
boundaries still require their concrete reuse/authority review; this correction
does not itself enable package installation or prove live continuation.

## 8. Data truthfulness

Every external read that feeds an answer keeps:

- its source;
- the time the source says the data is as of;
- the raw values apart from values Still or the model computed;
- two independent states:
  - **freshness**: `fresh`, `stale` or `unknown`;
  - **completeness**: `complete`, `partial` or `inconsistent`.

  A response can be fresh and partial, or stale and complete.

These are generic Evidence semantics, introduced for API reads by #1218 with exactly these values. They are never domain-specific types in core code.

A report never upgrades a state. `stale` stays stale, `partial` is not reported as complete, and `inconsistent` values are not reconciled silently. Requested, observed, failed and unknown stay distinct (Secretary Agency Contract §Evidence-based completion). For money-related reads, the answer names the account and the as-of time it used.

## 9. Cost

- **Explore once, repeat cheaply.** The first time, a capable model works out how to do a task for this owner. Later runs re-use that method (skill plus the owner's way) on the cheapest model that is adequate. A situation-dependent reply still never drops to the lowest-cost model (#1008).
- **A watch costs nothing when nothing changed.** The #719 rule is kept: a result identical to the last one is quiet without a model call. A further step is a cheap model-free check before calling a model; it is listed as a gap (§12).
- **Repeating a method is not replaying an effect.** A repeated procedure re-reads current state and re-authorizes every effect. No replay, ever.
- **Budgets bound every Work and every watch.** Delegation budgets are part of the mandate.

## 10. Threat model

| Threat | Holding line |
| --- | --- |
| A third-party MCP server or plugin lies in its tool description or annotations (tool poisoning) | annotations only add requirements (§4); unknown and money-capable defaults; owner introduction (§6) |
| Prompt injection through tool results, pages or delegate answers | results are untrusted data; the AI cannot widen authority by what it reads; effects pass §4; the information-use audit records the source |
| Credential exposure | Still secrets are referenced and never shown to a model; AI-side tokens are never copied; browser output mediation (#680) |
| A local MCP server runs code on the owner's Mac | only servers the owner already configured in their own CLI, after a one-time confirmation in conversation; no auto-install |
| Delegate scope creep, or chained delegation widening authority | subset rule; mandate exclusions; completion declared by Still |
| Trust-record manipulation | only owner decisions raise a rung; popularity is not an input |
| Data retained by an AI vendor through its own connector | disclosed per service as an execution-location fact (Owner Control Contract); the owner chooses the Still-owned hand when that matters |

Injection and exfiltration defences beyond these lines remain owner-deferred (trifecta proposal parked, 2026-09-28). This contract does not reopen that decision.

## 11. Observation cases

These are observation windows, not completion criteria. No site, provider or category is named in core code because of them (C16).

| Case | Expected behavior |
| --- | --- |
| "the way to a screen-golf appointment" | Uses the owner's saved map service. Honours "prefer main roads" when a hand can; otherwise says it could not. Gives leave-by time from the owner's start point (map-directions method). |
| Cart add on the owner's shop | Hand per §3. Exact item, variant and quantity read back. Payment never started (shopping-cart method; pilot posture). |
| "summarize my current assets" | Read-only. Hand per §3: an AI-side or registry MCP if the brokerage has one, otherwise #1218 or the browser. Account and as-of time named. Stale, partial and inconsistent shown (§8). No order tool offered without the §4 money rules. |
| Second opinion | An independent review is delegated to a different AI with context but no conclusion (§5). Disagreements are shown to the owner; the owner decides. |

## 12. Current state and gaps

| Piece | State | Where |
| --- | --- | --- |
| Still-owned Google connections | merged | #1172, #1204, #1213/#1214 |
| Secret-slot API read with provenance | merged | #1218 |
| Public skills with a repository-root licence | merged | #1220 |
| AI-side connection first, read-only, audited (Google) | open | #1197 |
| AI-side connections generalised to all connectors, MCP servers and plugins | gap | successor of #1197 |
| Per-call decision point for AI-side tools | merged for Claude Code and Codex: claude.ai connectors and owner-confirmed MCP servers (§4 observed decision points); the owner's trust record decides each operation | #1197, #1296, #1297 |
| Still connecting to an MCP server itself | gap | none |
| Conversational secret entry (elicitation URL mode) | gap | none |
| Money-capable connection marking | merged for AI-side connections (#1297); Still-owned API slots follow in #1300 | #1297, #1300 |
| Trust record and ladder | connection subjects: operation classes, money marking, rungs, bounded history, observed outcomes and owner corrections; confirmed exact undo and forget/restore over existing owner-state receipts. Executable skill and delegated-agent subjects are not activated by this connection record | #1297; #1301 audits subject coverage |
| Owner-way slots beyond Memory facts | partial: map-directions preferred-service slot | #794 phase 3 for correction and forgetting |
| Model-free change check before a watch's model call | gap | none |
| Family A2A delegation | planned | #661 |

### Continuity acceptance (owner direction 2026-10-11, CONNECT-01 #1295)

The original Work remains the unit of completion when a request encounters a missing capability, authentication, safe input, permission or action approval. A successful setup receipt is not the requested result. This applies to every domain and does not activate a scheduler, a live account or a release.

- **CC1 — Bound continuation:** verified prerequisite completion automatically continues the original request without another owner request. Setup-only requests do not invent a pending task.
- **CC2 — Actual readiness:** discovery, configuration, installation, authentication, usable tool/schema/scope and action authority remain distinct. Reuse a covering non-payment decision without asking twice; payment still requires its own action approval.
- **CC3 — Fresh execution context:** rebuild the worker catalogue and brief from current connections, grants and revisions on continuation. Preserve canonical owner state and the integrity of each active run's pinned snapshot.
- **CC4 — Multiple waits:** sequential prerequisites, unrelated conversation and independent requests sharing a connection must retain their own Work bindings. Replacement and cancellation are explicit; a confirmation cannot execute unrelated work.
- **CC5 — Effects:** continue only the remaining obligations. Reconcile known and unknown effects, invalidate non-covering approvals and preserve the no-redelegation-after-effect boundary. No universal remote exactly-once claim.
- **CC6 — Durable settlement:** duplicate callbacks, process restart and races cannot duplicate execution. Cancellation, denial, expiry, replacement and revocation close waits; late callbacks cannot revive them or report success.
- **CC7 — Cumulative bounds:** continuation/retry/cost limits belong to the request even when an individual run starts a fresh budget. Waiting and execution time are distinct; watch deadlines and run limits remain effective.
- **CC8 — Outcome evidence:** report the substantive original result, its information-use audit and relevant source freshness/completeness. Configuration or connection evidence alone cannot establish task completion.

Native connector and local-folder handoffs retain one content-free binding per
owner, Work and prerequisite in the existing resume index. A new request using
the same connection does not replace an older request. Connector completion
rechecks each waiting Work's owner, generation, scopes and claim separately;
one expired/refused wait does not prevent eligible siblings from resuming.
Folder approval remains bound to the exact selected handoff and grants no other
waiting request's chosen folder. Focused withdrawal cancels only its own Work.
Legacy connector-keyed entries remain readable alongside current handoff-keyed
entries; the existing pending-registry limit also bounds index admission.
`test_connect_native_waits.py` exercises the actual callback, store, queue and
worker with injected transports, including substantive original results,
restart, duplicate callbacks, legacy rows and isolated cancellation/denial.
These are model-free development observations, not live-account evidence or
completion of the remaining durable-settlement audit.

The existing worker loop also reconciles native/local waits before claiming its
next job. It observes expiry and definition changes through the existing pending
registries without claiming an offer or granting authority. Dead owner/generation
bindings, missing offers and expired unclaimed offers settle only their exact
current handoff. Work failure is a SQLite compare-and-set from waiting, with the
reason retained in the original response and conversation history. A scheduled
Work or a renewed handoff is not cancelled by stale settlement. Claimed offers
keep their recovery rights after the offer deadline; finished claims are completed
without scheduling again. Local folder settlement shares the existing approval
lock. Notices are emitted after settlement and respect current owner/generation;
repeated sweeps do not report another failure. A settled failed Work also clears its retained Telegram reaction through the
existing terminal truth gate, only on the current paired generation. No new timer,
scheduler or model call is introduced. `test_connect_native_settlement.py` supplies model-free
expiry/restart, cancellation, renewal-race, capacity and local-selection evidence.

The same worker admission recovers unexpired native waits whose connection
committed before callback scheduling was lost. It rechecks current owner,
generation, connection and scopes, then uses the existing exact-handoff claim
and queue compare-and-set. A claimed offer may recover beyond its offer TTL,
but current connection authority, Stop, watch window and the original execution
budget still apply. Recovery does not repeat authentication or create a new
request. The worker rebuilds its normal current catalogue. Local folder approval
is not inferred from another ready connection. Prior non-read attempts cannot be
replayed through this path; unknown or unreadable effect evidence remains an
explicit unknown outcome. Admission checks the existing active deadline before
any early judgment or legacy direct read. Every existing terminal Work outcome
can release its finished claim without changing the result. The model-free
`test_connect_native_recovery.py` covers committed/claimed crash windows,
restart, callback races, original substantive results and refusal/budget guards.

Local folder approval publishes the exact selected grant, sharing reset, original
Work queue transition and existing continuation marker in one SQLite transaction.
A failed queue/marker commit leaves no new grant. A lost pending-claim completion
after commit still leaves the original Work queued for the existing worker; terminal
settlement later closes the claim. Current owner/generation, Stop and the waiting
Work are checked at admission. Cancellation winning admission grants nothing and
reports a refusal, not successful approval. A different result folder chosen in
Settings remains preferred, and reference/result overlap is checked against the
current transaction state. Selecting a folder remains transient and is not a grant;
no global folder grant automatically approves another waiting Work. The model-free
`test_connect_local_atomic.py` covers all three grant kinds, rollback, restart,
substantive original result, sibling isolation and current-state races.

The existing browser login callback now commits its final nonce-bound state,
observed keyed sign-in attribution, original Work queue and shared action
continuation receipt together. It never commits an intermediate `resuming`
state. A post-commit lost notification leaves the original queue intact; a
pre-commit failure publishes none of that settlement. This uses the same
continuation cap, remaining-obligation brief, original-message handling and
request budget as other action/settings continuations. Current owner/generation,
Stop, accepted watch window, login expiry and site refusal are checked again.
Unaccounted CLI actions and unknown effects withhold replay; saved browser
cookies do not establish remote account identity or substantive task completion.
Old unqueued `resuming` records settle as unverified instead of assuming a
login; an already queued/running original Work is preserved without another
queue entry or fabricated sign-in attribution. Notices never cross a replaced
Telegram pairing. `test_connect_browser_continuation.py` covers atomic faults,
restart, duplicates, current authority/effects/bounds and actual login callback
through a fresh service/worker to a substantive result in the original chat.

Ownership stays in the ordered children: #1297 supplies the first read-review continuation over `SettingsOrchestrator` drafts, permission events and the existing jobs queue; #1298 supplies effect/approval continuation and reconciliation; #1299 discovery, route choice and connection/authentication continuation; #1300 contextual secret/token input; #1301 audits the complete boundary matrix. These are acceptance requirements, not evidence that all paths already operate.

For a supported content-skill prerequisite, the decision model may propose
`settings_change` with `continue_work="yes"` for `skills.add` or
`skills.enabled=on`. The exact owner-confirmed draft binds the original Work;
setup-only changes omit that flag. Enabling skills also binds the exact
`required_skill` reference (`skill@digest`) shown in Settings, so an unrelated
nonempty catalogue cannot satisfy the prerequisite. The common continuation seam verifies the
pinned source is in the current bounded catalogue and its content is intact
before queuing once. A running worker's skill snapshot never changes; the
resumed worker gets a fresh catalogue and still chooses whether to load a skill.
Installation/readiness alone is not the original result. Failed acquisition,
withdrawn or changed content, cancelled/expired drafts, stopped Works and unknown
effects withhold continuation. Other setting kinds do not acquire automatic
continuation merely by passing this flag. This is content-skill evidence, not
new-server authentication or a full plugin-runtime readiness claim.

For a new public remote MCP, `ai_connections.connect` adapts the same Settings
draft and continuation transaction. Its JSON value binds the engine/name, exact
credential-free HTTPS endpoint (known private/local addresses excluded), public source, stated publisher/licence (unknown
is disclosed), required tool names, named owner-reviewed reads and money marking.
The owner confirms registration, contacting that endpoint, native Mac browser
login if needed, and enabling AI connections plus the selected server. The
existing global switch also makes other already admitted AI connections usable;
this consequence is stated in the confirmation. Long Telegram confirmations
show every part before offering controls. Metadata never establishes safety or
authority; a setup draft cannot overwrite a differing configuration, a refusal,
a previous money marking, or a forgotten trust record.

`RemoteMcpSetup` keeps registration/login/status separate. Only the installed
Claude Code remote HTTP path currently has selected-server readiness support;
Codex setup readiness and phone-based CLI OAuth are not claimed. A connected
status and all required tool names are necessary, but account identity, OAuth
consent scopes and input schemas remain unknown. The resumed CLI rebuilds its
actual tool context and calls remain subject to current Still permission.
Settings, the server admission and the named read/money trust revision settle in
one SQLite transaction. After that commit a restart can queue the same original
Work once; an interrupted apply without that commit is unknown and does not
repeat registration/login or grant authority. Partial vendor registration can
remain after failure; a new explicit setup may inspect a matching definition,
never overwrite it. A historical status receipt is an observation at its recorded
time, not ongoing token-health or account-identity certification.

Setup-only drafts have no continuation. Required tools, owner generation,
current definition/authority, stopped Work and unresolved effects bound resume.
Connection setup attempts appear in the existing information-use view with the
local/endpoint destination, phase and observed CLI state; remote receipt and
OAuth redirects remain unknown. Hostname resolution and redirects are CLI-owned;
after owner confirmation the existing bounded resolver rejects observed private
answers before each CLI setup command, but this adapter does not pin the CLI’s
later DNS/redirects or certify general network isolation. Current
Telegram pairing must still match throughout apply and at the authority commit.
Forgetting the exact connection cancels its JSON setup drafts and any queued
continuation atomically; restoring trust cannot revive that old Work. The bridge uses a host-generated invocation id
to pair durable start/terminal events, including Settings proposals; a missing
terminal event cannot be assumed safe. The evidence is model-free: an unseen
synthetic service through Registry transport, actual bridge process, Settings,
queue, fresh CLI configuration and substantive original result. Real account
operation is not part of that evidence.

Independent Works waiting for the same remote setup can reuse an applied setup
receipt from the same owner, channel and owner generation. Endpoint, engine/name,
public source, publisher/licence and money marking must match; the source's
reviewed reads and observed tools must cover the new prerequisite. The original
observation time and unknown account/scope fields are preserved by reference.
Current definition, admission and exact trust revision must still hold. This
settles a prerequisite without registering, logging in, changing authority or
sharing an action/payment approval. Each original Work retains its own queue
receipt, cancellation, expiry and effect checks. Forget/restore does not revive
either old Work; concurrent cancellation wins over shared settlement. No new
network request or token-health observation is implied by reuse.
New setup drafts also bind the target trust epoch, so a draft inserted after
forget/restore cannot escape revocation by referencing a newer setup. Drafts
predating this binding cannot participate in sharing. The receiving Work's
current owner pairing, generation and stop state are checked again inside queue
admission; a disconnect during settlement cannot authorize a resume.
If the owner already confirmed a second draft while the first setup was running,
the serialized apply boundary performs the same coverage check before any CLI
action. An equivalent admitted draft can settle from the completed setup rather
than fail merely because that setup changed the global admission state.

Observed setup failures can be offered again in the same conversation through
Settings' exact-draft renewal. The owner confirms the same reviewed change within
the existing one-hour renewal window; matching partial registration is retained,
and required tools must be observed before the original Work is queued. Three
admitted setup attempts per Work bound this path across replacement drafts.
Current owner/channel, authority, Stop, forget and effect-safety checks still
apply. Cancelled, disabled and unsupported outcomes are not recovery offers.
Unknown outcomes, including an interrupted pre-commit setup, can be confirmed for
observation only within the same bounds. The renewed draft binds this restriction
and its recovery origin in its digest. It neither registers nor logs in: an absent
or changed definition cannot be overwritten, and a matching definition must expose
the required tools before the normal authority/receipt commit and original resume.
A known non-ready readback leaves a failure that may be separately confirmed for
ordinary setup recovery; an unknown readback stays unknown. Each observation-only
admission consumes the same three-attempt Work budget. Failure context contains
only whitelisted phase/state metadata. These observations do not establish remote
account identity or OAuth scope coverage.

The [connection support and evidence matrix](connection-support.en.md) records
route dispositions, canonical preference/fallback evidence and remaining
#1300/#1301 obligations. It does not establish whole-program completion or
upgrade unknown account/scope information into verified readiness.

The #1297 read-review path binds the exact engine, connection kind, stable identity, operation, original Work and paired generation. Confirmation can arrive before the original run settles; the worker boundary reconciles it afterward. The enqueue and consumption receipt share one SQLite transaction. #1298 shares the eight-continuation limit across settings and action waits. A completed, exactly correlated AI-connector effect can precede another prerequisite: its observation travels in both the planner's continuation context and the worker's brief. Unobserved CLI actions, unmatched effects and unresolved dispatch claims withhold continuation.

The existing Work budget row retains tool attempts, host-visible model-call
attempts and recovery nudges across same-Work resumptions. The foreground host
opens it before semantic routing and carries that exact execution scope into
joined early judgments. Optional start reaction threads capture an
existing-only ledger bridge before dispatch, retaining the run epoch. A late,
paused, stopped or missing run cannot create or reopen an allowance; delivered
completion keeps its deterministic truth-gated reaction when no active budget
permits an optional choice. Those reaction judgments spend a separate bounded
`presentation` counter in the same row, not the work turns (#1418). Request
classification, follow-up, settings and memory judgments made through the decision
route share the worker's allowance, which keeps its last turn for the outcome
judgment.
The API, Jev and subscription decision adapters admit immediately before their
provider request; unavailable configuration or rejected preflight inputs consume
no provider-call allowance. Failed dispatched attempts still consume it. These
counters do not assert provider billing or CLI-internal call/token accounting.
Source attribution alone does not open this foreground budget for background
upkeep or explicit model qualification, which retain their own existing bounds.
Planning, re-planning and direct/CLI outcome evaluation use this admission
without a second charge; callers using a non-routed engine retain their existing
explicit admission. Each host worker CLI invocation still spends one turn.
Reusing a matching early plan costs no extra call; changed inputs require a new
admitted plan. Early futures settle before active time pauses or a newly judged
retry migrates its allowance. A fresh owner utterance is not assumed to be a
retry before the model establishes that relation: its bounded pre-association
cost is folded into the original ledger when bound, never erased. A merged
ledger at or above the cap admits no further provider/worker call.
Routing or planning cannot receive a fresh deadline at worker dispatch, and
exhausted CLI dispatch is refused before recording it as sent.
The host pauses remaining active execution time when its run ends and resumes
only that remainder; owner waiting does not replenish or consume active time.
An interrupted active process conservatively retains its old absolute deadline.
The existing SQLite transaction serializes counters, and a run epoch prevents
old processes from spending or pausing a later run. Missing/corrupt active rows
fail closed. Legacy rows preserve recorded attempts and deadline; counters not
historically recorded start at zero, not an invented historical measurement.
Parked Work budgets are retained. Vendor-internal model calls and token prices
remain unobserved.

Host-recorded retries and consumed owner-input continuations share the original
request's budget even when they create another Work ID. The existing Work
relations and exact consumed input binding identify that request; ordinary
references, corrections and unrelated requests retain independent budgets.
The host and real CLI bridge resolve the same row. A new execution owns the
row's active Work/epoch, so an earlier host or bridge cannot spend or pause it.
Queued continuations wait while their parent is still running. Missing, cyclic
or cross-owner relations fail closed instead of allocating a fresh allowance.
Older per-Work budget rows in a verified request graph are atomically folded
into the original row: recorded counters add, remaining time takes the tighter
bound, and consumed legacy rows are removed only after the fold succeeds.
Corrupt rows leave the transaction unchanged. This does not invent historical
usage absent from old records or grant authority to any related Work.

Accepted preparations now retain their running slot while the original Work
waits for a connection, input or action approval. The host's slot receipt is
bookkeeping, not a tool effect. Settings/action queue transactions and worker
admission recheck the current preparation, deadline and run count; a location
continuation also rechecks that same slot. Cancelling future runs still lets
an already running Work finish as its own Work, but prevents another resume.
Expired queued/waiting runs end with a recorded refusal, without a model call,
new schedule, count reset or a setup receipt saved as the prepared result.
The shared host/bridge ledger is also capped by the remaining accepted window.
Settlement rechecks the current Work and answer so a stale tick cannot attach
an older response to a newer continuation. An unanswered location input that
expires or is withdrawn also ends its asking run without saving the input
prompt as a prepared answer; remaining accepted recurring slots retain their
existing scheduling rules.

`tests/test_connect_schedule.py` exercises the actual local input HTTP callback,
synthetic readiness read, resumed worker and substantive prepared result, plus
exact action approval, expiry/cancellation, queue admission, window bounds and
settlement races. This is model-free fixture evidence, not live scheduled
operation. `tests/test_connect_request_budget.py` additionally crosses actual
location ingress/worker and real bridge-process boundaries with a shared request
budget, and covers migration and stale-run counterexamples.
`tests/test_connect_decision_budget.py` adds planning/evaluation exhaustion,
early-result reuse, active-time preservation, both outcome interfaces and a
host-recorded natural-language retry sharing its original ledger, generic decision
admission, real adapter transports, concurrent early calls, preflight refusal
and substantive worker completion without double-counting. The complete
route/expiry/outcome and whole-program CC7 matrix still requires #1301's audit.

The #1298 effect path adopts the existing browser/API step approval and Work event store; it adds no approval engine or dependency. Confirmed non-payment mutations use pilot authority. Owner refusal and money marking floor model classification; an unknown operation's classification uses the request and argument names, never argument values. Classification cannot create a reviewed read grant. Payment approval binds the Work, engine, connection, operation, exact arguments, owner generation, trust and confirmation revisions, and keyed fingerprints of the MCP definitions actually supplied to the CLI. Changed inputs or launch definitions need a new approval. Vendor-managed account state hidden behind an opaque connector remains outside that fingerprint: it is not evidence of independently verified remote account identity.

Each offered confirmation also has its own random identity, separate from the exact action digest. Recreating identical inputs after rejection or expiry cannot make an old card current again. A still-pending identical offer keeps its original expiry. Dispatch rechecks all unknown effect evidence inside the same transaction that reserves a claim, including unmatched observations and concurrent distinct inputs. Completed effects remain a Work-wide no-redelegation condition after continuation, even when the next attempt fails before making a call.

Credential-bearing argument structures are refused before classification or card creation, using nested field inspection alongside existing stored-secret and credential-shape exclusions. This includes named parameter pairs and JSON/form text containing credential fields; values are not masked and then silently approved. The supported path keeps credentials in the connector's secret channel. A delayed first proposal compares against expected absence, so it cannot overwrite a concurrently issued approval. The connection control describes reads, pilot mutations and per-action payment approval explicitly.

Permission reserves a durable dispatch claim before the CLI can act. Only a matching CLI completion clears its unknown outcome; permission itself never proves success. Live and final stream observations share correlation and do not count an invocation twice. A crash after permission leaves an explicit unknown outcome and blocks further changes; authorized reads remain available to investigate, but a model's inference from a read cannot silently erase that claim. Without a supported authoritative reconciliation receipt, the Work reports uncertainty instead of retrying. Identical effect inputs in one Work are conservatively not dispatched again. Approval cards preserve all parameters (up to 8,000 characters, never silently truncated); long Telegram cards deliver their complete parts before approval controls. Secrets remain outside cards and effect evidence. These are model-free implementation boundaries, not claims of observed live-account execution.

Connection trust uses the existing atomic `QuickStore` config, Settings confirmation and `OwnerForget` receipt lifecycle (Adopt/Adapt; no new dependency or store). An authority revision binds each draft and exact undo. Undo restores the prior operation decisions, money marking and rung together; a newer change cannot be overwritten. Observed call outcomes retain only operation, status and Work reference, never arguments or result content. A failed allowed call or a changed owner classification lowers the rung and is disclosed to the owner; success never raises it.

Conversational confirmation retains exactly the drafts shown to its semantic judgment. Multiple pending Works use the existing conversation focus; an ambiguous or unrelated reply cannot approve all of them, and a draft inserted afterward is not part of the confirmation. A cancelled draft cannot be renewed from an older expired snapshot. Work-bound buttons still name their own set. Automatically resumed input is not a new owner utterance and cannot confirm another draft or withdraw unrelated pending work. Pending changes remain readable as owner-scoped context, without confirmation digests, so the AI can clarify in conversation. That model-facing context includes still-renewable changes, explicitly marked as renewable. An expired confirmation renews its complete offered set before any apply can reconcile the Work. A withheld expiry receipt does not consume execution; a fresh owner confirmation may continue once, while an actually queued continuation still consumes its original wait. A stale decline cannot overwrite a concurrent renewal or report that an applied change never happened.

Separate new read grants for different operations of one hand can be confirmed without re-asking: a complete chain in the existing bounded authority history must show only such independent additions. Corrections, failures, money/rung changes, forget/restore or missing history links reject the stale approval. The setter repeats this check inside its SQLite transaction. This exception does not cover mutations or payment approvals.

Forgetting excludes the record immediately and cancels pending trust confirmations. The existing seven-day forget window permits restoration only through a new owner-confirmed Settings draft, with owner/generation and later-value checks. Generic Memory undo cannot restore authority. Purge deletes the trust payload but retains a minimum scoped revocation marker so seed permissions cannot silently reactivate. Retained Work/settings audit receipts are not erased. A new explicit operation decision starts without the forgotten permissions or seed access; neither restoration nor reintroduction revives old confirmations. The supported subjects here are `(engine, ai-connection/owner-mcp, stable identity)`; content-only skills still grant no authority, and executable skill/delegated-agent runtime trust remains outside this connection implementation. #1301 must distinguish those dispositions from observed support.

## 13. Constitutional amendment proposals (not applied)

The following are proposed for a separate governance issue once real use supports them. They are not in force through this contract.

- **Owner-choice precedence.** Still, skills and packages never decide on the owner's behalf which service, option or hand to use when the owner's choice is known. When it is unknown, they learn it, and if it matters they ask once. This is the other half of C16: C16 forbids scripting the task; this forbids choosing for the owner.
- **Every hand is under decision or delegation.** Whoever holds a connection — the owner's AI vendor, Still or another agent — a consequential effect happens only through an Still decision point or inside an owner-defined mandate. Where neither exists, the effect is not offered.

## Non-goals

- Changing the pilot posture, or adding request-time sensitivity judgments.
- Copying AI-vendor credentials, syncing web-account connectors the CLI cannot reach, or importing plugin hooks and slash commands.
- An Still skill marketplace or skill commerce.
- AI-authored owner skills generated from scratch (§2). Experience-verified function libraries (§2, #1486) are in scope.
- Task-, site-, provider- or category-specific core code.
