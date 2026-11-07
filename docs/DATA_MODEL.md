# Data Model

Approved structure for the app described in [SPEC.md](SPEC.md). Exact field names may change during the build, but the rules in "Constraints" must hold.

General rules:

- Every table has an internal primary key, separate from any displayed ID.
- Money is stored as integer cents.
- Records are never physically deleted.
- Sellers are isolated by tagging records with their event, not by creating a table per event. This keeps seller data just as separate, with no links between years, while keeping reports and upgrades simple.

## Accounts

**Account** (the Django user)

- `email` (not unique on its own), password hash, `is_active`, `last_login`
- Sign-in looks up every active account matching the Seller ID, Volunteer ID or email, then checks the password. If more than one account matches, the user chooses. Seller accounts past their 30-day limit are excluded.

**StaffProfile**

- `account`, `name`, `is_admin`, `is_volunteer`
- `volunteer_id` (unique, nullable), `admin_id` (unique, nullable)
- `pin_hash`, `failed_pin_count`, `badge_locked_at`, `badge_issued_at`

**EventAccess**

- `staff`, `event`, `granted_by`, `granted_at`

**Seller**

- `account`, `event`, `number`, `seller_id`, `name`, `email`
- `payment_method`
- `rules_accepted_at`
- `linked_staff` (nullable), `linked_at`
- `source_response` (nullable)

## Events

**Event**

- `event_code`, `name`, `dates`, `status`, `moved_to_postsale_at`, `closed_by`
- `form_url`, form connection details, `field_mapping`
- `listing_fee_type` (master sheet or flat), `sheet_fee_cents`, `items_per_sheet`, `flat_fee_cents`
- `commission_percent` (whole number), `unsold_fee_cents`
- `charge_unsold_on_donated`, `charge_negative_balances`
- `fees_locked_at`
- `active_window` (nullable), `last_window_closed_at`

**EventStatusSchedule**

- `event`, `status`, `scheduled_at`, `moved_at`, `moved_by`

**PriceWindow**

- `event`, `position`, `name`, `scheduled_start`, `scheduled_end`, `opened_at`, `closed_at`

**FeeWaiver**

- `event`, `category` (volunteer, admin or individual seller), `seller` (nullable)
- `waive_commission`, `waive_listing`, `waive_unsold`
- `set_by`, `set_at`, `revoked_at`

**FormResponse**

- `event`, `external_id` (or a hash for CSV rows), `source` (Google or CSV), `payload`
- `outcome` (created, duplicate or outside Presale), `needs_review`, `reviewed_by`, `seller` (nullable)

## Listings and inventory

**Listing** (the permanent record of one game)

- `seller`, `status` (see SPEC section 7)
- Current details: `name`, `publisher`, `description`, `condition`, `condition_note`, `is_bundle`, `bundle_count`, `bgg_id`, `slip_size`, `donate_if_unsold`
- `current_slip` (nullable), `accepted_slip` (nullable), `checked_in_at`, `checked_in_by`
- `checked_in_at` is never cleared once set. Fees count on it.

**ListingPrice**

- `listing`, `window`, `dollars` (nullable while a draft)

**SlipVersion** (one per Item ID, immutable)

- `listing`, `item_id`, `sequence`
- A frozen copy of everything printed: name, publisher, description, condition, bundle count, slip size, donate flag
- `generated_at`, `invalidated_at`

**SlipPrice**

- `slip`, `window`, `price_cents`

**ItemStatusChange**

- `listing`, `from_status`, `to_status`, `changed_by`, `changed_at`, `note`
- `checkout` (nullable), `approved_by_admin` (nullable)

**SoldItemFlag**

- `listing`, `raised_by`, `raised_at`, `context` (checkout or pickup)
- `resolved_by`, `resolved_at`, `note`

## Check-in

**CheckinReport**

- `seller`, `volunteer`, `started_at`, `completed_at`, summary counts

