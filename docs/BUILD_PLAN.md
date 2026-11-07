# Build Plan

Approved 2026-11-05. Requirements are in [SPEC.md](SPEC.md), records in [DATA_MODEL.md](DATA_MODEL.md) and undecided items in [OPEN_ITEMS.md](OPEN_ITEMS.md).

## Stack

| Part | Choice | Why |
| --- | --- | --- |
| Framework | Python 3.12, Django 5.2 LTS | Built-in sign-in, password hashing, permissions and migrations. Widely known. Security updates until 2028. |
| Database | PostgreSQL 16 | Row locks and partial unique indexes prevent double sales |
| Screens | Django templates, HTMX, one CSS file | No JavaScript build step |
| Live register alerts | HTMX polling every 2–3 seconds | Simple, and the server enforces blocks even if an alert is late |
| Sales slips | ReportLab PDFs, Code 128 barcodes | Precise layouts, and no system dependencies |
| Email | A low-cost email service | Set-password and reset links |
| Google Form sync | Google Forms API, on a schedule plus a "Sync now" button | Someone with access to the form must authorize it |
| Tooling | `uv`, `pytest-django`, `ruff` | |
| Local development | Docker Compose for Postgres only, app run with `uv run` | |

## Hosting

**Recommended: Render**, at about $160 a year for hosting plus about $15 for a domain, or roughly $175. Render handles the server, security patches and database backups, and includes HTTPS. Updates deploy from the code repository.

Email and the scheduled sync should add only a few dollars a year. All prices are approximate and from memory, so check them before signing up.

The alternative is a small virtual server at $60–80 a year plus the domain. It is cheaper, but a volunteer must maintain updates and backups. The app is configured through environment variables and isn't tied to either host.

## Code layout

```
bgf/                  Project settings and URLs
apps/core/            Shared layout, audit log, permissions, money helpers, badge-and-PIN step
apps/accounts/        Accounts, staff profiles, sign-in, seller linking
apps/events/          Events, statuses, price windows, fee settings, waivers, form sync
apps/consignment/     Listings, slips, check-in, checkout, pickup, payouts, reports
    services/         Business rules (fees, checkout, status changes), kept out of views
docs/                 These documents
```

A future convention module becomes a new `apps/<name>` that reuses accounts and events.

## Phases

Each phase ends with working screens the board can try, passing tests and a git commit.

### Phase 1: Accounts and events

- Project skeleton, base layout and easily changed branding
- Sign-in by Seller ID, Volunteer ID or email, with a choice when an email matches more than one account
- Set-password and reset emails
- Server-side role checks, and a secure command for the first admin
- Staff management, event access, and generating Volunteer IDs
- Admin badges and PINs, with lockout and reissue, and the shared override step
- Event setup, forward-only statuses, scheduled reminders, fee locks and waivers
- Seller accounts from the Google Form and CSV, with a review list
- Accepting the rules on first login, linking staff to seller accounts

**Tests:** sign-in required everywhere, role enforcement, seller isolation, login matching, import duplicates, fee locks, forward-only status, seller linking, PIN lockout.

### Phase 2: Listings and slips

- Add, edit and deactivate listings, and reactivate them with a "make changes?" step
- Slip versions with permanent history, generation rules and reprints
- PDF slips at 2 and 6 per page, on the provisional layout
- Test prints with the real scanners

**Tests:** seller isolation by direct URL, Presale locks, price validation, slip versioning and validity, immutable history, generation only from complete listings.

**Finishes with:** the treasurer's review of the slip layout and field limits.

### Phase 3: Check-in

- Seller selection and scan-to-accept with the comparison screen
- Rejection with a reason
- Outdated, deactivated and combined slip warnings
- Double-scan and duplicate-slip flows with admin overrides
- Block on checking in your own games
- Check-in reports on seller accounts, and item browse and search

**Tests:** accepted version retained, each exception flow, one check-in per listing, wrong-event codes, own-game block, status history.

### Phase 4: Point of sale

- Admin window control, schedule warnings, the open-cart list and the open-cart block
- Cart, reservations, Remove, Cancel Sale and scan errors
- Sale ID, locked payment step, restock list and both confirmation prompts
- Moving checkouts between registers
- Two-register conflicts with live alerts
- Sold item flags, admin release and sale overrides
- All admin cart modes

**Tests:** concurrent scans and payments against real Postgres, window change blocked by open carts, each admin cart mode by status, removed-item handling, conflict resolution, register transfers, permanent lock on completed sales, override rules.

### Phase 5: Pickup, payouts and closing

- Pickup scanning and every pickup error flow
- Donated and abandoned scanning
- Fee calculations for both listing fee options and all event settings
- Seller reports, the admin financial report and the other reports in SPEC section 12
- Payout recording and freezing, closing checks and the 30-day seller cutoff
- CSV exports, demo data, README and a how-to for each role

**Tests:** every fee path including waivers and negative balances, rounding, the money invariant in DATA_MODEL.md, payout preconditions, closing preconditions, frozen figures, past events unaffected by later settings, 30-day cutoff.

**Demo data:** a current event, a past closed event, several sellers and about 50 games, including sales.

**README covers:** local setup, deployment, creating the first admin, starting a new event, and a how-to for each role.

## Working agreements

- Commit to git at the end of each phase.
- No code until the phase has been started. Phase 1 was built 2026-11-07.
- When a decision changes, update `SPEC.md` so it stays the single source of truth.
