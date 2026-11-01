# Localized homepage conversation examples

The first two images excerpt separately sourced real conversations. The third is an illustrative conversation, newly written at the owner's request to show a complete recommendation → AI switch → contextual follow-up. Each image contains its own dialogue in reading order. Recorded sessions are not spliced together.

| Example | Source | What the excerpt shows |
| --- | --- | --- |
| Remembered coffee preference | [October 11 screenshot figure](../readme-examples-v4.ko.png), [demo manifest](../demo/manifest.json) | The owner asks about a remembered latte, then says their beans are running out. The reply refers to the earlier taste and asks about brewing equipment. The exact menu name remains unconfirmed. |
| F1 research continued on Amazon | Same October 11 sources | The initial F1 model question and the follow-up asking for Amazon results. The reply identifies Bburago 1:43 models and recommends a McLaren MCL38. Final-total and unverified-step disclosures remain. Prices are omitted rather than presented as current. |
| Continue an earlier recommendation with another AI — illustrative | Based on the use case in the [October 12 clip record](clip/README.md) and [clip manifest](clip/manifest.json); the displayed dialogue is newly authored | An initial latte preference leads to two named bean recommendations. After Codex → Claude Code, “the second one” is resolved to Momos Es Chocolat, with the latte/taste conditions retained. |

## Evidence boundaries

The first two are translated, condensed SVG redrawings, not pixel-for-pixel product screenshots. Assistant branding is normalized to Still. All four locales have the same scene types, message order and provenance. The English F1 owner messages retain the source wording; other languages translate them. Ellipses mark cuts within recorded replies. The research continuation marker is editorial, not an assistant utterance.

The October 11 recordings name no application revision or model. Those examples therefore identify the assistant only as Still and do not claim to demonstrate a model switch.

The October 12 record does not contain the original Momos Coffee recommendation. On 2026-11-01 the owner requested an appropriate new example instead. The third image therefore labels itself an illustrative dialogue rather than a transcript and has no recording date. Its messages, named engines, ordering and preference recall describe the intended flow; they are not additional observed execution evidence. Its `based_on` references establish the source of the use case, not verbatim dialogue provenance. No availability, price, successful cart action or payment is claimed. The original clip and its record retain the later failed swap outcome.

Responses are not independent verification of an external service operation or repeatability evidence for the current release. No checkout or payment is shown. The older [October 8 screenshots](ai-switch-conversation.png) remain available with their own [manifest](manifest.json), but are not used in this carousel.

## Reproduction

`carousel-copy.json` holds the localized dialogue and per-example provenance. `scripts/build_site_carousel.py` uses Python's standard library to render 24 SVGs: three examples × four languages × narrow/wide layouts. Run it from the repository root. No models, account actions, image-generation service, browser runtime or new dependency are needed. Text is escaped; the SVGs contain no scripts or external resources.

The homepage uses native horizontal scrolling, fragment links and keyboard-focusable targets. Each example has a descriptive navigation link, previous/next controls, alt text and a source link. The image URLs include a version query so older coffee-summary or spam images are not reused from cache.
