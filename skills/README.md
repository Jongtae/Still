# Still reference skills

This folder holds the reference skills Still publishes. Each one is an [Agent Skills](https://agentskills.io/specification) folder, licensed AGPL-3.0-only like the rest of the repository.

They are not bundled into Still. The owner adds one in conversation ("add a shopping skill"). Still then pins it to an exact commit and checks it like any external skill before installing it, and each skill can be removed on its own (Constitution C16, #974).

| Skill | What it is |
| --- | --- |
| `shopping-cart` | A method for changing an online cart safely: exact product, quantity meaning, change only what was asked, read the cart back, and keep product price, delivery fee and total apart for a final-price answer |
| `emart-ssg` | Site know-how for Emart mall on SSG.COM, from redacted Still observations. Use it with `shopping-cart` |
| `map-directions` | A method for answers that send the owner somewhere: starting point, when to leave, mode, approximate time, parking, and one route link in the owner's preferred map service (reference: KakaoMap) |

Site hints are observations, not guarantees. Pages change, and the method tells the AI to check each hint on the page.
