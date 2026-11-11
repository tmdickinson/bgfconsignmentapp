# Project: Consignment Store App for a Board Game Convention

## Context
We run an annual board game convention with a consignment store: attendees ("sellers")
bring used games, we sell them on their behalf during the convention, and we pay them out
afterward. Build a web application that manages this store from item listing through
check-in, point of sale, and post-convention reporting. The app will be reused every year.

The consignment store is the first and highest-priority module. Structure the codebase so
other convention modules can be added later, but build only the consignment store now.

**There is no public access.** Every page except login requires an authenticated user.
There is no self-registration. Accounts are created by administrators.

## Roles and permissions

### Administrator (board members)
- Create, edit, deactivate, and reset passwords for all user accounts (admins,
  volunteers, sellers)
- Bulk-import seller accounts from a CSV export of our Google Form application
  (sellers apply via Google Form outside this app; admins create their accounts from
  that data)
- Create events and configure per-event settings: event name/year, listing deadline,
  the store sale date (the store is open one day only), the three price windows, and the
  fee amounts (see Fees)
- View the financial breakdown (see Fees and seller payouts)
- Void a completed transaction, returning its items to `checked_in`
- Override checkout restrictions (outside price windows or off the sale date), with a
  required reason that is recorded
- Close the store/event, which finalizes all fees and payouts
- Export all reports to CSV
- Everything volunteers can do

### Volunteers
- **Friday night check-in:** Print a "master sheet" per seller listing their registered
  items (up to 20 items per sheet; see Fees). At check-in, mark each item as received
  (by scanning its info sheet or ticking it off), flag missing items, and handle items
  that arrive unlisted by adding them on the spot and printing their info sheets.
- **Point of sale:** Scan an item's code to add it to a cart, see the running total,
  remove items, and finalize the sale. The app does not process payments. The volunteer
  enters the total into Stripe separately, then records the Stripe transaction/receipt
  ID in the app to complete the sale.
- Prevent selling an item that is not checked in, already sold, or withdrawn, with a
  clear error message.
- View all listed games grouped by seller, with search/filter by title, seller, and status.
- **End-of-sale pickup:** Mark unsold items as returned to the seller.

### Sellers
- Log in and add, edit, and delete their games until the listing deadline (lock editing
  after that and at check-in)
- Item fields: title, optional edition/notes, condition (New / Like New / Good / Fair /
  Poor), optional BoardGameGeek link, and three sale prices, one per price window
  (see Pricing)
- Generate printable **item info sheets** to attach to each game (see Item info sheets)
- View their own sales report: what sold, in which window, at what price, commission and
  fees deducted, net owed, and what remains unsold. Before the event closes, show unsold
  fees as projected.
- View their reports from past events
- Optional: copy unsold items from a previous event into the current one so they don't
  have to re-enter games they're bringing back
- Sellers see only their own data, never anyone else's
- Payouts happen outside the app. The app only reports amounts owed.

## Multi-year use
- All items, check-ins, sales, fees, and reports belong to an **Event**. One event is
  marked active. Default views show the active event. Admins can switch to past events,
  which are read-only.
- Event settings (dates, windows, fee amounts) are stored per event so changing them in
  future years never alters past reports.
- User accounts persist across years. Returning sellers keep the same account and
  seller number unless an admin changes it.
- Item codes are unique across all events and include the event year, so an old info
  sheet can't be scanned into the wrong year's sales.

## Item lifecycle
`listed → checked_in → sold`, with side branches `missing`, `withdrawn`, and `returned`
(unsold and picked up). Record who changed the status and when for every transition,
especially sales and voids.

## Item info sheets
Sellers print these on plain letter paper, cut them out, and attach one to each game
before bringing it to the store. No label stock is used.
- **3 per page** by default, with a 2-per-page print option, and dashed cut lines
- Each info sheet shows:
  - A large scannable QR code and a barcode, plus the human-readable item code
  - Seller number and item number (e.g., "Seller 042 · Item 7 of 23")
  - Game title, edition/notes, and condition
  - The three prices in a clear, large-type table labeled with their time windows
  - The event name and year