**CheckinEntry**

- `report`, `scanned_code`, `listing` (nullable), `slip` (nullable)
- `outcome` (accepted, rejected, not found, wrong event, outdated accepted, reactivated, double scan, duplicate slip, override)
- `reason`, `note`, `approved_by_admin` (nullable), `created_at`

## Checkout

**Register**

- `event`, `name`

**Checkout** (created for every cart)

- `event`, `owner` (staff), `register`, `register_changed_at`
- `kind` (regular or admin), `opened_by_admin`
- `pricing_mode` (active window, chosen window, or per item), `window` (nullable)
- `state` (open, awaiting payment, complete or canceled)
- `sale_id` (nullable), `total_cents`, `proceeded_at`, `paid_at`, `canceled_at`

**CheckoutLine**

- `checkout`, `listing`, `slip` (the accepted version), `window`, `price_cents`, `commission_cents`
- `added_at`, `removed_at`, `removed_by`, `released_by_admin` (nullable)

**RegisterConflict**

- `listing`, `holding_checkout`, `scanning_checkout`, `raised_at`
- `resolved_by`, `resolved_at`, `kept_by_checkout`, `note`

**SaleOverride**

- `checkout`, `admin`, `note`, `created_at`

## Money

**Settlement** (one per seller who delivered items, frozen when the payout is recorded)

- `seller`, counts of checked-in, sold, and unsold-fee items, `sheets`
- `gross_cents`, `commission_cents`, `listing_fee_cents`, `unsold_fee_cents`
- `waived_commission_cents`, `waived_listing_cents`, `waived_unsold_cents`
- `net_cents`, `forgiven_cents`, `frozen_at`

**Payout**

- `settlement`, `outcome` (paid, nothing due, forgiven, collected or not collected)
- `reference`, `note`, `recorded_by`, `recorded_at`

Revenue and report figures come from sale lines until a settlement is frozen, and from settlements afterward.

## History

**AuditLog**

- `actor`, `action`, `event` (nullable), target type and ID, `details`, `created_at`

## Constraints

These must be enforced in the database where possible.

| Rule | How |
| --- | --- |
| An item can't be held by two checkouts | Partial unique index on `CheckoutLine.listing` where the line belongs to an open, awaiting-payment or complete checkout and hasn't been removed or released |
| Scans and status changes are safe at multiple registers | `SELECT ... FOR UPDATE` on the listing within a transaction |
| One active checkout per staff member | Partial unique index on `Checkout.owner` for regular carts in open or awaiting-payment states |
| Sale IDs are unique within an event | Unique index on event and `sale_id` |
| Item IDs are unique | Unique index on `item_id`, which includes the event |
| Seller numbers and emails are unique within an event | Unique indexes on event plus each |
| Event codes are unique | Unique index |
| Prices meet the minimum | Check constraint, `price_cents >= 100` and a whole dollar amount |
| Each form response is processed once | Unique index on event and `external_id` |
| Slip versions never change | No update path in code, checked by tests |
| Status moves forward only | Enforced in one service function, checked by tests |

## Fee formulas

```
checked_in  = listings with checked_in_at set
sheets      = ceil(checked_in / items_per_sheet)
listing_fee = sheets × sheet_fee_cents         (master sheet option)
            = flat_fee_cents                   (flat option)
commission  = sum over sold lines of (price_cents × commission_percent + 50) // 100
unsold      = checked-in items that are Returned or Abandoned,
              plus Donated if charge_unsold_on_donated
net         = gross − commission − listing_fee − unsold × unsold_fee_cents
```

A seller with `checked_in = 0` pays no fees. Waived fees count as zero toward `net`, and their amounts are stored separately. If `net` is negative and negative balances aren't charged, the recorded net is 0 and the shortfall is stored as `forgiven_cents`.

**Money invariant, for an event once all outcomes are recorded:**

```
total sales + balances collected = payouts + fees kept
```
