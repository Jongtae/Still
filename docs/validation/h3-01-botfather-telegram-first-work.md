# H3-01 BotFather Telegram first-work acceptance

This procedure proves that a personal BotFather bot and an officially
logged-in Codex subscription work for the paired owner. Unit tests cover the
local boundary; the automatic verification records the live delivery without
asking the owner to send a test message or click a confirmation button.

1. In Telegram BotFather, create a dedicated bot with no existing webhook. In
   authenticated local Still settings, paste its token once. Still
   validates the bot and webhook state locally and generates a private,
   time-limited pairing link. Do not put the token in screenshots, logs, or
   reports.
2. Open the pairing link from the owner's private Telegram account. Still
   records the private pairing and queues one idempotent connection check for
   that Telegram generation. A group or another account cannot create it.
3. The check uses a fixed public search query, runs it through the paired
   Codex subscription, and sends the result only to the paired private chat.
   Its durable job, public-source event, and successful Telegram delivery are
   the acceptance evidence. The owner does not need to write a test message.
4. Restart Still and verify that it resumes polling from the durable cursor
   without replaying an uncertain delivery. Existing paired installations can
   queue the same idempotent verification through the local Still API.

The resulting `telegram_first_work_acceptance` report contains only boolean
checks. It excludes message text, Telegram identifiers, pairing links,
BotFather tokens, provider credentials, capabilities, and source payloads.
