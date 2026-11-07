"""Account rules: staff, badges, sellers, sign-in lookup and seller linking."""

import re
import secrets
import unicodedata

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.audit import audit
from apps.core.ids import DIGITS, normalize_code, random_chars

from .models import Account, EventAccess, Seller, StaffProfile


class AccountError(Exception):
    pass


# ---------- Identifiers ----------

VOLUNTEER_ID_RE = re.compile(r"^VOL-[A-Z]{2}\d{2}$")
SELLER_ID_RE = re.compile(r"^([A-Z0-9]{4})-(\d{1,4})$")


def initials(name):
    """First and last initials, as plain capital letters."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    words = [w for w in re.split(r"[^A-Z]+", plain) if w]
    if not words:
        return "XX"
    return words[0][0] + words[-1][0]


def generate_volunteer_id(name):
    """VOL- plus initials and two random digits. A different pair is drawn if taken."""
    prefix = "VOL-" + initials(name)
    taken = set(
        StaffProfile.objects.filter(volunteer_id__startswith=prefix).values_list(
            "volunteer_id", flat=True
        )
    )
    free = [prefix + a + b for a in DIGITS for b in DIGITS if prefix + a + b not in taken]
    if not free:
        raise AccountError(f"Every Volunteer ID starting {prefix} is in use.")
    return secrets.choice(free)


def generate_admin_id():
    while True:
        admin_id = "ADM-" + random_chars(6)
        if not StaffProfile.objects.filter(admin_id=admin_id).exists():
            return admin_id


def parse_seller_id(text):
    """Return (event_code, number) for a Seller ID such as F26A-042, or None."""
    match = SELLER_ID_RE.match(normalize_code(text))
    if not match:
        return None
    return match.group(1), int(match.group(2))


# ---------- Emails ----------


def set_password_url(account):
    uid = urlsafe_base64_encode(force_bytes(account.pk))
    token = default_token_generator.make_token(account)
    path = reverse("accounts:set_password", kwargs={"uidb64": uid, "token": token})
    return settings.SITE_URL + path


def send_set_password_email(account, *, new):
    """Email a link to set the password: a welcome for new accounts, else a reset."""
    context = {"account": account, "url": set_password_url(account), "new": new,
               "brand": settings.BRAND}
    subject = (f"Your {settings.BRAND['convention']} consignment account" if new
               else f"Reset your {settings.BRAND['convention']} consignment password")
    body = render_to_string("accounts/email/set_password.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [account.email])


def send_reset_emails(accounts):
    """One email per address, listing a link for each of its accounts."""
    by_email = {}
    for account in accounts:
        by_email.setdefault(account.email.lower(), []).append(account)
    for group in by_email.values():
        links = [(a.login_label, set_password_url(a)) for a in group]
        body = render_to_string("accounts/email/reset.txt",
                                {"links": links, "brand": settings.BRAND})
        send_mail(f"Reset your {settings.BRAND['convention']} consignment password", body,
                  settings.DEFAULT_FROM_EMAIL, [group[0].email])


# ---------- Staff ----------


def _apply_roles(profile, is_admin, is_volunteer):
    """Volunteer IDs are permanent once issued. Admins get a badge when first made admin."""
    profile.is_admin = is_admin
    profile.is_volunteer = is_volunteer
    if is_volunteer and not profile.volunteer_id:
        profile.volunteer_id = generate_volunteer_id(profile.name)
    if is_admin and not profile.admin_id:
        profile.admin_id = generate_admin_id()
        profile.badge_issued_at = timezone.now()


def create_staff(actor, *, name, email, is_admin, is_volunteer, password=None, send_email=True):
    with transaction.atomic():
        account = Account.objects.create_account(email, password)
        profile = StaffProfile(account=account, name=name.strip())
        _apply_roles(profile, is_admin, is_volunteer)
        profile.save()
        audit(actor, "staff.created", target=profile, name=profile.name, email=account.email,
              is_admin=is_admin, is_volunteer=is_volunteer)
    if send_email:
        transaction.on_commit(lambda: send_set_password_email(account, new=True))
    return profile


def update_staff(actor, profile, *, name, email, is_admin, is_volunteer):
    if profile.account_id == actor.pk and profile.is_admin and not is_admin:
        raise AccountError("You can't remove your own admin role.")
    with transaction.atomic():
        before = {"name": profile.name, "email": profile.account.email,
                  "is_admin": profile.is_admin, "is_volunteer": profile.is_volunteer}
        profile.name = name.strip()
        _apply_roles(profile, is_admin, is_volunteer)
        profile.save()
        profile.account.email = Account.objects.normalize_email(email)
        profile.account.save(update_fields=["email"])
        after = {"name": profile.name, "email": profile.account.email,
                 "is_admin": profile.is_admin, "is_volunteer": profile.is_volunteer}
        changes = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
        if changes:
            audit(actor, "staff.updated", target=profile, changes=changes)
    return profile


def set_account_active(actor, account, active):
    if account.pk == actor.pk and not active:
        raise AccountError("You can't deactivate your own account.")
    if account.is_active == active:
        return
    account.is_active = active
    account.save(update_fields=["is_active"])
    audit(actor, "account.activated" if active else "account.deactivated", target=account)


def set_event_access(actor, profile, events):
    """Grant access to `events` and revoke it from any others, keeping history."""
    wanted = {e.pk for e in events}
    with transaction.atomic():
        current = {a.event_id: a for a in profile.event_access.filter(revoked_at__isnull=True)}
        for event_pk, access in current.items():
            if event_pk not in wanted:
                access.revoked_at = timezone.now()
                access.revoked_by = actor
                access.save(update_fields=["revoked_at", "revoked_by"])
                audit(actor, "access.revoked", event=access.event, target=profile)
        for event in events:
            if event.pk not in current:
                EventAccess.objects.create(staff=profile, event=event, granted_by=actor)
                audit(actor, "access.granted", event=event, target=profile)


# ---------- Badges and PINs ----------


def set_pin(account, pin):
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise AccountError("The PIN must be exactly 4 digits.")
    profile = account.staff
    profile.set_pin(pin)
    profile.failed_pin_count = 0
    profile.save(update_fields=["pin_hash", "failed_pin_count"])
    audit(account, "badge.pin_set", target=profile)


def reissue_badge(actor, profile):
    """Replace a lost badge. The old Admin ID stops working and the badge unlocks."""
    old = profile.admin_id
    profile.admin_id = generate_admin_id()
    profile.badge_issued_at = timezone.now()
    profile.badge_locked_at = None
    profile.failed_pin_count = 0
    profile.save()
    audit(actor, "badge.reissued", target=profile, old_admin_id=old, new_admin_id=profile.admin_id)


def unlock_badge(actor, profile):
    profile.badge_locked_at = None
    profile.failed_pin_count = 0
    profile.save(update_fields=["badge_locked_at", "failed_pin_count"])
    audit(actor, "badge.unlocked", target=profile)


# ---------- Sellers ----------


class DuplicateSeller(AccountError):
    pass


def create_seller(event, *, name, email, payment_method="", actor=None, send_email=True):
    """Create a seller account with the next number in the event, and lock the fees."""
    from apps.events.models import Event

    email = Account.objects.normalize_email(email.strip())
    with transaction.atomic():
        # Locking the event row makes seller numbers sequential without gaps from races.
        event = Event.objects.select_for_update().get(pk=event.pk)
        if event.is_closed:
            raise AccountError("The event is closed.")
        if Seller.objects.filter(event=event, email__iexact=email).exists():
            raise DuplicateSeller(f"{email} is already a seller for this event.")
        number = (Seller.objects.filter(event=event).aggregate(n=Max("number"))["n"] or 0) + 1
        account = Account.objects.create_account(email)
        try:
            with transaction.atomic():
                seller = Seller.objects.create(account=account, event=event, number=number,
                                               name=name.strip(), email=email,
                                               payment_method=payment_method.strip())
        except IntegrityError as e:
            raise DuplicateSeller(f"{email} is already a seller for this event.") from e
        if not event.fees_locked_at:
            event.fees_locked_at = timezone.now()
            event.save(update_fields=["fees_locked_at"])
            audit(actor, "event.fees_locked", event=event, target=event)
        audit(actor, "seller.created", event=event, target=seller, seller_id=seller.seller_id,
              email=email)
    if send_email:
        transaction.on_commit(lambda: send_set_password_email(account, new=True))
    return seller


def update_seller(actor, seller, *, name, email, payment_method):
    email = Account.objects.normalize_email(email.strip())
    if (Seller.objects.filter(event=seller.event, email__iexact=email)
            .exclude(pk=seller.pk).exists()):
        raise DuplicateSeller(f"{email} is already a seller for this event.")
    before = {"name": seller.name, "email": seller.email, "payment_method": seller.payment_method}
    with transaction.atomic():
        seller.name, seller.email, seller.payment_method = name.strip(), email, payment_method
        seller.save(update_fields=["name", "email", "payment_method"])
        seller.account.email = email
        seller.account.save(update_fields=["email"])
        after = {"name": seller.name, "email": seller.email,
                 "payment_method": seller.payment_method}
        changes = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
        if changes:
            audit(actor, "seller.updated", event=seller.event, target=seller, changes=changes)


def accept_rules(seller, actor):
    if seller.rules_accepted_at:
        return
    seller.rules_accepted_at = timezone.now()
    seller.save(update_fields=["rules_accepted_at"])
    audit(actor, "seller.rules_accepted", event=seller.event, target=seller)


class LinkError(AccountError):
    pass


def link_seller(account, seller_id_text, password):
    """Link a staff account to its seller account for an active event.

    A correct Seller ID and seller password is enough, with no admin approval.
    """
    profile = account.staff
    parsed = parse_seller_id(seller_id_text)
    seller = None
    if parsed:
        seller = (Seller.objects.select_related("event", "account")
                  .filter(event__code=parsed[0], number=parsed[1]).first())
    if (seller is None or not seller.account.is_active
            or not seller.account.check_password(password or "")):
        raise LinkError("That Seller ID and password don't match.")
    if seller.event.is_closed:
        raise LinkError("That event is closed.")
    if seller.linked_staff_id == profile.pk:
        raise LinkError("That seller account is already linked to you.")
    if seller.linked_staff_id:
        raise LinkError("That seller account is linked to someone else. Ask an admin.")
    if Seller.objects.filter(event=seller.event, linked_staff=profile).exists():
        raise LinkError("You already have a seller account linked for this event.")
    seller.linked_staff = profile
    seller.linked_at = timezone.now()
    seller.save(update_fields=["linked_staff", "linked_at"])
    audit(account, "seller.linked", event=seller.event, target=seller, staff=profile.pk)
    return seller


# ---------- Sign-in ----------


def find_accounts(identifier):
    """Every active account that the identifier could mean. Passwords are checked by the caller.

    Sellers sign in with Seller ID or email, staff with Volunteer ID or email.
    Seller accounts past their 30-day limit, and staff with no role, are left out.
    """
    code = normalize_code(identifier)
    if VOLUNTEER_ID_RE.match(code):
        candidates = Account.objects.filter(staff_profile__volunteer_id=code)
    elif parsed := parse_seller_id(code):
        candidates = Account.objects.filter(seller_record__event__code=parsed[0],
                                            seller_record__number=parsed[1])
    elif "@" in code:
        candidates = Account.objects.filter(email__iexact=identifier.strip())
    else:
        return []
    candidates = candidates.filter(is_active=True).select_related(
        "staff_profile", "seller_record__event"
    )
    result = []
    for account in candidates:
        if account.seller and account.seller.login_expired:
            continue
        if account.staff and not account.is_staff_member:
            continue
        result.append(account)
    return result
