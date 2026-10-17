# README demo

Each public README shows the demo GIF for its language: `demo-v5.en.gif`, `demo-v5.ko.gif`, `demo-v5.ja.gif` and `demo-v5.zh-CN.gif`. All four use the same Telegram and browser recordings (Korean interface). A text column beside the recording gives the scene title and the owner's line in the README's language.

| | |
| --- | --- |
| Size | 1200 × 720, shown at 800 × 480; 65.3 s, looping; about 6.7 MB each |
| Scenes | 1. a remembered coffee taste · 2. an F1 model search continued on Amazon · 3. beans that fit that taste · 4. the chosen beans in the cart after sign-in |
| Sources | seven owner-supplied screen recordings made on 2026-10-11 (digests in [manifest.json](manifest.json)) |
| Selected for public use | by the owner on 2026-10-12 (#1383) |

When the demo content changes, give the files a new name (for example `demo-v6.<lang>.gif`) and update the READMEs. GitHub, browsers and the GitHub app cache images by URL, so a changed file under the same name can keep showing the previous version.

## Editing

Waiting time is removed and some typing and scrolling are sped up. Source pixels are cropped and resized; nothing is blurred, masked or simulated. Scene notes hold for 1.8 seconds with 0.2-second dissolves, and the final cart confirmation holds for 4.8 seconds. The sign-in screens and the first cart screen are left out; the cart shown is the later screen after the owner signed in. The layout uses the site's palette and the Figtree font (SIL OFL). [manifest.json](manifest.json) lists every segment with its source time and the localized captions.

The README example figure `../readme-examples-v4.<lang>.png` (1800 × 1320) is cut from the same recordings: remembered taste, the F1 search continued on Amazon, and the chosen Jardin Espresso Blend 500 g, quantity 1, in the signed-in cart.

## Pace

`demo-v5` is the `demo-v4` edit slowed so the replies can be read (#1387). Only the frame delays changed, so the frames, colours and file size are the same: reading holds last twice as long, scene title cards 1.4 times and typing or scrolling 1.5 times (35.9 s → 65.3 s). [retime.py](retime.py) applies it, using the segment timeline in [manifest.json](manifest.json); the segment times in the manifest are for the v4 pace.

## Evidence class

The demo is edited from real sessions. The screens show what the PA replied; they are not an independent trace of the account, tool calls or checkout, and the recording names no application revision or model. No payment is shown or claimed. It is not a repeatability check of a published release. See [product status](../../../product-status.en.md) for the evidence behind each area.
