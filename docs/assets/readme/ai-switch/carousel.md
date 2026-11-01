# Localized homepage conversation examples

The three images are separate examples, not three stages of one conversation. Each contains its own dialogue in reading order. No sidebar reconstructs an unseen earlier conversation, and no dialogue is moved across sessions.

| Example | Source | What the excerpt shows |
| --- | --- | --- |
| Remembered coffee preference | [October 11 screenshot figure](../readme-examples-v4.ko.png), [demo manifest](../demo/manifest.json) | The owner asks about a remembered latte, then says their beans are running out. The reply refers to the earlier taste and asks about brewing equipment. The exact menu name remains unconfirmed. |
| F1 research continued on Amazon | Same October 11 sources | The initial F1 model question and the follow-up asking for Amazon results. The reply identifies Bburago 1:43 models and recommends a McLaren MCL38. Final-total and unverified-step disclosures remain. Prices are omitted rather than presented as current. |
| Switch AI in the same conversation | [October 12 clip record](clip/README.md), [clip manifest](clip/manifest.json), [localized source copy](clip/copy.json) | `/ai` identifies Codex, `/ai claude-code` changes the AI, and the follow-up receives a reply recalling the earlier Momos Coffee recommendation. |

## Evidence boundaries

These are translated, condensed SVG redrawings, not pixel-for-pixel product screenshots. Assistant branding is normalized to Still. All four locales have the same scenes, message order, dates and source records. The English F1 owner messages retain the source wording; other languages translate them. Ellipses mark cuts within replies. The research continuation marker is editorial, not an assistant utterance.

The October 11 recordings name no application revision or model. Those examples therefore identify the assistant only as Still and do not claim to demonstrate a model switch.

The October 12 model-switch excerpt does not contain the original Momos Coffee recommendation. It shows the later reply recalling it; no earlier recommendation turn is invented. The clip ends at the recommendation. The later requested cart swap removed Jardin but failed to add Momos Coffee and reached its limit. Neither the images nor the page claim a completed swap.

Responses are not independent verification of an external service operation or repeatability evidence for the current release. No checkout or payment is shown. The older [October 8 screenshots](ai-switch-conversation.png) remain available with their own [manifest](manifest.json), but are not used in this carousel.

## Reproduction

`carousel-copy.json` holds the localized dialogue and per-example provenance. `scripts/build_site_carousel.py` uses Python's standard library to render 24 SVGs: three examples × four languages × narrow/wide layouts. Run it from the repository root. No models, account actions, image-generation service, browser runtime or new dependency are needed. Text is escaped; the SVGs contain no scripts or external resources.

The homepage uses native horizontal scrolling, fragment links and keyboard-focusable targets. Each example has a descriptive navigation link, previous/next controls, alt text and a source link. The image URLs include a version query so older coffee-summary or spam images are not reused from cache.
