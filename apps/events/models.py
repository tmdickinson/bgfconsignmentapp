from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

DEFAULT_RULES = """\
Every game needs a listing and a printed sales slip before check-in. No games are added at check-in.
Print slips at 100% / actual size on plain paper, cut them out and attach one to each game.
Listings can only be changed during Presale.
Games can't be withdrawn after check-in.
Unsold games must be collected during Pickup. Games not collected are kept by the convention.
Payouts are made after the event, as described in the fees above."""


class Status(models.TextChoices):
    SETUP = "setup", "Setup"
    PRESALE = "presale", "Presale"
    CHECKIN = "checkin", "Check-in"
    SALE = "sale", "Sale"
    PICKUP = "pickup", "Pickup"
    POSTSALE = "postsale", "Post-sale"


STATUS_ORDER = [s.value for s in Status]


class ListingFeeType(models.TextChoices):
    SHEET = "sheet", "Master sheet (per sheet of checked-in items)"
    FLAT = "flat", "Flat fee per seller"


event_code_validator = RegexValidator(
    r"^[A-Z0-9]{4}$", "Use four letters or digits, such as F26A."
)


class Event(models.Model):
    code = models.CharField(max_length=4, unique=True, validators=[event_code_validator])
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SETUP)
    moved_to_postsale_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    form_url = models.URLField(blank=True)
    # App field name -> form question title or CSV column header.
    field_mapping = models.JSONField(default=dict, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    rules_text = models.TextField(default=DEFAULT_RULES)

    listing_fee_type = models.CharField(
        max_length=5, choices=ListingFeeType.choices, default=ListingFeeType.SHEET
    )
    sheet_fee_cents = models.PositiveIntegerField(default=200)
    items_per_sheet = models.PositiveIntegerField(default=20, validators=[MinValueValidator(1)])
    flat_fee_cents = models.PositiveIntegerField(default=0)
    commission_percent = models.PositiveSmallIntegerField(
        default=10, validators=[MaxValueValidator(100)]
    )
    unsold_fee_cents = models.PositiveIntegerField(default=25)
    charge_unsold_on_donated = models.BooleanField(default=False)
    charge_negative_balances = models.BooleanField(default=False)
    fees_locked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-start_date", "-id"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def status_index(self):
        return STATUS_ORDER.index(self.status)

    def status_at_least(self, status):
        return self.status_index >= STATUS_ORDER.index(status)

    @property
    def next_status(self):
        i = self.status_index
        return Status(STATUS_ORDER[i + 1]) if i + 1 < len(STATUS_ORDER) else None

    @property
    def is_closed(self):
        return self.status == Status.POSTSALE

    @property
    def fees_locked(self):
        return self.fees_locked_at is not None


class EventStatusSchedule(models.Model):
    """Optional reminder time for each status change. Never moves a status on its own."""

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="schedule")
    status = models.CharField(max_length=10, choices=Status.choices)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    moved_at = models.DateTimeField(null=True, blank=True)
    moved_by = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "status"], name="one_schedule_per_status")
        ]

    @property
    def status_label(self):
        return Status(self.status).label


class PriceWindow(models.Model):
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="windows")
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=60)
    scheduled_start = models.DateTimeField(null=True, blank=True)
    scheduled_end = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["event", "position"]
        constraints = [
            models.UniqueConstraint(fields=["event", "position"], name="window_position_per_event")
        ]

    def __str__(self):
        return self.name


class WaiverCategory(models.TextChoices):
    VOLUNTEER = "volunteer", "Volunteers"
    ADMIN = "admin", "Admins"
    SELLER = "seller", "Individual seller"


class FeeWaiver(models.Model):
    """Which fees are waived for a category or one seller. Replaced rows are kept."""

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="waivers")
    category = models.CharField(max_length=10, choices=WaiverCategory.choices)
    seller = models.ForeignKey(
        "accounts.Seller", null=True, blank=True, on_delete=models.PROTECT, related_name="waivers"
    )
    waive_commission = models.BooleanField(default=False)
    waive_listing = models.BooleanField(default=False)
    waive_unsold = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    set_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["category", "seller__number", "-set_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "category"],
                condition=models.Q(revoked_at__isnull=True, seller__isnull=True),
                name="one_current_category_waiver",
            ),
            models.UniqueConstraint(
                fields=["event", "seller"],
                condition=models.Q(revoked_at__isnull=True, seller__isnull=False),
                name="one_current_seller_waiver",
            ),
            models.CheckConstraint(
                condition=models.Q(category="seller", seller__isnull=False)
                | (~models.Q(category="seller") & models.Q(seller__isnull=True)),
                name="seller_waiver_has_seller",
            ),
        ]

    @property
    def waived_labels(self):
        return [label for label, on in (("Commission", self.waive_commission),
                                        ("Listing fee", self.waive_listing),
                                        ("Unsold fee", self.waive_unsold)) if on]


class ResponseSource(models.TextChoices):
    GOOGLE = "google", "Google Form"
    CSV = "csv", "CSV import"


class ResponseOutcome(models.TextChoices):
    CREATED = "created", "Account created"
    DUPLICATE = "duplicate", "Duplicate for this event"
    OUTSIDE_PRESALE = "outside_presale", "Submitted outside Presale"
    INCOMPLETE = "incomplete", "Missing name or a valid email"


class FormResponse(models.Model):
    """One form submission or CSV row, processed exactly once."""

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="form_responses")
    external_id = models.CharField(max_length=128)
    source = models.CharField(max_length=10, choices=ResponseSource.choices)
    payload = models.JSONField(default=dict)
    submitted_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    outcome = models.CharField(max_length=20, choices=ResponseOutcome.choices)
    needs_review = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        "accounts.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=200, blank=True)
    seller = models.OneToOneField(
        "accounts.Seller", null=True, blank=True, on_delete=models.PROTECT,
        related_name="source_response",
    )

    class Meta:
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "external_id"], name="response_once_per_event")
        ]


class GoogleConnection(models.Model):
    """The authorization to read one event's Google Form responses."""

    event = models.OneToOneField(Event, on_delete=models.PROTECT, related_name="google")
    refresh_token = models.TextField()
    authorized_by = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="+")
    authorized_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
