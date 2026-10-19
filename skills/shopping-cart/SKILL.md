---
name: shopping-cart
description: A careful method for changing an online shopping cart in the owner's signed-in browser - match the exact product, variant, pack size and quantity, change only what was asked, then read the cart back before reporting. For a total-price comparison, read the delivery fee and total from the cart and keep them apart from the product price. Use with a site skill for the shop when one is installed.
license: AGPL-3.0-only
---
# Changing a shopping cart

This method works on any shop through the browser tools (`browser_open`, `browser_find`, `browser_click`, `browser_type`, `browser_read`, `browser_sign_in`). A site skill can add where things are on a particular shop. Everything it says is a hint that you check on the page. Nothing here lets you pay. Never start checkout or payment, never press an order or payment control, and never enter an address, payment or card details. Paying is the owner's own step, and Still asks for the owner's approval on every payment action.

## 1. Bind the request

Keep the owner's request whole: the shop they named, the products, the quantities and any constraints (brand, size, organic, budget, delivery type). Do not switch shop, account or delivery type on your own.

Decide what the quantity means before touching anything. Read `references/quantity.md` when the request is not obvious.

## 2. Read the current state first

1. Open the shop's cart. If the result is `login_required`, call `browser_sign_in` for that shop and end your turn telling the owner. Do not hunt for a sign-in page.
   - Never guess whose account the browser is signed in to (the owner's, a family member's, or anyone else's), and never claim an account is signed in unless the page shows it. When asking for a sign-in, just ask the owner to sign in to the shop. Name an account holder only when the page itself shows it.
2. Note what is already in the cart: product, option and quantity. This is the baseline that you must not disturb.
3. If the request is already satisfied (for example, "ensure there is milk" and there is), change nothing and say so.

## 3. Find the exact product

Search on the shop, then match before you act. See `references/matching.md`.

- Same product, same variant (flavor, fat, organic), same pack size and unit (1L versus 900ml×2), from the shop the owner named.
- Check that it is in stock, and note the price shown.
- If several items fit equally, or none fits exactly, do not pick a substitute silently. Name the closest options with their pack size and price, and ask once.

## 4. Change only what was asked, one step at a time

- Use `browser_find` with the product's own name to reach the add or quantity control in that product's own block. Click with `effect` `mutate`. Never click a control whose nearby text names a different product.
- Reach the intended quantity with the fewest actions. If the shop has a quantity field, set it. If it only has +/- buttons, click once per unit, and read the quantity after each click before the next. Stop and re-read if a step does not change the quantity as expected.
- If a hinted label or button is missing or moved, read the page again (`browser_read` / `browser_find`) and look for it. Never click a guessed position.
- If a press is refused because something covers the control (a banner, pop-up or app-install layer), the refusal names that layer and the controls inside it. Close or dismiss the layer with one of those controls, then try the press once more. Do not keep re-reading and pressing the same covered control.

### Replacing one item with another

Add the new item first and confirm it is in the cart. Only then remove the old one. If the new item cannot be added (an option you cannot choose, out of stock, a control that will not respond), leave the old item where it was and report both. Never leave the owner with neither.

## 5. Read the cart back

Open the cart again. Compare it with the baseline:

- The requested line shows the right product and option, at the quantity you intended.
- Every other line is unchanged.

Only that cart reading supports your answer. A clicked button, a pop-up or a "담았습니다" message is not proof.

## 5a. Final price when a comparison depends on the fee

A product price is not a final price when a delivery fee decides the answer, such as "which one is cheaper in total?". Then, in each shop the owner is signed in to:

1. Use the product matched in step 3, with the delivery type the owner asked for. If the owner named none, say which one the item uses.
2. Add only that one item, at the quantity asked (one if none was named), using steps 2, 4 and 5 as above, so the cart is read back before you go on. This is the same reversible cart change as any other here. Change nothing else in the cart or the account.
3. Read the delivery fee and the total as the cart shows them for this item. If the baseline cart held other items, the cart's total and fee can cover them too, and a free-delivery threshold can depend on them. Use a fee and total only when the cart shows them for this item on its own; otherwise report them as covering other items and unconfirmed for this item alone. Do not open checkout to find them, do not enter an address, payment or card details, and do not press an order or pay control.
4. Leave the item in the cart. Report the product price, the delivery fee and the total as separate figures, the exact product and quantity, and that the item is now in that cart so the owner can remove it or pay in the shop's own app or website.
5. If the fee cannot be read from the cart without entering details, opening checkout or pressing an order control, or the shop shows no fee, report the fee as unconfirmed. Do not state a lowest final price. For a shop the owner is not signed in to, say its fee is unconfirmed.

## 6. When you are not sure what happened

After a timeout, an error or a restart, **read the cart before doing anything else**.

- If the change is already there, do not click again.
- If the cart changed in a way you did not cause (someone else, or another assistant sharing the account, changed it), stop. Report what you see, without claiming or repeating your change.
- If you cannot tell whether your click counted, say it is unknown and what the cart shows now.

## 7. Report

Report in one short answer:

- what is in the cart now for the requested items, with quantity and price as read from the cart;
- for a final-price question, the product price, delivery fee and total kept apart, with any fee you could not read marked unconfirmed;
- anything you did not do, and why (out of stock, ambiguous, signed out);
- that the owner pays in the shop's own app or website.

Do not send a cart link as proof. It opens signed out on the owner's other devices. Never put cookies, passwords or order history into a message or memory.
