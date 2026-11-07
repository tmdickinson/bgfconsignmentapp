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
