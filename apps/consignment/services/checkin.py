"""Check-in rules. See SPEC section 8.

Every game needs a listing and a slip, and each listing is checked in once,
whichever slip version is scanned. The accepted slip is what checkout uses.
"""

import re

from django.db import transaction
from django.utils import timezone

from apps.core.audit import audit
from apps.core.ids import normalize_code
from apps.events.models import Status

from ..models import (
    CheckinEntry,
    CheckinOutcome,
    CheckinReport,
    ItemStatus,
    Listing,
    SlipVersion,
)
from .listings import current_prices, set_status

FULL_ITEM_ID = re.compile(r"^([A-Z0-9]{4})-(\d{1,4})-(\d{1,4})$")
SHORT_ITEM_ID = re.compile(r"^(\d{1,4})-(\d{1,4})$")


class CheckinError(Exception):
    pass


# What a scan found. "ok" waits for the next scan; the others need a decision.
OK = "ok"
OUTDATED = "outdated"
DEACTIVATED = "deactivated"
OUTDATED_DEACTIVATED = "outdated_deactivated"
CHECKED_IN = "checked_in"
ALERT_KINDS = {OUTDATED, DEACTIVATED, OUTDATED_DEACTIVATED}


def require_checkin(event):
    if event.status != Status.CHECKIN:
        raise CheckinError("Check-in is only open while the event is in Check-in.")


def is_own_seller(account, seller):
    """Staff can't check in their own games, admins included."""
    return bool(account.staff and seller.linked_staff_id == account.staff.pk)


def get_report(seller, actor):
    report, _ = CheckinReport.objects.get_or_create(seller=seller, defaults={"started_by": actor})
    return report


def parse_item_id(event, code):
    """Return (event code, full Item ID) for a scanned or typed code, or None.

    Volunteers may type just `042-0001`, which means the current event.
    """
    code = normalize_code(code)
    if match := SHORT_ITEM_ID.match(code):
        return event.code, f"{event.code}-{int(match[1]):03d}-{int(match[2]):04d}"
    if match := FULL_ITEM_ID.match(code):
        return match[1], f"{match[1]}-{int(match[2]):03d}-{int(match[3]):04d}"
    return None


def _entry(report, actor, code, outcome, *, slip=None, reason="", admin=None):
    return CheckinEntry.objects.create(
        report=report, scanned_code=code[:40], listing=slip.listing if slip else None, slip=slip,
        outcome=outcome, reason=reason, approved_by_admin=admin, created_by=actor)


def scan(report, actor, code):
    """Look up a scanned code for this seller.

    Returns (slip, kind). Codes that can't be accepted are recorded on the report
    and raise CheckinError, so nothing changes.
    """
    seller = report.seller
    require_checkin(seller.event)
    parsed = parse_item_id(seller.event, code)
    if parsed and parsed[0] != seller.event.code:
        _entry(report, actor, code, CheckinOutcome.WRONG_EVENT)
        raise CheckinError(f"{normalize_code(code)} belongs to another event. Not accepted.")
    slip = None
    if parsed:
        slip = (SlipVersion.objects.select_related("listing__seller", "listing__current_slip")
                .filter(item_id=parsed[1]).first())
    if slip is None:
        _entry(report, actor, code, CheckinOutcome.NOT_FOUND)
        raise CheckinError(f"{normalize_code(code) or 'That barcode'} wasn't found. Not accepted.")
    if slip.listing.seller_id != seller.pk:
        _entry(report, actor, code, CheckinOutcome.OTHER_SELLER, slip=slip)
        raise CheckinError(f"{slip.item_id} belongs to seller {slip.listing.seller.seller_id}, "
                           "not this seller. Not accepted.")
    return slip, classify(slip)


def classify(slip):
    listing = slip.listing
    if listing.is_checked_in:
        return CHECKED_IN
    outdated = not slip.is_valid
    deactivated = listing.status == ItemStatus.DEACTIVATED
    if outdated and deactivated:
        return OUTDATED_DEACTIVATED
    if outdated:
        return OUTDATED
    if deactivated:
        return DEACTIVATED
    return OK


def comparison(slip):
    """The slip's prices and condition beside the listing's latest details."""
    listing = slip.listing
    latest = current_prices(listing)
    rows = [(p.window.name, p.price_cents, (latest.get(p.window_id) or 0) * 100 or None)
            for p in slip.prices.select_related("window")]
    return {
        "prices": rows,
        "slip_condition": (slip.get_condition_display(), slip.condition_note),
        "latest_condition": (listing.get_condition_display(), listing.condition_note),
    }


