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
scenario and judgment links while remaining untried. Both `untried` and
`out-of-bounds-by-design` may cite only class S specification links of kind
`scenario`, `judgment-case` or `doc`, never U/I/P/L. Keep those S links: lane G
uses them to select regressions; they do not claim execution.

`fixture-only` needs a concrete U or I citation. Its summary states the tested
subtask and limitations. It never covers every activity under a broad row.
`observed-live` needs a dated P or L run with a revision and a relevant observed
outcome. The historical shopping run stopped at sign-in; it is retained as a
failed L attempt alongside cart fixtures, not evidence of live cart completion.
The presence-eval record is explicitly fixture-only. Historical findings are
checked against current tests before being treated as current failures.

`blocked-by-still` requires a task that actually needs the blocked path, an
open issue recording the block, an existing `path:symbol` in `code_ref`, and
at least one evidence item showing it (S documentation/code inspection or a
U test pinning the behavior). The four kinds are:

- `tool-not-exposed`: the AI needs a capability Still does not offer, and no
  offered tool covers it; for example the history capability requested in #31.
- `login`: Still-side sign-in friction, not merely an owner being signed out.
- `approval-boundary`: a Still approval, accept or budget gate stopping work
  the owner asked for, beyond the deliberate per-action payment gate. #717
  records this kind of complaint, but its owner-request scheduling gate is
  already corrected in the inspected code.
- `routing`: wrong routing or a Still orchestration judgment (including the
  decision model or completion/goal check) that stops or discards valid work,
  such as #767/#768. Scheduling defects that lose a promised watch report or
  end a watch early (#813/#814) also belong here.

The payment gate is a boundary note or `out-of-bounds-by-design`, never a
blocker. Closed/merged fixes such as #761 are not current blockers. An open
issue alone is insufficient when the inspected code already corrects the
relevant behavior. A fixture-backed row is marked blocked only when the
block stops the realistic task; notes name that path and preserve the narrower
fixture result. A block on one route does not claim that every route fails.

`engine-limit` needs evidence attributing failure to the owner's AI. Unknown
causes are not assigned either label.
Physical execution and personal security checks are `out-of-bounds-by-design`;
linked advice scenarios do not change that boundary.

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
The built-in mail/drive connectors are read-only, and there is no dedicated
host tool to message another person. Browser interaction can operate signed-in
web mail in principle, so a missing dedicated send tool does not establish a
`tool-not-exposed` blocker. Personal messages, invitations, colleague updates
and external-contact drafts remain `untried`: no row-specific delivery run or
browser-mail fixture was found. AppWorld's send-text scenario is S only; the
injected Gmail-send effect records in `tests/test_owner_update_redelegation_466.py`
test classification, not message delivery. Drafting and sending are distinct.
A configured connector may extend the host catalogue. Skill installation or
loading does not prove a task outcome. Every bundled scenario has row tags;
existing tags and all other scenario content are preserved.
