# Localized homepage carousel

The `site/assets/ai-switch/<locale>-<1..3>.svg` images are translated,
condensed **reconstructions**, not screenshots of localized application UI.
They retell the three panels of `ai-switch-conversation.png`, supplied by the
owner from one real Telegram session on **2026-10-08**. The source digests,
crops and screenshot evidence class remain in [manifest.json](manifest.json).

1. After switching to Claude Code, the owner asks about yesterday's Spam.
   The assistant recalls its earlier report but cannot establish its date,
   and needs login to check the cart. The reconstruction condenses the
   login explanation and temporal caveat without presenting memory as a live read.
2. After login, the assistant reports one CJ Spam Classic 200 g in the cart.
   Its statement that some steps could not be checked remains visible.
3. After switching to Codex, the owner asks to remove it. The assistant
   reports removal and 22 other products remaining. The decision-AI
   fallback disclosure remains in the reconstruction.

These are records of what the assistant said, not independent tool/account
traces, payment evidence, release validation or a claim that the application
rendered in these languages. English, Korean, Japanese and Simplified Chinese
copy is explicit in [carousel-copy.json](carousel-copy.json). Korean is also
condensed and redrawn. The source screenshot includes longer explanations;
the unchanged original remains linked for inspection.

The former homepage video depicts a **different session on 2026-10-12**;
its [provenance](clip/README.md) and historical media remain unchanged.
The carousel does not reclassify that video or the October 11 README demo.

Regenerate the SVG assets with `python3 scripts/build_site_carousel.py`.
This uses only Python's standard library. SVG keeps translated text legible
when enlarged without adding fonts, image-generation services or browser scripts.

Each locale has three mobile compositions and three `.wide.svg` desktop
compositions (24 images total). The homepage selects them with native
`picture`/`source` elements. The desktop composition places a short heading
beside the dialogue; the mobile composition stacks them. Both use the same
translated source text and disclosures. The neutral image treatment follows
the owner-requested homepage redesign: a bright common canvas, centered hero,
wide demo and alternating feature rows, with color confined to demonstrations.
