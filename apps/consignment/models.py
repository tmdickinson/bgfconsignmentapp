from django.db import models
from django.utils import timezone

# Provisional until the treasurer reviews the slip layout (OPEN_ITEMS.md).
NAME_MAX = 60
PUBLISHER_MAX = 40
DESCRIPTION_MAX = 300
CONDITION_NOTE_MAX = 100


class ItemStatus(models.TextChoices):
    LISTED = "listed", "Listed"
    DEACTIVATED = "deactivated", "Deactivated"
    AVAILABLE = "available", "Available"
    IN_CHECKOUT = "in_checkout", "In checkout"
    SOLD = "sold", "Sold"
    MISSING = "missing", "Missing"
    RETURNED = "returned", "Returned"
    DONATED = "donated", "Donated"
    ABANDONED = "abandoned", "Abandoned"
    ERROR = "error", "Error"


class Condition(models.TextChoices):
    NEW = "new", "New / Mint"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair / Incomplete"


class SlipSize(models.TextChoices):
    LARGE = "large", "Large (2 per page)"
    SMALL = "small", "Small (6 per page)"


class Listing(models.Model):
    """The permanent record of one game. Never deleted."""

    seller = models.ForeignKey("accounts.Seller", on_delete=models.PROTECT, related_name="listings")
    status = models.CharField(max_length=12, choices=ItemStatus.choices, default=ItemStatus.LISTED)
    name = models.CharField(max_length=NAME_MAX, blank=True)
    publisher = models.CharField(max_length=PUBLISHER_MAX, blank=True)
    description = models.TextField(max_length=DESCRIPTION_MAX, blank=True)
    condition = models.CharField(max_length=4, choices=Condition.choices, blank=True)
    condition_note = models.CharField(max_length=CONDITION_NOTE_MAX, blank=True)
    is_bundle = models.BooleanField(default=False)
    bundle_count = models.PositiveSmallIntegerField(null=True, blank=True)
    bgg_id = models.PositiveIntegerField(null=True, blank=True)
    slip_size = models.CharField(max_length=5, choices=SlipSize.choices, default=SlipSize.LARGE)
    donate_if_unsold = models.BooleanField(default=False)
    # The slip a volunteer can scan. Cleared when a change invalidates it.
    current_slip = models.OneToOneField(
        "SlipVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # The slip version accepted at check-in. Checkout uses it, never the seller's later edits.
    accepted_slip = models.OneToOneField(
        "SlipVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Set once and never cleared. Fees count on it.
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["seller", "created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(checked_in_at__isnull=True, accepted_slip__isnull=True)
                | models.Q(checked_in_at__isnull=False, accepted_slip__isnull=False),
                name="checked_in_listing_has_accepted_slip",
            ),
        ]

    def __str__(self):
        return self.name or f"Listing {self.pk}"

    @property
    def is_active(self):
        return self.status != ItemStatus.DEACTIVATED

    @property
    def has_valid_slip(self):
        return self.current_slip_id is not None

    @property
    def is_checked_in(self):
        return self.checked_in_at is not None

    @property
    def item_id(self):
        """The ID the game is known by: the accepted slip once checked in, else the current one."""
        slip = self.accepted_slip if self.accepted_slip_id else self.current_slip
        return slip.item_id if slip else ""


class ListingPrice(models.Model):
    """The seller's current price for one window, in whole dollars. Empty while a draft."""

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="prices")
    window = models.ForeignKey("events.PriceWindow", on_delete=models.PROTECT, related_name="+")
    dollars = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["window__position"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "window"], name="one_price_per_window"),
            models.CheckConstraint(condition=models.Q(dollars__isnull=True) | models.Q(dollars__gte=1),
                                   name="listing_price_at_least_one_dollar"),
        ]


class ImmutableError(Exception):
    pass


