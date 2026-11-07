"""Event rules: setup, fee locks, forward-only statuses, reminders and waivers."""

from django.db import transaction
from django.utils import timezone

from apps.core.audit import audit

from .models import (
    STATUS_ORDER,
    Event,
    EventStatusSchedule,
    FeeWaiver,
    PriceWindow,
    Status,
    WaiverCategory,
)


class EventError(Exception):
    pass


FEE_FIELDS = [
    "listing_fee_type", "sheet_fee_cents", "items_per_sheet", "flat_fee_cents",
    "commission_percent", "unsold_fee_cents", "charge_unsold_on_donated",
    "charge_negative_balances",
]
# Changeable during Setup only.
SETUP_ONLY_FIELDS = ["code"]


def create_event(actor, *, window_names, **fields):
    with transaction.atomic():
        event = Event.objects.create(**fields)
        for position, name in enumerate(window_names, start=1):
            PriceWindow.objects.create(event=event, position=position, name=name)
        for status in STATUS_ORDER[1:]:
            EventStatusSchedule.objects.create(event=event, status=status)
        audit(actor, "event.created", event=event, target=event, code=event.code)
    return event


def locked_fields(event):
    locked = []
    if event.fees_locked:
        locked += FEE_FIELDS
    if event.status != Status.SETUP:
        locked += SETUP_ONLY_FIELDS
    return locked


def update_event(actor, event, changes):
    """Apply setting changes, refusing any that are locked. `changes` maps field to value."""
    event.refresh_from_db(fields=["status", "fees_locked_at"])
    if event.is_closed:
        raise EventError("The event is closed.")
    locked = set(locked_fields(event))
    changed = {}
    for field, value in changes.items():
        old = getattr(event, field)
        if old == value:
            continue
        if field in locked:
            raise EventError(f"{Event._meta.get_field(field).verbose_name} is locked.")
        changed[field] = [str(old), str(value)]
        setattr(event, field, value)
    if changed:
        event.full_clean()
        event.save()
        audit(actor, "event.updated", event=event, target=event, changes=changed)
    return changed


def windows_locked(event):
    """Window count and names lock once any item is added (SPEC section 4)."""
    from apps.consignment.models import Listing

    return event.is_closed or Listing.objects.filter(seller__event=event).exists()


def update_windows(actor, event, rows):
    """`rows` is a list of dicts with name, scheduled_start and scheduled_end, in order.

    Schedules can always change. Names and the number of windows lock with the items.
    """
    if event.is_closed:
        raise EventError("The event is closed.")
    with transaction.atomic():
        existing = list(event.windows.all())
        names_changed = (len(rows) != len(existing)
                         or any(r["name"] != w.name for r, w in zip(rows, existing, strict=False)))
        if names_changed and windows_locked(event):
            raise EventError("Price window names and count are locked once items are added.")
        if not rows:
            raise EventError("An event needs at least one price window.")
        for window in existing[len(rows):]:
            window.delete()  # Only possible before any item exists, so nothing refers to it.
        for position, row in enumerate(rows, start=1):
            window = existing[position - 1] if position <= len(existing) else PriceWindow(
                event=event, position=position)
            window.name = row["name"]
            window.scheduled_start = row.get("scheduled_start")
            window.scheduled_end = row.get("scheduled_end")
            window.save()
        audit(actor, "event.windows_updated", event=event, target=event,
              windows=[{"name": r["name"], "start": str(r.get("scheduled_start") or ""),
                        "end": str(r.get("scheduled_end") or "")} for r in rows])


def update_schedule(actor, event, times):
    """`times` maps status to a datetime or None. Only reminders, never automatic."""
    with transaction.atomic():
        for row in event.schedule.all():
            if row.status in times and row.scheduled_at != times[row.status]:
                row.scheduled_at = times[row.status]
                row.save(update_fields=["scheduled_at"])
        audit(actor, "event.schedule_updated", event=event, target=event,
              times={k: str(v or "") for k, v in times.items()})


# Checks that must pass before an event can enter a status. Later phases add the
# open-cart and closing checks here. Each takes the event and returns a list of problems.
STATUS_CHECKS = {
    Status.PRESALE: [
        lambda event: [] if event.windows.exists() else ["Add at least one price window."],
    ],
}


def status_blockers(event):
    target = event.next_status
    if target is None:
        return ["The event is already in its final status."]
    problems = []
    for check in STATUS_CHECKS.get(target, []):
        problems += check(event)
    return problems


def advance_status(actor, event, expected_status):
    """Move the event forward one status. `expected_status` guards against double submits."""
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        if event.status != expected_status:
            raise EventError("The status has already changed. Reload the page.")
        problems = status_blockers(event)
        if problems:
            raise EventError(" ".join(problems))
        old, new = event.status, event.next_status
        now = timezone.now()
        event.status = new
        fields = ["status"]
        if new == Status.POSTSALE:
            event.moved_to_postsale_at = now
            event.closed_by = actor
            fields += ["moved_to_postsale_at", "closed_by"]
        event.save(update_fields=fields)
        EventStatusSchedule.objects.filter(event=event, status=new).update(
            moved_at=now, moved_by=actor
        )
        audit(actor, "event.status_changed", event=event, target=event, old=old, new=new)
    return event


def due_reminders(event, now=None):
    """Scheduled status changes whose time has passed but haven't happened."""
    now = now or timezone.now()
    return [
        row for row in event.schedule.all()
        if row.scheduled_at and row.scheduled_at <= now
        and STATUS_ORDER.index(row.status) > event.status_index
    ]


def set_waiver(actor, event, *, category, seller=None, commission, listing, unsold, note=""):
    """Replace the current waiver for a category or seller. All false removes it."""
    event.refresh_from_db(fields=["status"])
    if event.is_closed:
        raise EventError("Waivers can't change after the event closes.")
    if (category == WaiverCategory.SELLER) != (seller is not None):
        raise EventError("Individual waivers need a seller, and only those.")
    with transaction.atomic():
        current = FeeWaiver.objects.select_for_update().order_by().filter(
            event=event, category=category, seller=seller, revoked_at__isnull=True
        ).first()
        if current and (current.waive_commission, current.waive_listing,
                        current.waive_unsold) == (commission, listing, unsold):
            return current
        if current:
            current.revoked_at = timezone.now()
            current.save(update_fields=["revoked_at"])
        waiver = None
        if commission or listing or unsold:
            waiver = FeeWaiver.objects.create(
                event=event, category=category, seller=seller, waive_commission=commission,
                waive_listing=listing, waive_unsold=unsold, note=note, set_by=actor,
            )
        audit(actor, "waiver.set", event=event, target=seller or event, category=category,
              commission=commission, listing=listing, unsold=unsold, note=note)
    return waiver
