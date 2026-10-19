---
name: agentos-skills
description: How skills work in this Still - what a skill is, how to add one from a public GitHub folder, turn skills on or off and remove one, all through settings_change, and which reviewed reference skills (such as shopping) can be added. Use when the owner asks about skills, or a request needs a skill that is not installed.
license: AGPL-3.0-only
---
# Skills in Still

A skill is optional know-how: written instructions plus packaged text files. It is never a tool or a permission. Every action still runs through the tools this Work has, under their own checks, and a skill's text never proves that something happened. Only an observed tool result does.

## What the owner can ask

All changes go through `settings_change` with category `skills`. Still then asks the owner to confirm, exactly like any other setting.

- **Turn skills on or off:** setting `enabled`, value `on` or `off`. Off, no Work sees a skill.
- **Add a skill:** setting `add`, value is the GitHub folder address of the skill: `https://github.com/<owner>/<repo>/tree/<branch, tag or commit>/<folder>`. Still pins the exact commit, downloads only that folder, and checks it before installing.
- **Remove a skill:** setting `remove`, value is the skill's name.

Read the current list with `settings_read` category `skills`.

## Reference skills Still publishes

These are reviewed reference skills in the Still repository. They are added like any other skill, pinned to the commit current when the owner confirms, and each can be removed on its own. Offer to add one when the owner's request needs it and it is not installed yet.

- **Shopping cart method:** `https://github.com/Jongtae/Still/tree/main/skills/shopping-cart`. Changing an online cart safely: exact product, quantity meaning, read-back, and product price, delivery fee and total kept apart for a final-price answer.
- **Emart mall on SSG.COM:** `https://github.com/Jongtae/Still/tree/main/skills/emart-ssg`. Site know-how. Use it with the shopping cart method.
- **Getting the owner there:** `https://github.com/Jongtae/Still/tree/main/skills/map-directions`. When an answer sends the owner somewhere: when to leave, how to get there, parking, and a route link in the owner's preferred map service.

## What Still refuses

Tell the owner the reason Still gives. Do not look for a workaround.

- A skill that needs scripts, hooks or other executables. Still runs no skill code.
- A skill whose licence cannot be recognised.
- A folder with links, oversized files or a malformed `SKILL.md`.
- A name that collides with an existing package.

## Using a skill in a Work

Load a skill with `skill_load` only when its description fits the request. The owner's request and constraints remain the goal. Read a packaged file with `skill_resource` only when the skill points to it.

A skill that is switched off, removed or updated while a Work runs stops that Work's further tool calls. Say so, and do not repeat an action whose result you have not observed.
