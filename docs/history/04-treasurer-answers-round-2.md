# Treasurer's answers, round 2 (2026-11-05)

Kept for history. Follow-up questions with the replies interspersed, followed by Ted's final approval. Where this differs from [SPEC.md](../SPEC.md), the spec wins.

---

## Conflicts and gaps

**1. Old slips look valid again:** `042-0001` has no event in it, and numbering restarts each event. A slip left on a returning game could match a different seller's item this year. *Recommended:* putting the event in the barcode only (`F26A-042-0001`), while volunteers still type `042-0001`. The app would then reject scans from other events.

> We can put the event code at the start of the Item ID, sure.

**2. Clearing a two-register conflict.** *Recommended:* an admin clears it with their badge and PIN, choosing which sale keeps the item. If that means taking it out of a sale that already has a Sale ID, that whole sale must be canceled. If the first register already removed the item from its cart, only the second should get the error, since no charge is at risk.

> Both those clarifications work.

**3. Admin sale override.** *Recommended:* the override requires badge, PIN and a note, and returns the sale to the payment step. There, the volunteer or admin chooses Payment Received or Cancel Sale. Partial changes and refunds stay disallowed.

> I agree with this clarification.

That still doesn't resolve a Sold item scanned at pickup when the sale was valid, such as a buyer who left it behind. *Suggested:* the admin records a note and holds the game for the buyer.

> This is a reasonable solution. From an application perspective, it can not be included in the current open cart and should flag an Admin to take possession of the item and then allow the transaction to continue

**4. Payouts happen before fees are final.** *Recommended:* a payout can be recorded only once all of that seller's items are final. Recording it freezes their figures and blocks any later sale override that would change them.

> Correct. Payout for an individual seller can not happen until all games scanned to available at check in have been properly accounted for. (We still don't have a process for missing games. We are still ignoring that for now).

An event can't close with balances unresolved, so a seller who never pays would block closing forever. *Recommended:* adding "Not collected" as a final outcome, shown in reports.

> Acceptable safeguard

*Recommended:* removing the proposal's "reopen event" process, since statuses only move forward. The trade-off is that any error found after closing has to be corrected outside the app.

> Acceptable safeguard

## Other decisions

**5. Donated and abandoned scanning.** *Recommended:* one admin scanning screen during Pickup. Items the seller marked for donation become Donated, and any other unsold item becomes Abandoned. Moving to Post-sale is blocked while any item is still Available.

> Acceptable safeguard. However, we need to scan them to be sure they are all accounted for.

**6. Seller signups.** *Recommended:* creating the account and emailing the login link immediately, and flagging duplicates and any submissions outside Presale for review. Someone with access to the form will need to authorize the connection.

> Acceptable solution.

**7. The 30-day limit.** *Recommended:* it starts the day the event moves to Post-sale, so sellers always get 30 days with their final report. After that, seller logins for that event stop working.

> Acceptable safeguard

**8. Admin badge and PIN.** *Recommended:* using these only for overrides, with normal admin login still using a password. The app would lock the badge after 5 wrong tries, and admins could reissue a lost one.

> Acceptable safeguard, and yes, only for overrides.

**9. Task overrides.** *Recommended:* building only the ones named so far: check-in errors, admin carts, sale overrides and releasing held items.

> Yes, only build the ones discussed, keeping in mind two general types of admin actions
> 1. Actions from the admin screen(s)
> 2. Override actions taking place on Volunteers screen(s) using badge scan and pin.

**10. Square export.** A sample export is needed to confirm which column holds the Sale ID.

> Can't provide that right now because it needs to be further investigated from the square side. Most of the Post-Sale process is not very developed, but doesn't highly affect most of the rest of the build.

## Build assumptions

**Slip versions:** a new Item ID is created when a slip is generated, not each time a listing is edited. Each listing can be checked in only once, whichever version is scanned.

> Correct. This means that an item that is reactivated will need a make changes confirmation before it becomes listed so the system knows if it should still show an active Item ID.

**Check-in:** Confirm Check-in also accepts the last scanned item. Sellers can't deactivate listings after Presale, but can reprint valid slips through the end of check-in.

> That seems simpler and is a fine starting point.

**Own games:** the rule against checking in your own games applies to admins too.

> Acceptable

**Price windows.**

- The last window ends when an admin closes it, not by the clock. > Correct
- Admin carts with their own window don't block a window change. > Correct
- Admins can see and cancel stuck carts that would block one. > Correct
- Every admin-cart sale appears on a report, since an admin could otherwise choose the lowest price for their own purchase. > Correct

**Restock list:** the app can only list items that were scanned and then removed. Games brought to the register but never scanned won't appear.

> Correct

**Donate mark:** the slip PDF will use red, which prints as grey on black-and-white printers, so there's only one version.

> Acceptable

## Final approval (Ted, 2026-11-05)

Plan approved. Also agreed:

- Sold item scans: the volunteer sets the game aside and keeps selling while an admin is alerted, and only an admin can close out the flagged item.
- Start a git repository and commit at the end of each phase.
- Save all specs in the repo, with no code yet.
