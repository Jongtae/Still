# Task coverage map

This is development data for #462. It is **not a score** (#887). Counts show
where evidence exists and where it is missing. They are not success rates,
rankings or targets. Still runtime code must not read this map.

We adopt the supplied public taxonomies, not Still's scenario list. ATUS 2025
contributes 91 second-tier rows. Codes ending in `99` and data codes `50xx` are
excluded. O*NET 31.0 contributes all 41 five-part `4.A` Generalized Work
Activities. Titles and parent names retain the source spelling. The source
URLs, byte digests, licences and attribution are in `taxonomies.json`.
The ATUS digest is for the PDF, not its text extraction. The O*NET digest is
for the CSV. This pass used the supplied downloads without network access.

The first-pass defaults answer the three open questions: use ATUS tier 2;
include work at the GWA level; defer the Korean time-use classification until
a release and reuse terms are pinned. No Korean categories are invented.

Each `still_task` is a synthetic assistance example, not a new category.
`needs` describes that example; optional routes explain `sometimes` and multiple
information classes. Physical participation remains visible even when an AI
can offer advice. Planning a trip is not driving it. A role or skill grants no
authority. Payment approval is a deliberate gate, not a Still blocker.

## Evidence and status

Start at `untried`. A catalogue entry or static scenario does not upgrade it.
Here that means no row-specific execution evidence was identified in this
review, not proof that no person has ever tried it. A static-only row may have
scenario and judgment links while remaining untried.

`fixture-only` needs a concrete U or I citation. Its summary states the tested
subtask and limitations. It never covers every activity under a broad row.
`observed-live` needs a dated P or L run with a revision and a relevant observed
outcome. The historical shopping run stopped at sign-in; it is retained as a
failed L attempt alongside cart fixtures, not evidence of live cart completion.
The presence-eval record is explicitly fixture-only. Historical findings are
checked against current tests before being treated as current failures.

`blocked-by-still` needs evidence attributing the failure to Still's tool
exposure, login, extra approval or routing. `engine-limit` needs evidence
attributing it to the owner's AI. Unknown causes are not assigned either label.
Physical execution and personal security checks are `out-of-bounds-by-design`;
linked advice scenarios do not change that boundary. Row notes retain relevant
limitations that do not fit the contract's four blocker kinds.

U is a model-free unit or fixture check. I crosses a real boundary without a
model. S is static specification or structure. P is a live public read without
an account. L is a live model or account observation. Scenario files and
judgment inputs are S unless a separate dated run is cited. Test dates mark
this inspection, and their revision identifies the inspected source; collection
confirms names, not a passing run. No new live runs were made. Owner-derived
cases stay local under the privacy rule in `evals/README.md`.

## Tool map inspected

These are implementation pointers, not execution evidence for every row.

| Surface | Code reference |
| --- | --- |
| Host catalogue and per-Work exposure | `src/personal_agent/agent_runtime.py:DEFINITIONS`, `Capabilities.offered_tools`, `Capabilities.definitions` |
| CLI MCP catalogue | `src/personal_agent/mcp_bridge.py:serve` (same host definitions) |
| Browser and public search/page reads | `src/personal_agent/agent_runtime.py:Capabilities.execute`, `src/personal_agent/local_tools.py:LocalTools`, `PublicPageReader` |
| Files and memory | `src/personal_agent/agent_runtime.py:Capabilities.execute`, `src/personal_agent/memory_service.py:MemoryService` |
| Schedules and preparations | `src/personal_agent/preparations_service.py:PreparationsService` |
| API and configured connectors | `src/personal_agent/agent_runtime.py:Capabilities.api`, `src/personal_agent/owner_mcp.py:available` |
| Conversation surfaces | `src/personal_agent/telegram_surface.py:TelegramSurface`, `src/personal_agent/mattermost_surface.py:MattermostSurface`, `src/personal_agent/slack_surface.py:SlackSurface` |
| Code and optional skills | `src/personal_agent/code_run.py:CodeRunner.run`, `src/personal_agent/skills.py:SkillBinding.load`, `skills/` |

Message delivery to the owner does not prove arbitrary third-party messaging.
A configured connector may extend the host catalogue. Skill installation or
loading does not prove a task outcome. Every bundled scenario has row tags;
existing tags and all other scenario content are preserved.
