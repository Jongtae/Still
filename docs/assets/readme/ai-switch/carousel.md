# Localized homepage carousel

The homepage tells **one continuing coffee conversation across an AI switch**.
It uses the same owner-supplied Telegram session from **2026-10-12** as the
[archived site clip](clip/README.md), with digests and segments preserved in
[the clip manifest](clip/manifest.json).

## Story and evidence

1. **Context before switching:** Jardin is already in the cart, and Momos
   Coffee Es Chocolat 500 g had been suggested for lattes. The prior
   recommendation is an explicit **summary reconstructed from the later
   assistant reply**, not a newly supplied screenshot of the original turn.
   The recorded `/ai` output shows Codex immediately before switching.
2. **Change the worker:** `/ai claude-code` changes the selected AI to Claude
   Code. The illustration condenses the recorded change receipt.
3. **Continue the conversation:** the owner says “not the Jardin, something
   pricier this time.” Claude Code refers back to the Momos beans it had
   earlier called best for lattes. The owner does not restate that recommendation.

Every image repeats the earlier context so a visitor can understand any slide
without first seeing another. The summary label stays visible, and the
context is separate from the current request and response. This does not
fabricate a verbatim earlier owner utterance or establish who made the
original recommendation. The chosen AI before the switch is an observed
configuration, not proof of the worker used for every earlier turn.

The clip stops at the subsequent replacement offer. After the owner accepted,
the run removed the old beans but failed to add the replacement and hit a
limit; that outcome is not in the clip. Neither the carousel nor its source
establishes a completed swap, checkout, payment or release validation. The
images represent what the assistant reported, not independent tool/account
traces or proof of identical capabilities across models.

## Localization and rendering

[carousel-copy.json](carousel-copy.json) contains explicit English, Korean,
Japanese and Simplified Chinese copy and source references. These are
translated, condensed **reconstructions**, not screenshots of localized
application UI. Korean is also redrawn.

Each locale has three mobile SVGs and three `.wide.svg` desktop SVGs
(24 images total). Native `picture`/`source` selects the composition.
Regenerate with `python3 scripts/build_site_carousel.py`; it uses only the
standard library. Earlier-context summaries, AI-transition labels, dialogue
and evidence disclosures remain in both layouts and in accessible alt text.

The October 8 [screenshots](manifest.json) and October 11
[README demo](../demo/README.md) remain separate historical records. They are
not spliced into this session or used to imply that every remembered
preference/research example demonstrates a model switch.

The owner-requested visual design uses a neutral canvas, centered hero, wide
demo, prominent installation/Engineering/contribution entry points and
alternating feature rows. No third-party artwork or product promises are copied.
