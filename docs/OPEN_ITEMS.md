# Open Items

Decisions still needed, as of 2026-11-07. None of these blocks starting the build.

## Deferred decisions

| Item | Owner | Behavior until decided | Needed by |
| --- | --- | --- | --- |
| Handling of Missing items | Treasurer | Admins can mark an Available game Missing, or set it back, with a note. It blocks that seller's payout and closing the event. No unsold fee applies. | Before the convention |
| Handling of Error items | Treasurer | Set by check-in overrides. Admins can set it to Available or Missing, with a note. Blocks pickup of the item, that seller's payout and closing. | Before the convention |
| Slip layout, field length limits and donate mark | Treasurer | Provisional layout, built in Phase 2: name 60 characters, publisher 40, description 300, condition details 100. Admins can print sample slips from the event page. | Before Presale |
| Square export format and import | Treasurer | The Completed sales report lists Sale IDs for manual matching | After the first convention |
| Rest of the Post-sale process | Treasurer | Reports and the payout outcomes in SPEC section 11, built in Phase 5 | After the first convention |
| Hosting budget and domain name | Board | Local development only | Before deployment |
| Who authorizes the Google Form connection, and who owns the Google Cloud project it needs | Board | CSV import works without it. The connection is built but needs a Google Cloud client ID and secret. | Before Presale, for the live connection |

## Assumptions to confirm

These weren't explicitly decided. They were filled in while combining the documents, and are easy to change.

| Assumption | Where |
| --- | --- |
| Admin-cart Sale IDs use the last four characters of the Admin ID | SPEC section 3 |
| An Item ID's four-digit number is sequential per seller, across all versions | SPEC section 3 |
| Volunteer ID digits are random from 2–9, in line with the character rules, and a different pair is drawn if the ID is taken | SPEC section 3 |
| Staff sign in with Volunteer ID or email. Admins without a Volunteer ID use email. | SPEC section 2 |
| An item removed by mistake can be scanned back into the same cart before the Sale ID | SPEC section 10 |
| A volunteer in a two-register conflict can still cancel their own sale | Not stated in SPEC. Built this way in Phase 4. |
| A seller can take back an Abandoned game if they arrive late, with the same fee | SPEC section 11 |
| An open Sold flag blocks that seller's payout and closing | SPEC section 11 |
| Volunteers can see past events they had access to only for their linked seller account, within its 30 days | Not stated in SPEC. Built this way in Phase 1. |
| If a seller returns after Check-in Complete, a volunteer can resume, and the report is updated | Not stated in SPEC. Built this way in Phase 3. |
| A scan of another seller's item during a check-in is an error and isn't accepted | Not stated in SPEC. Built this way in Phase 3. |
| One active event at a time is the normal case, though nothing prevents more | Not stated in SPEC. Built this way in Phase 1. |

### Added during Phase 1

| Assumption | Where |
| --- | --- |
| Admins can add a seller by hand in any status before Post-sale, including from a signup on the review list | Event sellers page |
| Signup duplicates are matched by email within the event | Signup import |
| A staff member can link one seller account per event, and a seller account links to one staff member | Seller linking |
| Admins set their own PIN on their account page, confirmed with their password. An admin without a PIN can't approve overrides. | Account page |
| A reissued badge keeps the admin's PIN, unlocks the badge and makes the old Admin ID stop working | Staff page |
| A one-word name repeats its initial in the Volunteer ID, so Cher becomes `VOL-CC##` | Volunteer IDs |
| An event needs at least one price window before it can enter Presale | Status change |
| Staff accounts with neither role can't sign in | Sign-in |
| Set-password and reset links work for 7 days | Emails |
| Volunteers see seller names and IDs, but not emails or payment methods | Event sellers page |

### Added during Phase 2

| Assumption | Where |
| --- | --- |
| Slip size defaults to Large | Listing form |
| Prices go up to $10,000, and bundles have 2 to 99 items | Listing form |
| Changing only the BGG ID while reactivating keeps the slip valid, since it's never printed | Reactivation |
| Choosing "make changes" and then saving with nothing changed keeps the slip valid | Reactivation |
| Generating slips opens a PDF of just the new slips. Reprints are chosen by ticking games. | Seller's games page |
| Admins can view a seller's games and their full history, but can't edit them | Seller admin page |
| Volunteers with access to the event can view a game's history | Item page |

### Added during Phase 3

| Assumption | Where |
| --- | --- |
| Check-in runs only while the event is in Check-in, for admins as well as volunteers | Check-in |
| The game shown and waiting for the next scan is held in the volunteer's browser. If they leave the screen, it stays Listed until scanned again. | Check-in screen |
| For two games with one slip, the admin's decision is recorded and the listing stays checked in. The game without the listing goes back to the seller. | Check-in screen |
| For any other scan of a checked-in game, the admin either leaves its status as it is or sets it to Error. The spec says "Available or Error", which is the same for an Available game. | Check-in screen |
| Typed Item IDs may leave out leading zeros, such as `F26A-1-2` or `1-2` | Scanning |
| Volunteers can browse and search every game in events they can access | Games page |

### Added during Phase 4

| Assumption | Where |
| --- | --- |
| Admins name each event's registers. Each laptop chooses its register once and remembers it. | Registers |
| A register with another volunteer's open sale can't be used for a new sale until that one finishes | Registers |
| After a sale moves, the old register only shows where it went. Moving it back means signing in again there. | Registers |
| Admins can set the active window during Check-in, ahead of Sale, as well as during Sale. Any window can be chosen, not only the next. Choosing one after the last window closed reopens regular checkout. | Sales control |
| One active cart per person, counting regular and admin carts together | Registers |
| Only the slip version accepted at check-in scans at a register | Scanning |
| When an admin gives a conflicted game to the second sale, the volunteer scans it again to add it | Register alerts |
| A completed sale returned to payment by an override is finished on the admin's sale page, not at a register | Sales |
| Admins close Sold flags and conflicts on the register alerts page with badge and PIN, even when signed in | Register alerts |
| A failed first scan doesn't leave an empty cart open. The empty cart is kept as canceled. | Registers |
| The check that blocks a sale override after a payout is connected in Phase 5, when payouts exist | Sales. Done in Phase 5. |

### Added during Phase 5

| Assumption | Where |
| --- | --- |
| A staff member with both roles gets the volunteer and admin waivers combined | Fees |
| A game marked for donation that is still on the tables isn't counted in projected unsold fees, unless donated games are charged | Fees |
| Payouts are recorded only during Pickup | Payouts |
| Nothing due and Balance forgiven are recorded with one button for every ready seller, not silently | Payouts |
| Once a seller's payout is recorded, their games can't change at pickup or by admin status changes | Pickup |
| Pickup is done one seller at a time, and another seller's game is refused | Pickup |
| A Sold game found during donated and abandoned scanning raises a Sold flag, as at pickup | Unsold scanning |
| Sellers can see their sales and fees at any time. The page becomes their final report when the event closes. | Seller statement |
| For sellers not yet settled, the financial report shows the amount currently owed | Reports |
