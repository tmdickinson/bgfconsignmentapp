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
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["seller", "created_at", "id"]

    def __str__(self):
        return self.name or f"Listing {self.pk}"

    @property
    def is_active(self):
        return self.status != ItemStatus.DEACTIVATED

    @property
    def has_valid_slip(self):
        return self.current_slip_id is not None


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

    class Meta:
        ordering = ["changed_at", "id"]
