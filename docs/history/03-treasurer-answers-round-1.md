# Treasurer's answers, round 1 (2026-11-05)

Kept for history. The questions were raised in a review of the revised proposal, and the treasurer's replies are interspersed. Text is as received, apart from layout (the status and ID tables were flattened in the paste and are restored as tables here). Where this differs from [SPEC.md](../SPEC.md), the spec wins.

---

## Problems to fix

**1. Outdated slips can still scan:** any edit invalidates a slip, but the new slip has the same Item ID and barcode, so the app can't tell the old one from the new one. *Recommended:* adding a slip version to the code (`26-042-07-2`), so an old slip gets a clear "out of date" error at check-in.

> When a listing is created, the system assigns it a unique, automatically generated primary key. This internal ID remains associated with the listing record and allows the database to track its history.
> When a sales slip is generated, the listing receives an Item ID and a corresponding barcode. The Item ID identifies the specific version of the listing information printed on that slip.
> Listing deactivated: Deactivating a listing does not change its primary key, Item ID, or barcode.
> Listing reactivated without changes: The existing Item ID and barcode remain valid. Nothing changes.
> Listing reactivated with changes: The system generates a new Item ID and corresponding barcode. The original Item ID and its associated listing information remain in the database history.
> At check-in, the system uses the Item ID scanned from the barcode to identify which version of the listing information to use. At checkin, the volunteer must compare the information on the checkin screen with the physical slip and item. For efficiency, items are accepted by scanning the next barcode. There is an option to reject the item if any information is inaccurate (slip doesn't match system screen) and select a reason. Rejected items are set to deactivated and included in sellers checkin report
>
> Outdated or deactivated sales slips at check-in
>
> Outdated Item ID:
> The system alerts the volunteer that the scanned sales slip contains outdated information.
> The system displays the prices and condition printed on the scanned slip alongside the most recent version in the system.
> The volunteer must confirm that the seller agrees to sell the item using the information on the scanned sales slip and that the information is accurate.
> If confirmed, the item is accepted using the information on the scanned slip, not the current listing.
>
> Deactivated listing:
> The system alerts the volunteer that the listing is currently deactivated.
> The system displays the prices and condition printed on the scanned slip alongside the most recent version in the system, if one exists.
> The volunteer must obtain confirmation that the seller agrees to include the item using the information on the scanned slip and that the information is accurate.
> If confirmed, the item can be accepted using the scanned slip's information.
>
> Both conditions apply:
> If the scanned Item ID is outdated and the listing is deactivated, display one combined warning and require only one confirmation.
>
> In all cases, if the item is rejected, status is set to deactivated and included in seller's checkin report
> Important implementation rule: The accepted Item ID and its associated listing version must be retained with the inventory record. Checkout pricing must use the accepted version, not the seller's latest listing.
> This also means the system must preserve the complete historical information for every Item ID, including prices and condition.

**2. Volunteer linking is open to abuse:** a volunteer can enter any Seller ID and automatically gets that seller's account and fee waivers, with no approval. *Recommended:* also requiring that seller account's password.

> Agree

**3. Logins may collide across years:** a returning seller gets a new account each year, probably with the same email. Should they log in with their Seller ID and password? *Recommended:* yes.

> Seller ID or email address. If the email address is associated with multiple ACTIVE events, ask which they wish to access.

Can they still open old accounts to see past reports? The original spec said yes. *Recommended:* keeping that as read-only access.

> For now, we have decided NOT to connect seller accounts across events. We can manage that by keeping seller data in a unique table for each event. Event data is only visible to sellers for 30 days after the event.

**4. Automatic accounts need email:** if the Google Form creates accounts, the app has to send sellers their login details, and password resets have the same need. *Recommended:* adding a low-cost email service, with a set-password link instead of a temporary password. This may add a little to the budget.

> The google form does not create the accounts, it is just how sellers give us their information for account creation. Your solution is a reasonable solution as I would like this to be as automated as possible.

**5. A wrong Payment Received can't be fixed:** a misclick or failed card leaves the item Sold, and the seller is still owed for it. Should admins be able to record a payout adjustment with a note, leaving the sale itself locked? *Recommended:* that, plus a confirmation step before Payment Received. It would also give admins a way to resolve the "Sold" item error at pickup, which currently has no defined outcome.

> There should be a Payment Received confirmation, same as cancel sale. It's annoying but will prevent missclicks. We can have an admin ability to override a sale.

## Money rules

**6. Square reconciliation:** the app no longer records a Square reference, so nothing links a sale to a line in Square's CSV. *Recommended:* having volunteers type the Sale ID into Square's note field, if the export includes it, and matching on amount and time otherwise. Can your treasurer provide a sample export?

> We are going to put the Sale ID into square when taking payment since we can easily control this format to make it easy to type

**7. Revenue when balances are forgiven:** with negative balances off, Seller B's $3.25 in fees is never collected. *Recommended:* that revenue counts only fees actually kept, with forgiven and waived amounts shown separately.

> Agree.

**8. Flat seller fee:** does a seller who brings nothing still pay it? The proposal says items that never arrive cost nothing. *Recommended:* charging only sellers with at least one item checked in.

> Agree. For all fees, only sellers who deliver items are charged any fees.

**9. Uncollected items:** if donated items are exempt from the unsold fee, does that include games a seller simply didn't pick up? *Recommended:* exempting only items the seller marked for donation, otherwise abandoning games avoids the fee.

> Agree. We need a system status for abandoned games so we know how to assign fees and be able to pull a report of what donated items we should have at the end of the sale. During pickup faze, the admin can access the process for abandoned games and scan them so they get the correct fee charge and are accounted for by the system. These are now assumed to be the same as donated games but the unsold item fee is charged.

## Operations

**10. Listing deadline:** this is gone from the event settings. *Recommended:* that sellers can list and edit only during presale, and that each event status unlocks only its own actions (check-in, sale, pickup). Can admins move the status backward?

> I agree, only during presale. At event set up, we can make it optional to include date/time the event moves from one status to another. I think status can only move forward. We will need to include some override abilities for individual tasks, but that seems less messy than allowing an admin to universally change status

**11. Changing the window during a sale:** *Recommended:* that open carts reprice with a warning, while sales that already have a Sale ID keep their locked total. With manual windows, is the override still needed for anything beyond activating a window? *Recommended:* removing it unless the board has a use for it.

> Price windows have scheduled start and end times, but the active price window is changed manually by an Admin. The system must not automatically switch windows based on the clock.
>
> **Register Warning:** Once the scheduled start time of the next price window has passed, each register must prominently display a warning at the top of the checkout screen indicating that the scheduled price window has changed but the active price window has not yet been updated. This alerts volunteers that the store is operating under a different price window than the current schedule.
>
> **Activating the Next Window:** An Admin manually activates the next price window once the checkout line has been completely cleared. This can not happen while there are any open carts. New checkouts then use the prices for the newly activated window.
>
> **Individual Sale Price Window Override:** Admins can override the active price window for an individual sale from the Admin screen. This allows an Admin to process a sale using a different price window without changing the active price window for the rest of the store. The system must record the override and the Admin who authorized it.
>
> During check in status: admin can open a cart set ONLY to the first price window
> during sale status: admin can open a cart and select a price window other than the active window for the entire sale.
> during sale status, but only after the last price window closes: an admin can open a cart and select the price window for each item.
> during pickup status: an admin can open a cart and select the price window for each item OR set it to a price window for the entire sale.
>
> The purpose of admin cart overrides is to check out board members and volunteers who may not be able to complete their transactions during the regular sale windows.

**12. Admin override at check-in:** the proposal leaves the method open. *Recommended:* the admin entering their own password and a note on the volunteer's screen.

> Actually, let's go with an Admin ID (scanable) + pin after scanning

**13. Master sheets:** the printed sheet is no longer in the check-in flow. Is it now only the name of a fee? *Recommended:* dropping the printout unless volunteers want it.

> We can drop them

**14. Volunteers and their own games:** the rule against checking in or selling their own items is gone from the proposal. Was that intentional? *Recommended:* keeping it, because a linked volunteer also gets fee waivers.

> Checkin NO, Sell YES. Games are sold in mixed lots, so it is possible a person could be buying an item from every volunteer, in which case, none would be able to check out the person.

## Formats to settle

**Seller ID:** `26-042`

> We actually have a bunch of IDs to determine the format for. We should do them all at the same time. See below.

**Barcode:** Code 128 only, with no QR code, so it fits on the small slips. Webcams read these barcodes poorly, so typing the code becomes the backup instead of the camera.

> Our tech can do barcodes or QR codes. Barcodes seem safer since they are more likely to be scannable if slightly damaged. @TED this is to your preference.

**Donate mark:** a bold "DONATE IF UNSOLD" box, which prints clearly in black and white.

> Agree. Along the bottom of the sheet. Maybe an identifier in another area like a heart? Can print in red for color slips, but grey for b/w slips.. But we will need to talk more about the exact sale slip format later.

**Windows on slips:** names only, so changing the schedule doesn't make printed slips wrong.

> Agree

## Assumptions

**Sale reservations.** Only the second register gets the "item held" error, not both as the safeguards table says.

> No, BOTH registers should get the item error and be unable to proceed with their sales. This needs to be . Because the second register should physically have the item, which means the first register has a charge sitting there without an item. It's all around bad. Implement this as a real-time notification or sufficiently frequent refresh. The first register should not silently continue as though the physical item is still with its customer.

"Restock items" means items removed from a sale.

> No, restock items are items brought to checkout, but not purchased, so they need to go back to the sale after the sale they were removed from is closed. The info on the sales summary screen should be all that is needed for volunteers.

If a sale is locked, an admin has to cancel the whole sale to release an item.

> Agree, because this can't happen because all sales are final (admins will only unlock sales when a volunteer accidentally says payment received before it was and payment was not able to be collected.

A canceled sale keeps its Sale ID in the audit history.

> Each time a cart is open, it should create a record with a primary key. Those should always be kept along with any removed games, Sale ID attached to the cart, or cancel action.

**Registers:** an interrupted sale stays with the volunteer and can be resumed from another laptop.

> Yes, when the volunteer logs in on a different register, the current open sale will appear. One active checkout per volunteer.
> A checkout belongs to the volunteer, not the physical laptop.
> A volunteer can resume their checkout on another authorized device.
> Logging in elsewhere transfers access to the existing checkout rather than creating a second checkout.
> Here's how I would expect it to work:
> A volunteer logs in at Register 1 and opens a checkout.
> The system creates the checkout record and associates it with that volunteer.
> Register 1 is recorded as the device currently accessing the checkout.
> If the volunteer logs in at Register 2, the system finds their existing checkout.
> The volunteer can resume it on Register 2. Access transfers to Register 2, and Register 1 can no longer make changes to that checkout.
> If someone tries to use the old checkout on Register 1, they receive a message that it has moved to another register.

**Listings.**

- A bundle counts as one item for fees. > Agree
- Everything except the BGG ID is required before a slip can be generated. > Agree
- Large and Small are a single either/or choice. > Agree
- Descriptions have a length limit so they fit on small slips. > Agree. We will determine that once we set up the slip format. Most fields will have a length limit so we aren't ever cutting off text.

**Fees.**

- All fee settings lock at the first seller signup. > Correct. Wasn't that explicitly specified?
- Waivers can still change until the event closes, and can cover any of the three fees. > Correct
- Missing items don't incur the unsold fee. > Correct, but also, missing item handling needs to be further defined. We will do this at a later date.
- Square's processing fees are absorbed by the convention. > Correct, also already explicitly specified.

**Dropped features:** copying items from last year is gone, as are the printed-sheet items in the original spec.

> Correct.

**Closing:** an event with recorded payouts can't be reopened without a warning, and collection notes on negative balances are optional.

> An event can't be closed until all payouts are resolved. For negative balances where we are not collecting the fee, the system should zero them out (but provide the totals in the reports). For negative balances where we are collecting the balances, and admin will mark if it has been collected, but providing an external reference number is optional.
>
> The Event status determines which operations are available.

| Status | Main permissions |
| --- | --- |
| Setup | Configure event, fees, settings, and volunteers |
| Presale | Seller registration and listing management. |
| Check-in | Check-in operations; no seller registration or listing changes |
| Sale | Volunteer checkout and admin price-window management |
| Pickup | Item returns, abandoned-item processing, and admin payout processing. Donation processing (with abandoned). Handling of any missing or error statuses (these processes are not yet defined) |
| Post-sale | Operational processes locked; final reports available |

> All admins can access every event, regardless of status.
> Status changes are initiated by admins.
> Status changes are forward-only.
> Significant changes require a warning and confirmation.
> Individual task overrides are available without changing the entire event status.
> Scheduled transition dates and times are optional.
> Volunteers can be added at any time.
> Treat scheduled dates and times as reference points and warning triggers, not automatic status transitions.

**Admins who sell:** they link to a seller account the same way volunteers do.

> Correct

## Identifier formats

| Identifier | Format | Example | Rules |
| --- | --- | --- | --- |
| Admin ID | ADM-XXXXXX | ADM-7K2P9W | Six randomly generated alphanumeric characters. Globally unique. Scanned from admin badge, followed by a 4-digit PIN. |
| Volunteer ID | VOL-XXXX | VOL-KP27 | First and last initials plus two randomly generated digits. Globally unique and permanent across events. |
| Seller ID | EVENTID-### | F26A-042 | Event ID plus sequential three-digit number, starting at 001. Unique within the event. |
| Item ID | SSSSS-#### | 042-0001 | Last three characters of Seller ID plus sequential four-digit version number, starting at 0001. New version gets a new Item ID. |
| Sale ID | VOLUNTEERID-XXXX | KP27-7M4C | Last 4 characters of Volunteer ID plus four randomly generated alphanumeric characters. Unique within the event. Never reused, including canceled sales. |
| Event ID | XXXX | F26A | Four Specifically set alphanumeric characters. Must be unique across all events. Assigned at event creation; changeable during Setup only. |

Character rules: for randomly generated alphanumeric portions, use `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`. Excludes 0, 1, I, and O to avoid confusion when reading or typing IDs.

Database rules:

- Every record has its own internal database primary key, separate from its displayed ID.
- IDs are unique within their specified scope and are never reused.
- Volunteer accounts are global, not event-specific.
- Seller accounts and Seller IDs are event-specific.
- Item IDs preserve the history of every listing version and are event-specific
- Sale IDs remain in the audit history even when a sale is canceled and are event-specific
