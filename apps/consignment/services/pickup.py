"""Pickup, donated and abandoned scanning, and admin status changes. See SPEC section 11."""

from django.db import transaction

from apps.core.audit import audit
from apps.core.ids import normalize_code
from apps.events.models import Status

from ..models import (
    FlagContext,
    ItemStatus,
    Listing,
    PickupEntry,
    PickupOutcome,
    SlipVersion,
    SoldItemFlag,
)
from .checkin import parse_item_id
from .listings import set_status


class PickupError(Exception):
    pass


# Scans that need the seller's answer before anything changes.
TAKE = "take"  # Listed or Deactivated: never checked in, the seller may take it
DONATED = "donated"
ABANDONED = "abandoned"


def require_pickup(event):
    if event.status != Status.PICKUP:
        raise PickupError("This is only available while the event is in Pickup.")


def require_not_frozen(listing):
    if hasattr(listing.seller, "settlement"):
        raise PickupError(f"{listing.seller.seller_id}'s payout is recorded, so their figures are "
                          "frozen. Ask an admin.")


def find(event, code):
    code = normalize_code(code)
    parsed = parse_item_id(event, code)
    if parsed is None:
        raise PickupError(f"{code or 'That'} isn't an Item ID.")
    if parsed[0] != event.code:
        raise PickupError(f"{parsed[1]} belongs to another event.")
    slip = SlipVersion.objects.select_related("listing__seller__event").filter(
        item_id=parsed[1]).first()
    if slip is None:
        raise PickupError(f"{parsed[1]} wasn't found.")
    return slip.listing


def _entry(listing, actor, outcome, note=""):
    PickupEntry.objects.create(listing=listing, outcome=outcome, note=note, created_by=actor)


def _raise_sold_flag(listing, actor):
    flag, created = SoldItemFlag.objects.get_or_create(
        listing=listing, resolved_at__isnull=True,
        defaults={"event": listing.seller.event, "context": FlagContext.PICKUP, "raised_by": actor})
    if created:
        audit(actor, "sold_flag.raised", event=listing.seller.event, target=listing,
              context="pickup")


def scan_return(seller, actor, code):
    """A game going back to its seller. Returns (listing, decision needed or None, message)."""
    require_pickup(seller.event)
    listing = find(seller.event, code)
    name = listing.accepted_slip.name if listing.accepted_slip_id else listing.name
    if listing.seller_id != seller.pk:
        raise PickupError(f"{name} belongs to seller {listing.seller.seller_id}, not this seller.")
    require_not_frozen(listing)
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=listing.pk)
        s = listing.status
        if s == ItemStatus.AVAILABLE:
            _return(listing, actor, "Returned at pickup")
            return listing, None, f"{name} returned."
        if s in (ItemStatus.LISTED, ItemStatus.DEACTIVATED):
            return listing, TAKE, (f"{name} was {listing.get_status_display().lower()} and never "
                                   "checked in. The seller may take it.")
        if s == ItemStatus.DONATED:
            return listing, DONATED, f"{name} was donated. Does the seller want it back?"
        if s == ItemStatus.ABANDONED:
            return listing, ABANDONED, f"{name} was abandoned. Does the seller want it back?"
        if s == ItemStatus.SOLD:
            _raise_sold_flag(listing, actor)
            _entry(listing, actor, PickupOutcome.REFUSED, "Sold. Set aside for an admin.")
            message = (f"{name} is SOLD and can't be returned. Set the game aside. "
                             "An admin has been alerted.")
        elif s in (ItemStatus.ERROR, ItemStatus.MISSING):
            _entry(listing, actor, PickupOutcome.REFUSED, f"{listing.get_status_display()}.")
            message = (f"{name} is {listing.get_status_display()}. An admin must resolve it "
                             "before it can be returned.")
        elif s == ItemStatus.IN_CHECKOUT:
            message = f"{name} is in a cart. That sale must finish first."
        else:
            message = f"{name} was already returned."
    # Raised after the block, so the Sold flag and history entry are kept.
    raise PickupError(message)


def _return(listing, actor, note):
    set_status(listing, ItemStatus.RETURNED, actor, note)
    listing.save(update_fields=["status"])
    _entry(listing, actor, PickupOutcome.RETURNED, note)


def decide(listing, actor, kind, seller_takes):
    """The seller's answer for a game that needed one."""
    require_pickup(listing.seller.event)
    require_not_frozen(listing)
    expected = {TAKE: (ItemStatus.LISTED, ItemStatus.DEACTIVATED), DONATED: (ItemStatus.DONATED,),
                ABANDONED: (ItemStatus.ABANDONED,)}[kind]
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=listing.pk)
        if listing.status not in expected:
            raise PickupError("The game's status changed. Scan it again.")
        if seller_takes:
            notes = {TAKE: "Taken by the seller at pickup, never checked in",
                     DONATED: "Seller took back a donated game. The unsold fee now applies.",
                     ABANDONED: "Seller took back an abandoned game. The fee is unchanged."}
            _return(listing, actor, notes[kind])
        elif kind == DONATED:
            _entry(listing, actor, PickupOutcome.KEPT_DONATED)
        elif kind == ABANDONED:
            _entry(listing, actor, PickupOutcome.LEFT_ABANDONED)


def scan_unsold(event, actor, code):
    """Account for a remaining unsold game: Donated if marked for donation, else Abandoned."""
    require_pickup(event)
    listing = find(event, code)
    require_not_frozen(listing)
    with transaction.atomic():
        listing = (Listing.objects.select_for_update(of=("self",))
                   .select_related("accepted_slip").get(pk=listing.pk))
        name = listing.accepted_slip.name if listing.accepted_slip_id else listing.name
        if listing.status == ItemStatus.SOLD:
            _raise_sold_flag(listing, actor)
            _entry(listing, actor, PickupOutcome.REFUSED, "Sold, found with unsold games.")
            return False, f"{name} is SOLD. Set it aside. An admin has been alerted."
        if listing.status != ItemStatus.AVAILABLE:
            return False, f"{name} is {listing.get_status_display()}, not an unsold game."
        donate = listing.accepted_slip.donate_if_unsold
        new = ItemStatus.DONATED if donate else ItemStatus.ABANDONED
        set_status(listing, new, actor, "Donated and abandoned scanning")
        listing.save(update_fields=["status"])
    return True, f"{name}: {new.label}."


# Admins can mark games Missing and resolve Error games. Handling of both is still
# to be decided by the treasurer (OPEN_ITEMS.md), so only these moves are offered.
ADMIN_MOVES = {
    ItemStatus.AVAILABLE: [ItemStatus.MISSING],
    ItemStatus.MISSING: [ItemStatus.AVAILABLE],
    ItemStatus.ERROR: [ItemStatus.AVAILABLE, ItemStatus.MISSING],
}


def admin_set_status(listing, admin, new_status, note):
    note = (note or "").strip()
    if not note:
        raise PickupError("A note is required.")
    require_not_frozen(listing)
    if listing.seller.event.is_closed:
        raise PickupError("The event is closed.")
    with transaction.atomic():
        listing = Listing.objects.select_for_update(of=("self",)).get(pk=listing.pk)
        if new_status not in ADMIN_MOVES.get(listing.status, []):
            raise PickupError(f"A {listing.get_status_display()} game can't be set to "
                              f"{ItemStatus(new_status).label} here.")
        set_status(listing, new_status, admin, note, admin)
        listing.save(update_fields=["status"])
        audit(admin, "item.status_set", event=listing.seller.event, target=listing,
              status=new_status, note=note)
