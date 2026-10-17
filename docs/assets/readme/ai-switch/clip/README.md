# Site AI-switch clip

`site/assets/still-ai-switch.<lang>.mp4` (1200 × 720, 21.2 s, no audio) and its still `still-ai-switch-poster.<lang>.jpg` show one real Telegram session from 2026-10-12, Korean interface:

1. `/ai` shows Codex doing the work; `/ai claude-code` switches to Claude Code.
2. “쟈뎅 말고 오랜만에 비싼 걸로 해볼까해” (not the Jardin, something pricier this time). Claude Code recalls the Momos Coffee Es Chocolat 500 g it had earlier called best for lattes and offers to swap it for the Jardin in the cart.

The clip ends there (#1393). After “응 바꿔줘” (yes, swap it) the run removed the Jardin but could not add the Momos Coffee beans: the grind-option dialog did not open and the run hit its limit. That result is not shown, and the site copy makes no cart claim.

The site shows it with native video controls and no autoplay, so visitors start, pause or slow it themselves (WCAG 2.2.2) without any script.

The four owner-supplied recordings are not committed; [manifest.json](manifest.json) records the two that are used, by file name and SHA-256, with every segment's time and crop. To rebuild, name the recordings with the prefixes in `segments.json` and run:

```sh
WORK=/tmp/ai-switch-clip node docs/assets/readme/ai-switch/clip/shells.cjs
WORK=/tmp/ai-switch-clip python3 docs/assets/readme/ai-switch/clip/compose.py <recordings folder> site/assets
```

`shells.cjs` needs Playwright; `compose.py` needs Pillow and FFmpeg. `copy.json` holds the localized text.

## Evidence class

Edited owner-supplied screen recordings. They show what the PA replied; they are not an independent trace of the account, the tool calls or the cart, and they are not release-specific validation.