class SlipVersion(models.Model):
    """One printed slip and its Item ID. A frozen copy of what was printed; never changes.

    The only later write is `invalidated_at`, when a change to the listing makes it outdated.
    """

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="slips")
    item_id = models.CharField(max_length=16, unique=True)
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=NAME_MAX)
    publisher = models.CharField(max_length=PUBLISHER_MAX)
    description = models.TextField()
    condition = models.CharField(max_length=4, choices=Condition.choices)
    condition_note = models.CharField(max_length=CONDITION_NOTE_MAX, blank=True)
    bundle_count = models.PositiveSmallIntegerField(null=True, blank=True)
    slip_size = models.CharField(max_length=5, choices=SlipSize.choices)
    donate_if_unsold = models.BooleanField()
    generated_at = models.DateTimeField(default=timezone.now)
    generated_by = models.ForeignKey(
        "accounts.Account", null=True, on_delete=models.PROTECT, related_name="+"
    )
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["listing", "sequence"]

    def __str__(self):
        return self.item_id

    def save(self, *args, **kwargs):
        if self.pk and kwargs.get("update_fields") != ["invalidated_at"]:
            raise ImmutableError("Slip versions can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Slip versions can't be deleted.")

    @property
    def is_valid(self):
        return self.invalidated_at is None and self.listing.current_slip_id == self.pk


class SlipPrice(models.Model):
    slip = models.ForeignKey(SlipVersion, on_delete=models.PROTECT, related_name="prices")
    window = models.ForeignKey("events.PriceWindow", on_delete=models.PROTECT, related_name="+")
    price_cents = models.PositiveIntegerField()

    class Meta:
        ordering = ["window__position"]
        constraints = [
            models.UniqueConstraint(fields=["slip", "window"], name="one_slip_price_per_window"),
            models.CheckConstraint(
                condition=models.Q(
                    models.lookups.Exact(models.functions.Mod("price_cents", 100), 0),
                    price_cents__gte=100,
                ),
                name="slip_price_whole_dollars_at_least_one",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableError("Slip prices can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Slip prices can't be deleted.")


class ItemStatusChange(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="status_changes")
    from_status = models.CharField(max_length=12, choices=ItemStatus.choices, blank=True)
    to_status = models.CharField(max_length=12, choices=ItemStatus.choices)
    changed_by = models.ForeignKey(
        "accounts.Account", null=True, on_delete=models.PROTECT, related_name="+"
    )
    changed_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=500, blank=True)
    approved_by_admin = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["changed_at", "id"]


class CheckinReport(models.Model):
    """A seller's check-in record. Resumed if the seller comes back with more games."""

    seller = models.OneToOneField("accounts.Seller", on_delete=models.PROTECT,
                                  related_name="checkin_report")
    started_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    started_at = models.DateTimeField(default=timezone.now)
    completed_by = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)


class CheckinOutcome(models.TextChoices):
    ACCEPTED = "accepted", "Checked in"
    REJECTED = "rejected", "Rejected"
    NOT_FOUND = "not_found", "Barcode not found"
    WRONG_EVENT = "wrong_event", "Belongs to another event"
    OTHER_SELLER = "other_seller", "Belongs to another seller"
    OUTDATED_ACCEPTED = "outdated_accepted", "Outdated slip accepted"
    REACTIVATED = "reactivated", "Deactivated game reactivated"
    DOUBLE_SCAN = "double_scan", "Double scanned"
    DUPLICATE_SLIP = "duplicate_slip", "Two games with one slip"
    OVERRIDE = "override", "Admin override"


class CheckinEntry(models.Model):
    """One scan or decision during check-in. Errors and overrides appear on the report."""

    report = models.ForeignKey(CheckinReport, on_delete=models.PROTECT, related_name="entries")
    scanned_code = models.CharField(max_length=40)
    listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="checkin_entries")
    slip = models.ForeignKey(SlipVersion, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    outcome = models.CharField(max_length=20, choices=CheckinOutcome.choices)
    reason = models.CharField(max_length=500, blank=True)
    approved_by_admin = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def is_error(self):
        return self.outcome != CheckinOutcome.ACCEPTED


class Register(models.Model):
    """A laptop at the sales tables. Each laptop remembers its register in a cookie."""

    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="registers")
    name = models.CharField(max_length=40)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["event", "name"]
        constraints = [
            models.UniqueConstraint(fields=["event", "name"], name="register_name_per_event")
        ]

    def __str__(self):
        return self.name


