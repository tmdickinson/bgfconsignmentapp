# Consignment Store App: Requirements

Buckeye Game Fest. Approved 2026-11-05.

This is the authoritative statement of what the app does. It combines the [original brief](history/01-original-brief.md), the [board's revised proposal](history/02-revised-proposal-2026-11-03.pdf) and the treasurer's answers ([round 1](history/03-treasurer-answers-round-1.md), [round 2](history/04-treasurer-answers-round-2.md)). Where those conflict, this document wins. Anything still undecided is in [OPEN_ITEMS.md](OPEN_ITEMS.md).

## 1. Scope

The app manages the consignment store from seller signup through listing, check-in, checkout, pickup and payout reporting. It is the authoritative system for consignment inventory and sales, and it is reused every year with past records preserved.

- **The consignment store is the only module for now.** Structure the code so other convention modules can be added later.
- **No public access.** Every page except login and password setup requires sign-in. There is no self-registration.
- **Online only.** No offline mode.
- **No payments in the app.** Square POS handles all payments, cash and card. The app never processes payments or stores card data.
- **No payouts in the app.** It reports what is owed and records what happened.

### Not being built

- Voids, refunds or partial changes to completed sales
- Adding games at check-in
- Printed master sheets (the fee keeps the name)
- QR codes and webcam scanning
- Copying items from a previous event
- Linking seller accounts across events
- Reopening a closed event or moving a status backward

## 2. Roles and accounts

| Role | Scope | Access |
| --- | --- | --- |
| Admin | Global, never expires | Everything in every event, regardless of status. Accounts, settings, price windows, financial reports. |
| Volunteer | Global, never expires, per-event access granted by an admin | Items in events they can access, grouped by seller and searchable. Check-in, checkout and pickup. |
| Seller | One account per event | Only their own games and reports for that event. |

Access is enforced on the server for every page and action.

### Staff accounts

- The first admin is created by a developer through a secure command. Admins create all other staff accounts.
- Volunteers can be added or granted event access at any time.
- Admins can create, edit, deactivate and manage all accounts.

### Seller accounts

- A returning seller gets a new, unconnected account each event.
- **From the Google Form:** the app retrieves new submissions on a schedule or on demand, creates the account immediately and emails a set-password link. Field mapping is configured per event. The form is expected to provide seller name, email and preferred payment method.
- **CSV import** is the backup and follows the same rules.
- Each form response is processed only once. Imports never change existing accounts.
- Duplicate submissions for an event, and any submission outside Presale, go to an admin review list and don't create an account.
- On first login, sellers see all selling rules and fees and must agree to continue. Listing items also constitutes agreement. The time of acceptance is recorded.
- Sellers can log in until 30 days after the event moves to Post-sale. After that, logins for that event stop working and the data stays available to admins.

### Staff who are also sellers

- Volunteers and admins register as sellers the same way as everyone else, including submitting the form.
- For an active event, their account page shows **Seller for this event?** with fields for their Seller ID and that seller account's password. A correct match links the accounts and applies the fee waivers for their category, with no admin approval.
- Once linked, they can open that seller account from their staff account. Admins can list staff who are sellers for each event.
- Staff can't check in their own games, including admins. They can sell their own games, because lots are mixed and a buyer may hold a game from every volunteer.

### Sign-in

- Sellers sign in with Seller ID or email. Staff sign in with Volunteer ID or email. If an email matches more than one active account, the app asks which to open.
- Passwords are hashed, the site is HTTPS only, and repeated failed logins are slowed down.
- Password setup and resets use emailed links.

### Admin badges and PINs

There are two kinds of admin action:

1. **Admin screens:** normal admin login.
2. **Overrides on a volunteer's screen:** the admin scans their badge (Admin ID) and enters a 4-digit PIN, plus a note where required. The action is logged under that admin. It doesn't sign them in.

The badge and PIN are for overrides only. Five wrong PINs lock the badge. Admins can reissue a lost badge. Build only the overrides this document names.

## 3. Identifiers

Every record has an internal primary key separate from its displayed ID. Displayed IDs are never reused.

| ID | Format | Example | Rules |
| --- | --- | --- | --- |
| Event ID | `XXXX` | `F26A` | Set by an admin. Unique across events. Changeable during Setup only. |
| Seller ID | `EVENT-###` | `F26A-042` | Sequential from 001 within the event. |
| Item ID | `EVENT-SSS-####` | `F26A-042-0001` | Event ID, seller number and a four-digit number sequential per seller from 0001. Each new slip version gets a new Item ID. |
| Volunteer ID | `VOL-XXXX` | `VOL-KP27` | First and last initials plus two random digits. Globally unique and permanent. |
| Admin ID | `ADM-XXXXXX` | `ADM-7K2P9W` | Six random characters. Globally unique. Printed as a scannable badge. |
| Sale ID | `XXXX-XXXX` | `KP27-7M4C` | Last four characters of the staff ID plus four random characters. Unique within the event. Canceled sales keep theirs. |

- Random characters come from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`, which leaves out 0, 1, I and O.
- Entry ignores case. When typing, volunteers may enter just `042-0001`, which the app reads as the current event. A scanned code from another event is rejected.

## 4. Event setup and statuses

### Settings

- Event ID, name and dates
- Number and names of price windows, locked once any item is added
- Scheduled start and end for each window, which admins can always change
- Listing fee structure and amount
- Commission as a whole percentage
- Unsold item fee
- Charge unsold item fees for donated items? (Yes/No)
- Charge sellers for negative balances? (Yes/No)
- Fee waivers (section 9)
- Google Form link
- Optional scheduled date and time for each status change

All fee settings lock at the first seller signup, except waivers. Settings belong to the event, so changes in a future year never affect past records.

### Statuses

Admins move an event forward one status at a time, with a warning and confirmation. Status never moves backward. Scheduled dates only trigger reminders and never change a status on their own. Individual overrides exist instead.

| Status | Available operations | To leave this status |
| --- | --- | --- |
| Setup | Configure event, fees, settings and volunteers | |
| Presale | Seller registration, listing management, slip generation | |
| Check-in | Check-in, slip reprints, admin carts at the first window. No registration or listing changes. | |
| Sale | Volunteer checkout, price-window management, admin carts | No open carts of any kind |
| Pickup | Returns, donated and abandoned scanning, payouts, admin carts | All conditions in section 11 |
| Post-sale | Operations locked, final reports available | Final status |

## 5. Listings

### Fields

| Field | Rule |
| --- | --- |
| Name, Publisher, Description | Required for a slip. Length limits set with the slip layout. |
| Condition | New / Mint, Good, or Fair / Incomplete, with a text box. The text is required for Fair / Incomplete. |
| Prices | One per configured window. Whole dollars, at least $1, in any order. |
| Bundle | Checkbox with number of items. A bundle counts as one item for fees. |
| BGG ID | Optional. Never printed. |
| Slip size | Either Large or Small. |
| Donate if not sold | Checkbox. |

### Rules

Sellers can only change listings during Presale.

- Listings can be saved incomplete. Generating slips requires every active listing to be complete or deactivated.
- Slip generation covers only active listings without a valid slip.
- After a slip is generated, a seller can view or deactivate that listing but can't edit it without deactivating it. They can still add new listings.
- The active list shows whether each listing has a valid slip.
- Sellers can reprint valid slips by ticking them and pressing a reprint button, through the end of Check-in.

### Deactivating and reactivating

- Deactivated listings are never deleted. They appear in a separate Deactivated Items section and not in active inventory or slip generation.
- Deactivating doesn't change the listing's Item ID or barcode.
- Reactivating asks whether the seller wants to make changes:
    - **No changes:** the existing Item ID stays valid.
    - **Changes:** any change, however small, invalidates the slip. The listing shows that no valid slip exists, and the next generated slip gets a new Item ID.
- All earlier versions stay in history, including prices and condition.

## 6. Sales slips

Slips are generated as a PDF. Sellers print and cut them at home on plain paper. The layout is provisional until reviewed ([OPEN_ITEMS.md](OPEN_ITEMS.md)).

- **Sizes:** Large at 2 per page, Small at 6 per page.
- **Contents:** name, publisher, description, condition, price per window, Item ID, Code 128 barcode of the Item ID, Seller ID and event name.
- **Windows** are shown by name only, so schedule changes don't make slips wrong.
- **Bundles** show the number of items.
- **Donate** items show a bold "DONATE IF UNSOLD" box along the bottom, possibly with a heart mark elsewhere. The PDF uses red, which prints as grey on black-and-white printers.
- Sellers are told to print at 100% / actual size. Barcodes must scan on home printers.

## 7. Item statuses

| Status | Meaning |
| --- | --- |
| Listed | Entered by the seller, not checked in |
| Deactivated | Removed from active listings or rejected at check-in, with history kept |
| Available | Checked in and for sale |
| In checkout | Reserved in an active checkout |
| Sold | Payment received and recorded |
| Missing | Can't be located. Only admins can set this. Handling is not yet defined. |
| Returned | Back with the seller |
| Donated | Seller marked for donation, unsold, kept by the organization |
| Abandoned | Unsold and not collected, kept by the organization. The unsold fee always applies. |
| Error | Exceptional situation that can't be sold. Handling is not yet defined. |

Every status change records who, when and any note. Only Available items can join a new checkout.

## 8. Check-in

Every game must already have a listing and a valid slip. No games are added at check-in. Each listing can be checked in only once, whichever slip version is scanned.

### Normal flow

1. The volunteer selects the seller and scans a slip.
2. The screen shows that Item ID's information. The volunteer compares it with the slip and the game.
3. Scanning the next barcode accepts the current item, which becomes Available. The accepted Item ID stays with the item, and **checkout uses that version, never the seller's latest edit.**
4. If anything doesn't match, the volunteer rejects the item with a reason. It becomes Deactivated and appears on the report.
5. **Confirm Check-in** accepts the last scanned item and shows a report: number checked in, items not checked in (still Listed), and any errors with their outcomes.
6. The volunteer chooses **Check-in Complete**, which saves the report to the seller's account, or **Check in Additional Items**, which returns to scanning.

### Exceptions

| Scan result | What happens |
| --- | --- |
| Barcode doesn't exist, or belongs to another event | Not accepted. Added to the report. |
| Outdated Item ID | Alert with prices and condition from the slip beside the latest version. The volunteer confirms the seller agrees and the information is accurate, and the item is accepted on the slip's information. |
| Listing deactivated | Same alert and confirmation. Acceptance reactivates the listing on the slip's information. |
| Outdated and deactivated | One combined warning and one confirmation. |
| Rejected at any of the above | Status becomes Deactivated. Added to the report. |
| Already checked in, accidental double scan | The volunteer selects **Double Scanned Item**. Nothing changes. |
| Already checked in, two games with one slip | Both are set aside for an admin, who decides which game has the correct slip. Badge, PIN and note are required to continue. Only one game can be associated with the listing. |
| Already checked in, anything else | Check-in stops until an admin investigates and overrides with badge, PIN and note. The item becomes Available or Error. |

All errors, overrides and resolutions go on the seller's check-in report, and overrides also record the admin. Items not checked in stay Listed. Sellers can't withdraw items after check-in.

## 9. Fees and payouts

### Fees

| Fee | Default | Basis |
| --- | --- | --- |
| Commission | 10% | Each item's actual sale price |
| Listing fee, master sheet option | $2.00 per sheet | Items checked in, 20 per sheet, partial sheets count as full |
| Listing fee, seller option | $0 flat | Per seller |
| Unsold item fee | $0.25 per item | Checked-in items not sold, as below |

- Each event uses one listing fee option, never both.
- **Only sellers with at least one item checked in pay any fee.**
- **Unsold fee:** applies to Returned and Abandoned items that were checked in, and to Donated items if the event setting is Yes. It doesn't apply to Sold or Missing items, or to items never checked in. A seller who takes back an item marked for donation pays the fee, as it is now Returned.
- Before an event closes, unsold fees still in progress are shown as projected.

### Net payout

Net payout = Sales − Commission − Listing fee − Unsold item fees

| | Seller A | Seller B |
| --- | --- | --- |
| Items checked in | 23 | 5 |
| Items sold | 15 | 0 |
| Sales | $120.00 | $0.00 |
| Commission (10%) | −$12.00 | $0.00 |
| Listing fee | −$4.00 (2 sheets) | −$2.00 (1 sheet) |
| Unsold fees | −$2.00 (8 items) | −$1.25 (5 items) |
| Net, negative balances not charged | $102.00 | $0.00 |
| Net, negative balances charged | $102.00 | Seller owes $3.25 |

**If negative balances aren't charged,** the app shows a $0 payout, never a negative amount or an amount owed. The forgiven total appears in admin reports.

**If they are charged,** the app records the amount owed. An admin marks it Collected or Not collected, and an external reference is optional.

### Waivers

Admins can choose which fees are waived for volunteers, for admins and for individual sellers. Waivers can change until the event closes, and can cover any of the three fees. The app records the admin who set each one. Waivers don't change the event's fee structure and have their own report.

### Money rules

- All money is stored in whole cents, with no floating point.
- Commission is rounded half up per item.
- Each sale line records the window and price charged. Reports use those figures and never recalculate history.
- Square's processing fees are absorbed by the convention and never affect seller figures.
- **Revenue counts only fees actually kept.** Waived, forgiven and uncollected amounts are shown separately.

## 10. Price windows and checkout

### Active window

- An admin chooses the active window. The app never changes it based on the clock, and the last window ends when an admin closes it.
- Changing the window is blocked while any regular cart is open. Admins can see open carts and cancel stuck ones. Admin carts don't block a change.
- Changes are logged with the admin and time.
- Registers show the active window prominently. Once the next window's scheduled start has passed without a change, registers show a warning at the top of the screen.
- Regular checkout runs only during Sale status with an active window.

### Admin carts

Admin carts let board members and volunteers buy outside the regular windows. An admin opens one from an admin screen, and every one appears on a report with the admin who authorized it.

| When | Price window |
| --- | --- |
| Check-in status | First window only |
| Sale status, before the last window closes | One chosen window for the whole sale |
| Sale status, after the last window closes | Chosen per item |
| Pickup status | Chosen per item, or one window for the whole sale |

### Building a sale

- A checkout belongs to the volunteer, not the laptop. Each volunteer has one active checkout, and each register one active sale.
- Every cart creates a permanent record, along with any removed games, Sale ID and cancellation.
- Logging in on another register moves the open checkout there. The old register can't change it and shows that it has moved.
- Scanning an Available item adds it to the cart and reserves it (In checkout).
- The screen shows the item count and total prominently, each item with its price and a **Remove** action, and an always-available **Cancel Sale** with a confirmation prompt.
- Removed items stay reserved until the sale is resolved. An item removed by mistake can be scanned back into the same cart before the Sale ID is created.

### Scan errors

A rejected scan never changes the item's status or the cart, except in a two-register conflict.

| Scan | Result |
| --- | --- |
| Already in this cart | Error, not added twice |
| Not Available (Listed, Deactivated, Missing, Returned and so on) | Error, the volunteer keeps selling |
| Held and removed in another cart | Error for the scanning register only, showing who holds it |
| Held on an active line in another cart | Two-register conflict, below |
| Sold | Error. The volunteer sets the game aside, not back on the tables, and keeps selling. See below. |

### Two-register conflict

If a register scans an item on another cart's active line, the physical game is probably not with the first buyer, so a charge may be at risk.

- **Both** registers get an alert and can't proceed or record payment. Registers check the server every 2–3 seconds, and the server enforces the block regardless.
- An admin clears it with badge and PIN, choosing which sale keeps the item. Taking it from a sale that already has a Sale ID cancels that whole sale.
- All of this is logged.

Separately, an admin can investigate and release an item held in another checkout from an admin screen, which is also logged. A sale with a Sale ID must be canceled as a whole to release an item.

### Sold item scanned

This applies at checkout and at pickup. The game is never added to the cart. An alert goes to admins, and the item is flagged. The volunteer sets the game aside and continues. Only an admin can close the flag, by taking possession with a note, using badge and PIN. If the sale was valid, for example a buyer who left the game behind, the admin holds it for the buyer.

### Payment

1. **Proceed to Payment** creates the Sale ID, locks the contents and total, and shows a summary: Sale ID, item count, total due, restock count and names, with **MOVE THESE ITEMS TO THE RESTOCK TABLE NOW**. Restock items are those scanned and then removed.
2. The volunteer takes payment in Square, entering the Sale ID so it can be matched later.
3. Only two actions remain, each with a confirmation prompt:
    - **Payment Received:** the sale is complete and locked, purchased items become Sold and removed items become Available.
    - **Cancel Sale:** all items in the sale become Available, except any an admin has since changed to another status. The Sale ID stays in history.

A sale isn't complete until Payment Received is confirmed.

### Sale override

Completed sales are final: no edits, removals, reversals or refunds. There is one exception, for when Payment Received was recorded but payment couldn't be collected. An admin uses badge, PIN and a note to return the whole sale to the payment step, where Payment Received or Cancel Sale is chosen. It is blocked if a payout has been recorded for any seller in that sale.

## 11. Pickup and closing

The event can only enter Pickup with no open carts.

### Returns

Volunteers scan each game as it goes back to its seller:

| Status when scanned | Result |
| --- | --- |
| Available | Returned |
| Listed | Error shown, seller may take it, Returned |
| Deactivated | Error shown, seller may take it, Returned |
| Donated | If the seller confirms the donation it stays Donated. If they want it back, Returned. |
| Abandoned | If the seller wants it back, Returned. The fee is unchanged. |
| Sold | Not returned. See "Sold item scanned" in section 10. |
| Error | Admin must resolve it first |

Pickup errors and overrides are recorded in the item's history.

### Donated and abandoned scanning

On one admin screen, every remaining unsold game must be scanned so that each is accounted for. Items the seller marked for donation become Donated, and all others become Abandoned. A report lists what the organization should hold at the end.

### Payouts

A seller's payout can be recorded only once all their checked-in games are accounted for: Sold, Returned, Donated or Abandoned, with no open Sold flags. Recording it freezes their figures.

| Outcome | Reference |
| --- | --- |
| Paid | Transaction or check number required |
| Nothing due | None, set automatically |
| Balance forgiven | None, set automatically and totaled in reports |
| Balance collected | Optional |
| Balance not collected | Optional, final and shown in reports |

Sellers with nothing checked in owe nothing and need no outcome.

### Closing

Moving to Post-sale closes the event. It requires:

- No items Available, In checkout, Missing or Error
- No open Sold flags or two-register conflicts
- A payout outcome for every seller who delivered items

Closing freezes all figures and creates each seller's final report. It can't be undone, so any error found afterward is corrected outside the app.

## 12. Reports

All reports export to CSV. Past events stay available to admins.

**Sellers:** their own games, sales (window, price and fees), net or balance, check-in reports and final report.

**Admins:**

- Financial report, per seller and in total: gross sales, each fee, net payout or negative balance, preferred payment method and payout outcome
- Revenue: fees kept, with waived, forgiven and uncollected amounts separate
- Completed sales by Sale ID, plus the transaction detail needed to investigate discrepancies
- Fee waivers and who set them
- Admin-cart sales and who authorized them
- Donated and abandoned items
- Staff who are sellers, per event

Reconciliation against Square uses the Sale ID. The import is deferred ([OPEN_ITEMS.md](OPEN_ITEMS.md)).

## 13. Audit trail

Records are never physically deleted. The app logs who and when for: every item status change, account changes, imports, price-window changes, overrides, checkout removals and cancellations, completed sales, administrative releases, conflict resolutions, waivers, payouts, status changes and event closing.

## 14. Equipment and screens

- **Registers are laptops.** Screens should still have large touch targets and a fast, keyboard-friendly flow.
- **Scanning uses the existing hand scanners** acting as keyboard input, with manual entry always available.
- **Double-sale prevention must hold at the database level** across registers.
- **Branding must be easy to change.** There is no logo yet, and it will change.
