"""Listing and slip rules. See SPEC sections 5 and 6."""

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.models import Seller
from apps.core.audit import audit
from apps.events.models import Status

from ..models import (
    Condition,
    ItemStatus,
    ItemStatusChange,
    Listing,
    ListingPrice,
    SlipPrice,
    SlipVersion,
)

DETAIL_FIELDS = ["name", "publisher", "description", "condition", "condition_note", "is_bundle",
                 "bundle_count", "bgg_id", "slip_size", "donate_if_unsold"]
# What a slip prints. BGG ID is never printed, so changing it doesn't outdate a slip.
PRINTED_FIELDS = ["name", "publisher", "description", "condition", "condition_note",
                  "slip_size", "donate_if_unsold"]


class ListingError(Exception):
    pass


def require_presale(seller):
    if seller.event.status != Status.PRESALE:
        raise ListingError("Listings can only be changed during Presale.")


def can_print(seller):
    """Valid slips can be reprinted through the end of Check-in."""
    return seller.event.status in (Status.PRESALE, Status.CHECKIN)


def set_status(listing, to_status, actor, note=""):
    ItemStatusChange.objects.create(listing=listing, from_status=listing.status,
                                    to_status=to_status, changed_by=actor, note=note)
    listing.status = to_status


def _apply(listing, details, prices):
    for field in DETAIL_FIELDS:
        if field in details:
            setattr(listing, field, details[field])
    if not listing.is_bundle:
        listing.bundle_count = None
    listing.updated_at = timezone.now()
    if listing.pk:
        listing.save(update_fields=[*DETAIL_FIELDS, "updated_at"])
    else:
        listing.save()
    for window in listing.seller.event.windows.all():
        ListingPrice.objects.update_or_create(listing=listing, window=window,
                                              defaults={"dollars": prices.get(window.pk)})


def _locked(listing):
    """A fresh, locked copy, so a stale object from the caller can't overwrite newer state."""
    return (Listing.objects.select_for_update(of=("self",))
            .select_related("seller__event", "current_slip").get(pk=listing.pk))


def current_prices(listing):
    return {p.window_id: p.dollars for p in listing.prices.all()}


def create_listing(seller, actor, details, prices):
    require_presale(seller)
    with transaction.atomic():
        listing = Listing(seller=seller)
        _apply(listing, details, prices)
        ItemStatusChange.objects.create(listing=listing, from_status="",
                                        to_status=ItemStatus.LISTED, changed_by=actor)
    return listing


def is_editable(listing):
    """Sellers edit a listing freely until a slip exists. After that, only by deactivating."""
    return listing.status == ItemStatus.LISTED and not listing.has_valid_slip


def update_listing(listing, actor, details, prices):
    require_presale(listing.seller)
    if not is_editable(listing):
        raise ListingError("This listing has a slip. Deactivate it to make changes.")
    with transaction.atomic():
        listing = _locked(listing)
        if not is_editable(listing):
            raise ListingError("This listing has a slip. Deactivate it to make changes.")
        _apply(listing, details, prices)
    return listing


def deactivate(listing, actor, note=""):
    """Deactivating keeps the Item ID and barcode."""
    require_presale(listing.seller)
    with transaction.atomic():
        listing = _locked(listing)
        if listing.status != ItemStatus.LISTED:
            raise ListingError("Only listed games can be deactivated.")
        set_status(listing, ItemStatus.DEACTIVATED, actor, note)
        listing.save(update_fields=["status"])
    return listing


def differs_from_slip(listing, slip):
    for field in PRINTED_FIELDS:
        if getattr(listing, field) != getattr(slip, field):
            return True
    if (listing.bundle_count if listing.is_bundle else None) != slip.bundle_count:
        return True
    slip_prices = {p.window_id: p.price_cents for p in slip.prices.all()}
    listing_prices = {w: (d * 100 if d is not None else None)
                      for w, d in current_prices(listing).items()}
    return slip_prices != listing_prices


