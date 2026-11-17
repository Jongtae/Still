# Owner-facing host text audit (2026-11-17)

Part 1 of [#940](https://github.com/Jongtae/Still/issues/940), issue [#945](https://github.com/Jongtae/Still/issues/945). This audits the text Still's own code sends to the owner. The owner's AI writes the answers; this is everything else: Telegram notices, approval and settings cards, receipts and status lines. It is research and a record of one bounded fix, not a contract.

## Why the host text

Both leaks that started #940 came from host text, not from a model answer. [#932](https://github.com/Jongtae/Still/issues/932) was a raw sources JSON in an approval card. [#929](https://github.com/Jongtae/Still/issues/929) was API URLs that the worker rules made the model cite.

The owner's live store for the 30 days before 2026-11-17, counts only:

- 1,069 assistant messages in the conversation, of which 37 carry a host template fragment.
- 154 Telegram notices, none of them written by the AI: memory saved 34, sign-in needed 34, watch result 27, working notes 17, reservation scheduled 17, reservation proposed 10, settings change 7, step approval 6.

## Standard used

[`prompts/voice.md`](../../src/personal_agent/prompts/voice.md) (including the reader-and-form rule of #941), checked against:

- ISO 24495-1:2023: relevant, findable, understandable, usable.
- Grice's maxims of quantity and manner.

Concretely, a notice should:

- lead with what the reader needs;
- carry only detail that changes what they decide or do;
- use plain 해요체 and address the owner directly, not as "소유자";
- show no system names, raw data or internal ids;
- say what happens next.

A value that has to be exact is a different matter: the request being approved, a destination, an amount. It stays exact and complete, but readable and placed under its heading, not dumped.

## Fixed in #945

| Where | Before | After |
| --- | --- | --- |
| Settings, payment, sign-in and status lines (13 messages) | the reader called "소유자" in the third person ("소유자의 AI가", "소유자만 할 수 있어요", "소유자가 작업 카드를 통해 취소했어요") | addressed directly or with the subject omitted; en/ja/zh where they did the same |
| `remote_login.no_tunnel`, `phone_input.no_tunnel` | tool names in parentheses ("ngrok도 cloudflared도 없어요") | the fact and the next step only |
| Sign-in window label in a settings card | the Work id prefix ("요청 1a2b3c4d") | the start of the request it belongs to |
| Long settings card split into parts | "승인 내용 … 승인하세요" on a card whose button is 적용 | "설정 변경 내용 … 적용할지 정해 주세요"; approval parts say "승인해 주세요" (해요체) |
| Approval card exact request | "실제로 보낼 요청" followed by a line starting "보낼 요청:" | one heading |
| Connector payment and change approvals | `service / operation: {"key": …}` JSON | `service / operation`, then one `key: value` line per value (nested values stay exact JSON) |
| Reservation proposal overflow line | hard-coded, formal ("수락되지 않습니다") | catalogued, 해요체, four locales |

Earlier the same day, #933 replaced the raw sources JSON in marked-Work approvals.

## Left as is, with the reason

These need an owner decision or a design of their own, not a wording fix.

- **Memory saved has no undo hint.** Buttons were removed on purpose (#996), and the owner wants memory used in conversation rather than "기억했어요" housekeeping. Whether the notice should exist at all is the owner's call.
- **Reservation scheduled/proposed show the worker brief.** Rows without an AI offer show `[미리 준비] <schedule> — <goal_text>` (up to 1,000 characters), and proposals fold the brief under "매번 실행할 때 맡길 내용". The brief is what will run, so hiding it changes what the owner accepts. An AI-written one-line offer for every row would remove the need.
- **Schedule lines** print an IANA zone outside the home zone ("(America/New_York)") and an RRULE end in UTC. Converting them to the home zone is a schedule-rendering change with its own tests.
- **Watch result header** "지켜보던 일에서 알려 드려요 ({title})." puts the subject in parentheses, and `title` can be the goal brief. Tests identify watch notices by this prefix, so it changes with them.
- **Self-change notice** names a reservation by its id and asks the owner to request the undo by id. Naming it by title needs an undo path that resolves the title.
- **Settings card notes** (`mcp_connect_note`, `skill_add_note`) carry Claude Code, OAuth, commit and branch vocabulary, and the card shows probe and token URLs, tool names and a commit hash. These are what the owner confirms. A readable summary with the exact values folded, like the request card, is the follow-up shape.
- **Request approvals without an AI summary** (api_request, self_set_write) show only a title and the folded exact request, so the owner sees no plain statement of what is asked. The AI's description is optional there. Requiring one is an approval-semantics change, and it needs the escalated review.
- **Browser payment step** puts page state and form action URLs in the main text, with no folded form.
- **Developer mode note** names a model and effort ("gpt-6.1-sol · medium") and the `/dev` commands. That is intended for that setting.

## Which text should move to the AI

C16 keeps Still code to what an AI must not own. Host text has three kinds:

1. **Must stay fixed:** the exact action being approved, its amount, destination and identity; payment and authority warnings; undo and cancel instructions bound to a real control. Rule: exact and complete, readable, with the raw form folded under one heading.
2. **Can be AI-written from host facts:** watch results, reservation offers, working-notes changes and failure explanations. The facts and the control are the host's; the sentence can be the AI's, as watch results and proposal offers already partly are.
3. **Should not be sent:** housekeeping the owner did not ask to hear about.

The follow-ups above mostly belong to kind 1. Moving a notice from kind 1 to kind 2 is a design change and needs its own issue.
