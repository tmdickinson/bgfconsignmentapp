import secrets
from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class AccountManager(BaseUserManager):
    def create_account(self, email, password=None, **extra):
        account = self.model(email=self.normalize_email(email), **extra)
        if password:
            account.set_password(password)
        else:
            # The person sets their own through an emailed link.
            account.set_unusable_password()
        account.save()
        return account


def new_username():
    return "acct-" + secrets.token_hex(8)


class Account(AbstractBaseUser):
    """Anyone who can sign in.

    A staff account has a StaffProfile. A seller account has exactly one Seller,
    for one event. The same person can have both, as separate accounts.
    Email isn't unique, so sign-in looks people up by Seller ID, Volunteer ID or
    email instead of by username.
    """

    # Internal and never shown. Django needs a unique field to identify accounts.
    username = models.CharField(max_length=40, unique=True, default=new_username)
    email = models.EmailField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"

    objects = AccountManager()

    class Meta:
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return self.display_name

    @property
    def staff(self):
        return getattr(self, "staff_profile", None)

    @property
    def seller(self):
        return getattr(self, "seller_record", None)

    @property
    def is_admin(self):
        return bool(self.staff and self.staff.is_admin)

    @property
    def is_volunteer(self):
        return bool(self.staff and self.staff.is_volunteer)

    @property
    def is_staff_member(self):
        return bool(self.staff and (self.staff.is_admin or self.staff.is_volunteer))

    @property
    def display_name(self):
        if self.staff:
            return self.staff.name
        if self.seller:
            return self.seller.name
        return self.email

    @property
    def login_label(self):
        """How the account is described when an email matches more than one."""
        if self.staff:
            roles = " and ".join(r for r, on in (("Admin", self.staff.is_admin),
                                                  ("Volunteer", self.staff.is_volunteer)) if on)
            ident = self.staff.volunteer_id or self.email
            return f"Staff ({roles or 'no role'}): {self.staff.name}, {ident}"
        if self.seller:
            return f"Seller {self.seller.seller_id} for {self.seller.event.name}"
        return self.email


PIN_LOCK_AFTER = 5


class StaffProfile(models.Model):
    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="staff_profile")
    name = models.CharField(max_length=120)
    is_admin = models.BooleanField(default=False)
    is_volunteer = models.BooleanField(default=False)
    volunteer_id = models.CharField(max_length=8, unique=True, null=True, blank=True)
    admin_id = models.CharField(max_length=10, unique=True, null=True, blank=True)
    pin_hash = models.CharField(max_length=128, blank=True)
    failed_pin_count = models.PositiveSmallIntegerField(default=0)
    badge_locked_at = models.DateTimeField(null=True, blank=True)
    badge_issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def staff_id(self):
        """The ID used in Sale IDs: the Volunteer ID, or the Admin ID for admins without one."""
        return self.volunteer_id or self.admin_id or ""

    def set_pin(self, pin):
        self.pin_hash = make_password(pin)

    def check_pin(self, pin):
        return bool(self.pin_hash) and check_password(pin, self.pin_hash)

    @property
    def badge_locked(self):
        return self.badge_locked_at is not None


class EventAccess(models.Model):
    """A volunteer's access to one event. Revoked rows are kept as history."""

    staff = models.ForeignKey(StaffProfile, on_delete=models.PROTECT, related_name="event_access")
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="staff_access")
    granted_by = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+")
    granted_at = models.DateTimeField(default=timezone.now)
    revoked_by = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "event"], condition=models.Q(revoked_at__isnull=True),
                name="one_active_access_per_event",
            )
        ]


class LoginFailure(models.Model):
    """Failed sign-in attempts, used to slow down guessing."""

    identifier = models.CharField(max_length=254)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["identifier", "created_at"]),
                   models.Index(fields=["ip_address", "created_at"])]


SELLER_LOGIN_DAYS_AFTER_POSTSALE = 30


class Seller(models.Model):
    """A seller for one event. Returning sellers get a new, unconnected record each event."""

    account = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="seller_record")
    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="sellers")
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=120)
    email = models.EmailField()
    payment_method = models.CharField(max_length=200, blank=True)
    rules_accepted_at = models.DateTimeField(null=True, blank=True)
    linked_staff = models.ForeignKey(
        StaffProfile, null=True, blank=True, on_delete=models.PROTECT, related_name="seller_links"
    )
    linked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["event", "number"]
        constraints = [
            models.UniqueConstraint(fields=["event", "number"], name="seller_number_per_event"),
            models.UniqueConstraint(
                models.functions.Lower("email"), "event", name="seller_email_per_event"
            ),
            models.UniqueConstraint(
                fields=["event", "linked_staff"], condition=models.Q(linked_staff__isnull=False),
                name="one_linked_seller_per_staff_per_event",
            ),
        ]

    def __str__(self):
        return f"{self.seller_id} {self.name}"

    @property
    def seller_id(self):
        # Built from the event code rather than stored, because the code can still
        # change during Setup.
        return f"{self.event.code}-{self.number:03d}"

    @property
    def logins_end_at(self):
        if self.event.moved_to_postsale_at is None:
            return None
        return self.event.moved_to_postsale_at + timedelta(
            days=SELLER_LOGIN_DAYS_AFTER_POSTSALE
        )

    @property
    def login_expired(self):
        end = self.logins_end_at
        return end is not None and timezone.now() >= end
