# Public README visuals

The public READMEs introduce the desired personal-agent relationship, owner control, the structure that makes continuity possible, a short representative conversation, and a first trial. [The documentation map](../../README.md) remains the documentation authority map.

## Header and demo

The READMEs open with a fixed-width character banner (`Still` in the FIGlet *smslant* font, 36 columns so it fits a phone screen, generated with pyfiglet and kept as plain text inside `<pre>`), status badges, and the localized demo GIFs ([`demo/`](demo/README.md)). The badges link to the `validate` and `full-validate` workflows, the latest release, the supported Python version, platforms and licence; their images come from GitHub Actions and shields.io.

The demo is edited from real Telegram and browser sessions, with one GIF per README language (localized scene notes beside the recording): a remembered coffee taste, an F1 model search continued on Amazon, a bean recommendation and the chosen beans in the cart after sign-in. Sign-in screens are left out; nothing is blurred. Its sources, segments and evidence class are in [demo/README.md](demo/README.md) and [demo/manifest.json](demo/manifest.json).

`social-preview.png` (1280 × 640) is the repository's social preview image, set in repository Settings → General → Social preview. It repeats the README banner, tagline and platform/licence facts so shared links match the README header.

## AI switch conversation

`ai-switch/ai-switch-conversation.png` is used by all four READMEs in the *A conversation that continues when the AI changes* section. It crops three owner-supplied Telegram screenshots of one real session (2026-10-08, 10:01–10:08 PM, Korean UI): the switch to Claude Code and the question about yesterday's cart, the sign-in notice and the cart answer after signing in again, and the switch to Codex with the removal. The crops drop the phone status bar, chat header, input bar and, in panel 2, the one-time sign-in link. The owner's display name in the reply quotes is blurred. The switch notices, the owner's requests and the PA's replies stay whole, including the limits the PA stated ("일부 단계는 확인하지 못했어요" and the decision-model fallback note). Source digests, crop boxes and the output digest are in [ai-switch/manifest.json](ai-switch/manifest.json); [ai-switch/build.py](ai-switch/build.py) rebuilds the image from the source screenshots, which are not committed. This is a selected real session, not release-specific end-to-end validation.

## Current introduction

`presence-overview.{en,ko,ja,zh-CN}.svg` is the illustrated overview used by the four READMEs. Three example cards show answering a question, handling an explicit cart request and carrying a delegated meeting decision toward its next preparation. The Owner/PA/Still architecture follows one sourced meeting through its invitation, proposal, earlier decisions, differing relationships, continuing work, authority check and verified result. It distinguishes invitation from acceptance and draft from send. A separate short coffee conversation in the preserved concept figure illustrates ordinary context over time. Original vector icons and dialogue cards carry the explanation; detailed ontology definitions remain in the linked documents.

The visual follows the composition of the owner-provided NVIDIA/Amazon/continuing-conversation reference. It does not reproduce a messaging application, market price or completed purchase. All depicted interactions are illustrative product direction, not observed runs or shipped-integration claims.

The *Everyday use* section shows `readme-examples-v4.{en,ko,ja,zh-CN}.png`: three excerpts cut from the same real recordings as the demo, with localized scene titles and notes around unedited Korean Telegram and cart screens. See [demo/README.md](demo/README.md) for sources and the evidence class. They are not release-specific validation.

The earlier everyday-use images are kept for history. `owner-pilot-conversation-reference.jpg` preserves the owner-provided Korean image byte-for-byte (SHA-256 `7b0266548d75939473c443c76df02dc8cd0cb8136d0dc3a38158dd26f85f26a9`); `owner-pilot-conversation-edited.png` is its first shortened edit ([prompt and provenance](owner-pilot-conversation-edit-prompt.md)); `owner-pilot-conversation.{en,ko,ja,zh-CN}.png` localize the text inside it ([localization prompt and copy](owner-pilot-conversation-localization.md)). They condense real conversations (dinner on the way home, a belt compared before sign-in, a restaurant photo leading to a walk) and redraw the replies as cards; in Telegram the PA answers with text and links.

The overview has locale-specific `.narrow.svg` compositions selected by the README's `<picture>` at viewport widths of 600 pixels or less. Its desktop canvases are 1120 pixels wide and narrow canvases are 420 pixels wide. The everyday-use image uses its locale's repository-owned PNG at every width; captions, alt text and short scene-by-scene prose remain readable where in-image text is too small. The overview is an architecture illustration; the images are reconstructed interaction references. Neither establishes operating evidence. There are no external fonts, image requests or scripts.

## Supporting and historical figures

- `interaction-model.*.svg`, `presence.*.svg` and `continuing-conversation.*.svg` preserve the earlier, more detailed concept figures. Their NASDAQ/Amazon examples and reconstructed conversations remain illustrative product direction, not shipped integrations or observed runs. They are no longer the public README's required layout.
- `presence-scenes.*.svg` preserves the prior localized three-scene illustration. Its embedded `illustrative-headphones.jpg` and `illustrative-restaurant.jpg` assets and [source prompts](scene-photo-prompts.md) remain available for history but are no longer the public README scene.
- `hero.*.html` / `hero.*.png` and `scenes.*.html` / `scenes.*.png` reconstruct condensed v1 synthetic journeys. [English product status](../../product-status.en.md#published-release-journey-illustrations) and [Korean product status](../../product-status.ko.md#공개-배포본의-여정-설명) retain those examples with their evidence boundaries and release limits. These are not live-account screenshots. Their source is [build_readme_visuals.py](../../../scripts/build_readme_visuals.py).

## Editing and visual verification

[build_concept_visuals.py](build_concept_visuals.py) is the standard-library source for the 8 current overview SVGs and 32 preserved concept/scene SVGs. Run `python3 docs/assets/readme/build_concept_visuals.py` after editing a generated SVG. Neither the owner-provided JPEG nor the localized PNGs are generated by this script. Keep the same relationships and evidence boundaries across locales and screen sizes. Do not squeeze glyphs with `textLength` or `spacingAndGlyphs`.

1. Read the whole README as a newcomer: desired relationship → why owner control matters → how continuity works → first trial → references/licence. Diagrams should shorten the explanation.
2. Run `python3 scripts/verify_readme_localization.py` and check local links. This protects semantic parity, localized assets and factual boundaries without requiring the old screenshot sections.
3. Inspect text against the viewport, cards and other text under sans-serif and serif fallback. Check native GitHub desktop width, 375px and 320px in all four locales. Verify image decoding, selected `currentSrc`, alt text and legibility, then read the entire page in order.
4. Keep the reconstructed interactions explicitly illustrative and selected recordings explicitly owner-supplied observations. Published-release evidence, current-main fixture checks, owner-pilot observations and product direction remain distinct in the linked status documentation.

Browser captures and layout measurements establish documentation rendering only. They do not establish live Still behavior.