class CheckoutKind(models.TextChoices):
    REGULAR = "regular", "Regular"
    ADMIN = "admin", "Admin cart"


class PricingMode(models.TextChoices):
    ACTIVE = "active", "Active window"
    CHOSEN = "chosen", "One chosen window"
    PER_ITEM = "per_item", "Chosen per item"


class CheckoutState(models.TextChoices):
    OPEN = "open", "Open"
    AWAITING_PAYMENT = "awaiting", "Awaiting payment"
    COMPLETE = "complete", "Complete"
    CANCELED = "canceled", "Canceled"


ACTIVE_STATES = [CheckoutState.OPEN, CheckoutState.AWAITING_PAYMENT]


class Checkout(models.Model):
    """Every cart, kept permanently with its removed games, Sale ID and cancellation."""

    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="checkouts")
    owner = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="checkouts")
    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name="checkouts")
    register_changed_at = models.DateTimeField(null=True, blank=True)
    kind = models.CharField(max_length=8, choices=CheckoutKind.choices, default=CheckoutKind.REGULAR)
    opened_by_admin = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    pricing_mode = models.CharField(max_length=8, choices=PricingMode.choices,
                                    default=PricingMode.ACTIVE)
    window = models.ForeignKey("events.PriceWindow", null=True, blank=True,
                               on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=8, choices=CheckoutState.choices,
                             default=CheckoutState.OPEN)
    # Set when an admin override returns a completed sale to the payment step. Such a
    # sale is settled from the admin's sale page, away from any register.
    reopened_by_override = models.BooleanField(default=False)
    sale_id = models.CharField(max_length=9, null=True, blank=True)  # noqa: DJ001  (unique when set)
    total_cents = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    proceeded_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey("accounts.Account", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="+")
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey("accounts.Account", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner"], name="one_active_checkout_per_owner",
                condition=models.Q(state__in=ACTIVE_STATES, reopened_by_override=False),
            ),
            models.UniqueConstraint(
                fields=["register"], name="one_active_sale_per_register",
                condition=models.Q(state__in=ACTIVE_STATES, reopened_by_override=False),
            ),
            models.UniqueConstraint(
                fields=["event", "sale_id"], name="sale_id_unique_per_event",
                condition=models.Q(sale_id__isnull=False),
            ),
        ]

    def __str__(self):
        return self.sale_id or f"Cart {self.pk}"

    @property
    def is_active(self):
        return self.state in ACTIVE_STATES

    @property
    def is_admin_cart(self):
        return self.kind == CheckoutKind.ADMIN


class CheckoutLine(models.Model):
    """One game scanned into a cart.

    `live` is true while the line is buying the game: not removed, not released and
    in a cart that isn't canceled. The database allows one live line per game,
    which is what makes a double sale impossible.
    """

    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="lines")
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="checkout_lines")
    slip = models.ForeignKey(SlipVersion, on_delete=models.PROTECT, related_name="+")
    window = models.ForeignKey("events.PriceWindow", on_delete=models.PROTECT, related_name="+")
    price_cents = models.PositiveIntegerField()
    commission_cents = models.PositiveIntegerField()
    live = models.BooleanField(default=True)
    added_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey("accounts.Account", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    released_by_admin = models.ForeignKey("accounts.Account", null=True, blank=True,
                                          on_delete=models.PROTECT, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["added_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["listing"], condition=models.Q(live=True),
                                    name="one_live_line_per_listing"),
            models.UniqueConstraint(fields=["checkout", "listing"], name="one_line_per_listing_per_cart"),
        ]

    @property
    def is_removed(self):
        return self.removed_at is not None


class RegisterConflict(models.Model):
    """A register scanned a game on another cart's live line. Blocks payment at both."""

    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="+")
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="conflicts")
    holding_checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="+")
    scanning_checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="+")
    raised_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    raised_at = models.DateTimeField(default=timezone.now)
    resolved_by = models.ForeignKey("accounts.Account", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    kept_by_checkout = models.ForeignKey(Checkout, null=True, blank=True,
                                         on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-raised_at"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "scanning_checkout"],
                                    condition=models.Q(resolved_at__isnull=True),
                                    name="one_open_conflict_per_scan"),
        ]


