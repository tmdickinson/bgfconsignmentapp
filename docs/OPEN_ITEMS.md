# Open Items

Decisions still needed, as of 2026-11-07. None of these blocks starting the build.

## Deferred decisions

| Item | Owner | Behavior until decided | Needed by |
| --- | --- | --- | --- |
| Handling of Missing items | Treasurer | Admins can set the status. It blocks that seller's payout and closing the event. | Phase 5, and before the convention |
| Handling of Error items | Treasurer | Set by check-in overrides. Blocks pickup of the item, that seller's payout and closing. | Phase 5, and before the convention |
| Slip layout, field length limits and donate mark | Treasurer | Provisional layout, built in Phase 2: name 60 characters, publisher 40, description 300, condition details 100. Admins can print sample slips from the event page. | Before Presale |
| Square export format and import | Treasurer | Reports list Sale IDs for manual matching | Phase 5 or later |
| Rest of the Post-sale process | Treasurer | Reports and the payout outcomes in SPEC section 11 | Phase 5 or later |
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
| A volunteer in a two-register conflict can still cancel their own sale | Not stated in SPEC. To confirm during Phase 4. |
| A seller can take back an Abandoned game if they arrive late, with the same fee | SPEC section 11 |
| An open Sold flag blocks that seller's payout and closing | SPEC section 11 |
| Volunteers can see past events they had access to only for their linked seller account, within its 30 days | Not stated in SPEC. Built this way in Phase 1. |
| If a seller returns after Check-in Complete, a volunteer can resume, and the report is updated | Not stated in SPEC. To confirm during Phase 3. |
| A scan of another seller's item during a check-in is an error and isn't accepted | Not stated in SPEC. To confirm during Phase 3. |
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