- Allow printing all info sheets or selected ones
- Design for black-and-white home printers. Codes must stay scannable if printed
  slightly off-scale. Show sellers a note to print at 100% / "actual size."
- Item codes must be short enough for a volunteer to type when a scan fails

## Pricing
The store is open on a single day. Each item has three prices, one per window:
- Window 1: 9:00–9:55 am
- Window 2: 10:00–10:55 am
- Window 3: 11:00–11:55 am
(Window times are configurable per event; these are the defaults.)

- The three prices are independent. They may go down, stay the same, or go up from
  window to window. Do not enforce any ordering.
- **Minimum price is $1.00** for every window. All three prices are required.
- The POS determines the active window from the **server's clock** in the convention
  time zone (America/New_York), never from the device clock.
- The cart shows each item's current-window price and which window is active.
- The price charged is the one in effect **when the sale is finalized**. If the window
  changes while a cart is open, reprice the cart and warn the volunteer before
  finalizing so the total entered in Stripe is correct.
- Checkout is blocked outside the three windows (including the 5-minute gaps) and on
  any date other than the sale date, unless an admin overrides it.
- Record the window and price actually charged on each sale line, so reports never
  recompute history.

## Fees and seller payouts
Fee amounts are configurable per event. Current defaults:
- **Master sheet fee:** $2.00 per master sheet, full or partial. A sheet holds up to
  20 items. The fee is based on **items checked in**:
  fee = ceil(checked-in item count ÷ 20) × $2.00.
  Items that never arrive don't count; items added at check-in do.
- **Commission:** 10% of the actual sale price of each sold item.
- **Unsold item fee:** $0.25 per checked-in item not sold when the store closes.

Seller net = total sales − commission − master sheet fees − unsold item fees.

- Net can be negative (for example, a seller who sells nothing). Show this clearly as
  an amount the seller owes.
- Until an admin closes the event, unsold fees are projected. Closing finalizes them.
- Do all money math in integer cents, never floating point. Use one rounding rule for
  commission (round half up per item) and cover it with tests.
- The admin financial breakdown shows, per seller and in total: gross sales,
  commission, master sheet fees, unsold fees, and net payout. It also shows total
  convention revenue (all fees combined) and sales by checkout transaction with the
  Stripe reference for each.

## Key requirements
- **Scanning:** Must work with USB/Bluetooth barcode scanners acting as keyboard input
  (primary) and a phone/tablet camera (secondary). Manual code entry is always available.
- **Multiple checkout stations** run at once. An item must never be sold twice, so
  handle concurrency correctly at the database level.
- **Volunteer screens** must work well on tablets and laptops, with large touch targets
  and a fast, keyboard-friendly checkout flow.
- **Online only.** No offline mode is needed.
- **Security:** Hashed passwords, role-based authorization enforced on the server (not
  just hidden in the UI), and HTTPS in deployment.

## Tech and deployment
- Propose a stack. Favor something simple and cheap for a volunteer organization to
  host and maintain, where a future volunteer developer could pick up the code easily.
- Propose a hosting option with a rough yearly cost.
- Include seed data (an active event, a past event, several sellers, ~50 games) for
  testing and demos.
- Include a README covering local setup, deployment, creating the first admin account,
  starting a new year's event, and a short how-to for each role.
- Write automated tests for the critical paths: permissions and data isolation between
  sellers, the checkout flow (including double-sell prevention and window repricing),
  price-window boundaries, and all fee and payout calculations.

## How to proceed
1. Before writing code, ask me any clarifying questions.
2. Propose the stack, data model, and a phased build plan, and wait for my approval.
3. Build in phases, each one working end to end:
   accounts/auth and events → seller listings and info sheets → check-in and master
   sheets → POS → reports and event close.
![alt text](image.png)