def accept(report, actor, slip, *, confirmed=False):
    """Check in the game on this slip, which becomes Available.

    Outdated or deactivated slips need `confirmed`: the volunteer has checked that
    the seller agrees and the slip's information is accurate.
    """
    require_checkin(report.seller.event)
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=slip.listing_id)
        slip = SlipVersion.objects.select_related("listing").get(pk=slip.pk)
        kind = classify(slip)
        if kind == CHECKED_IN:
            raise CheckinError(f"{slip.item_id} was already checked in.")
        if kind in ALERT_KINDS and not confirmed:
            raise CheckinError("Confirm that the seller agrees and the slip is accurate.")
        notes = {OUTDATED: f"Outdated slip {slip.item_id} accepted",
                 DEACTIVATED: "Reactivated at check-in",
                 OUTDATED_DEACTIVATED: f"Reactivated at check-in on outdated slip {slip.item_id}"}
        listing.accepted_slip = slip
        listing.checked_in_at = timezone.now()
        listing.checked_in_by = actor
        set_status(listing, ItemStatus.AVAILABLE, actor, notes.get(kind, ""))
        listing.save(update_fields=["accepted_slip", "checked_in_at", "checked_in_by", "status"])
        outcome = {OK: CheckinOutcome.ACCEPTED, OUTDATED: CheckinOutcome.OUTDATED_ACCEPTED,
                   DEACTIVATED: CheckinOutcome.REACTIVATED,
                   OUTDATED_DEACTIVATED: CheckinOutcome.REACTIVATED}[kind]
        _entry(report, actor, slip.item_id, outcome, slip=slip, reason=notes.get(kind, ""))
    return listing


def reject(report, actor, slip, reason):
    """The game doesn't match its slip. The listing becomes Deactivated."""
    require_checkin(report.seller.event)
    reason = (reason or "").strip()
    if not reason:
        raise CheckinError("Give a reason for rejecting the game.")
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=slip.listing_id)
        if listing.is_checked_in:
            raise CheckinError(f"{slip.item_id} was already checked in.")
        if listing.status != ItemStatus.DEACTIVATED:
            set_status(listing, ItemStatus.DEACTIVATED, actor, f"Rejected at check-in: {reason}")
            listing.save(update_fields=["status"])
        _entry(report, actor, slip.item_id, CheckinOutcome.REJECTED, slip=slip, reason=reason)


def double_scan(report, actor, slip):
    """An accidental second scan of a game already checked in. Nothing changes."""
    _entry(report, actor, slip.item_id, CheckinOutcome.DOUBLE_SCAN, slip=slip)


def resolve_duplicate_slip(report, actor, slip, admin, note, correct_game):
    """Two games with one slip. The admin decides which game the listing belongs to.

    Only one game can be associated with the listing. The other goes back to the seller.
    """
    choice = {"first": "the game checked in earlier", "second": "the game just scanned"}[correct_game]
    _entry(report, actor, slip.item_id, CheckinOutcome.DUPLICATE_SLIP, slip=slip, admin=admin,
           reason=f"Listing kept for {choice}. {note}")
    audit(admin, "checkin.duplicate_slip", event=report.seller.event, target=slip.listing,
          correct_game=correct_game, note=note, volunteer=actor.pk)


def resolve_checked_in(report, actor, slip, admin, note, set_error):
    """Any other scan of a game already checked in, after an admin investigates."""
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=slip.listing_id)
        if set_error and listing.status != ItemStatus.ERROR:
            set_status(listing, ItemStatus.ERROR, actor, f"Check-in override: {note}", admin)
            listing.save(update_fields=["status"])
        _entry(report, actor, slip.item_id, CheckinOutcome.OVERRIDE, slip=slip, admin=admin,
               reason=f"{'Set to Error' if set_error else f'Left {listing.get_status_display()}'}. "
                      f"{note}")


def complete(report, actor):
    report.completed_at = timezone.now()
    report.completed_by = actor
    report.save(update_fields=["completed_at", "completed_by"])
    audit(actor, "checkin.completed", event=report.seller.event, target=report.seller,
          checked_in=report.seller.listings.filter(checked_in_at__isnull=False).count())


def summary(seller):
    listings = list(seller.listings.select_related("accepted_slip", "current_slip"))
    report = getattr(seller, "checkin_report", None)
    entries = list(report.entries.select_related("approved_by_admin__staff_profile",
                                                 "created_by__staff_profile", "listing")
                   ) if report else []
    return {
        "report": report,
        "checked_in": [lst for lst in listings if lst.is_checked_in],
        "not_checked_in": [lst for lst in listings if lst.status == ItemStatus.LISTED],
        "errors": [e for e in entries if e.is_error],
    }