def invalidate_slip(listing):
    """Mark the listing's slip outdated. The caller holds the listing's lock."""
    slip = listing.current_slip
    if slip is None:
        return
    slip.invalidated_at = timezone.now()
    slip.save(update_fields=["invalidated_at"])
    listing.current_slip = None
    listing.save(update_fields=["current_slip"])


def reactivate(listing, actor, details=None, prices=None):
    """Reactivate with no changes (the Item ID stays valid) or with changes.

    Any change to what the slip prints, however small, invalidates the slip, and
    the next generated slip gets a new Item ID.
    """
    require_presale(listing.seller)
    with transaction.atomic():
        listing = _locked(listing)
        if listing.status != ItemStatus.DEACTIVATED:
            raise ListingError("Only deactivated games can be reactivated.")
        if details is not None:
            _apply(listing, details, prices or {})
            if listing.current_slip and differs_from_slip(listing, listing.current_slip):
                invalidate_slip(listing)
        set_status(listing, ItemStatus.LISTED, actor,
                   "Reactivated with changes" if details is not None else "Reactivated")
        listing.save(update_fields=["status"])
    return listing


def problems(listing):
    """What a listing still needs before it can get a slip."""
    missing = []
    for field, label in (("name", "name"), ("publisher", "publisher"),
                         ("description", "description"), ("condition", "condition")):
        if not getattr(listing, field).strip():
            missing.append(label)
    if listing.condition == Condition.FAIR and not listing.condition_note.strip():
        missing.append("condition details")
    if listing.is_bundle and not (listing.bundle_count and listing.bundle_count >= 2):
        missing.append("number of items in the bundle")
    prices = current_prices(listing)
    windows = listing.seller.event.windows.all()
    unpriced = [w.name for w in windows if not prices.get(w.pk)]
    if unpriced:
        missing.append("price for " + ", ".join(unpriced))
    return missing


def generate_slips(seller, actor):
    """Create slips for every active listing without a valid slip.

    Every active listing must be complete first. Returns the new SlipVersions.
    """
    require_presale(seller)
    with transaction.atomic():
        # Locking the seller keeps Item ID numbers sequential.
        seller = Seller.objects.select_for_update().select_related("event").get(pk=seller.pk)
        active = list(seller.listings.exclude(status=ItemStatus.DEACTIVATED)
                      .prefetch_related("prices"))
        incomplete = [(listing, problems(listing)) for listing in active]
        incomplete = [(listing, p) for listing, p in incomplete if p]
        if incomplete:
            raise ListingError(
                "Finish or deactivate these games first: "
                + "; ".join(f"{listing.name or 'Untitled'} (needs {', '.join(p)})"
                            for listing, p in incomplete))
        sequence = SlipVersion.objects.filter(listing__seller=seller).aggregate(
            n=Max("sequence"))["n"] or 0
        created = []
        for listing in active:
            if listing.has_valid_slip:
                continue
            sequence += 1
            slip = SlipVersion.objects.create(
                listing=listing, sequence=sequence,
                item_id=f"{seller.seller_id}-{sequence:04d}",
                name=listing.name, publisher=listing.publisher, description=listing.description,
                condition=listing.condition, condition_note=listing.condition_note,
                bundle_count=listing.bundle_count if listing.is_bundle else None,
                slip_size=listing.slip_size, donate_if_unsold=listing.donate_if_unsold,
                generated_by=actor,
            )
            for price in listing.prices.all():
                SlipPrice.objects.create(slip=slip, window_id=price.window_id,
                                         price_cents=price.dollars * 100)
            listing.current_slip = slip
            listing.save(update_fields=["current_slip"])
            created.append(slip)
        if created:
            audit(actor, "slips.generated", event=seller.event, target=seller,
                  item_ids=[s.item_id for s in created])
    return created