class FlagContext(models.TextChoices):
    CHECKOUT = "checkout", "Checkout"
    PICKUP = "pickup", "Pickup"


class SoldItemFlag(models.Model):
    """A Sold game was scanned again. Only an admin can close it, by taking the game."""

    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="+")
    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="sold_flags")
    context = models.CharField(max_length=8, choices=FlagContext.choices)
    raised_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    raised_at = models.DateTimeField(default=timezone.now)
    resolved_by = models.ForeignKey("accounts.Account", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-raised_at"]
        constraints = [
            models.UniqueConstraint(fields=["listing"], condition=models.Q(resolved_at__isnull=True),
                                    name="one_open_sold_flag_per_listing"),
        ]


class SaleOverride(models.Model):
    """An admin returned a completed sale to the payment step."""

    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="overrides")
    admin = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    requested_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)


class PickupOutcome(models.TextChoices):
    RETURNED = "returned", "Returned to the seller"
    KEPT_DONATED = "kept_donated", "Seller confirmed the donation"
    LEFT_ABANDONED = "left_abandoned", "Left abandoned"
    REFUSED = "refused", "Not returned"


class PickupEntry(models.Model):
    """A game scanned at pickup, including scans that change nothing, for its history."""

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="pickup_entries")
    outcome = models.CharField(max_length=16, choices=PickupOutcome.choices)
    note = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class Settlement(models.Model):
    """A seller's figures, frozen when their payout is recorded. Never changed after."""

    seller = models.OneToOneField("accounts.Seller", on_delete=models.PROTECT,
                                  related_name="settlement")
    checked_in_count = models.PositiveIntegerField()
    sold_count = models.PositiveIntegerField()
    unsold_fee_count = models.PositiveIntegerField()
    sheets = models.PositiveIntegerField()
    gross_cents = models.PositiveIntegerField()
    commission_cents = models.PositiveIntegerField()
    listing_fee_cents = models.PositiveIntegerField()
    unsold_fee_cents = models.PositiveIntegerField()
    waived_commission_cents = models.PositiveIntegerField()
    waived_listing_cents = models.PositiveIntegerField()
    waived_unsold_cents = models.PositiveIntegerField()
    # Negative only when negative balances are charged: the seller owes that amount.
    net_cents = models.IntegerField()
    forgiven_cents = models.PositiveIntegerField()
    frozen_at = models.DateTimeField(default=timezone.now)
    frozen_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableError("Settlements can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Settlements can't be deleted.")


class PayoutOutcome(models.TextChoices):
    PAID = "paid", "Paid"
    NOTHING_DUE = "nothing_due", "Nothing due"
    FORGIVEN = "forgiven", "Balance forgiven"
    COLLECTED = "collected", "Balance collected"
    NOT_COLLECTED = "not_collected", "Balance not collected"


class Payout(models.Model):
    """What happened with a seller's money. The app reports it; payment happens outside."""

    settlement = models.OneToOneField(Settlement, on_delete=models.PROTECT, related_name="payout")
    outcome = models.CharField(max_length=14, choices=PayoutOutcome.choices)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=500, blank=True)
    recorded_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    recorded_at = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ImmutableError("Payouts can't be changed.")
        super().save(*args, **kwargs)
