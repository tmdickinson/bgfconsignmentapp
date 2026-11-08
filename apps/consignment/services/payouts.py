"""Payout recording and closing checks. See SPEC section 11.

The app reports what is owed and records what happened. Recording a payout
freezes the seller's figures in a Settlement.
"""

from django.db import transaction

from apps.accounts.models import Seller
from apps.core.audit import audit
from apps.events.models import Status

from ..models import (
    ACTIVE_STATES,
    Checkout,
    ItemStatus,
    Listing,
    Payout,
    PayoutOutcome,
    RegisterConflict,
    Settlement,
    SoldItemFlag,
)
from .fees import calculate

ACCOUNTED = {ItemStatus.SOLD, ItemStatus.RETURNED, ItemStatus.DONATED, ItemStatus.ABANDONED}
AUTOMATIC = {PayoutOutcome.NOTHING_DUE, PayoutOutcome.FORGIVEN}


class PayoutError(Exception):
    pass


def delivered_sellers(event):
    """Sellers with at least one game checked in. Only they need a payout outcome."""
    return event.sellers.filter(listings__checked_in_at__isnull=False).distinct()


def blockers(seller):
    """Why the seller's payout can't be recorded yet."""
    problems = []
    if seller.event.status != Status.PICKUP:
        problems.append("Payouts are recorded during Pickup.")
    if hasattr(seller, "settlement"):
        problems.append("The payout is already recorded.")
    open_games = (seller.listings.filter(checked_in_at__isnull=False)
                  .exclude(status__in=ACCOUNTED))
    if open_games.exists():
        names = ", ".join(f"{g.name} ({g.get_status_display()})" for g in open_games[:5])
        problems.append(f"Not every checked-in game is accounted for: {names}.")
    if SoldItemFlag.objects.filter(listing__seller=seller, resolved_at__isnull=True).exists():
        problems.append("A Sold flag for one of the seller's games is open.")
    if not seller.listings.filter(checked_in_at__isnull=False).exists():
        problems.append("The seller had no games checked in, so no payout is needed.")
    return problems


def allowed_outcomes(f):
    """The outcomes that fit a seller's final figures."""
    if f.forgiven:
        return [PayoutOutcome.FORGIVEN]
    if f.net > 0:
        return [PayoutOutcome.PAID]
    if f.net == 0:
        return [PayoutOutcome.NOTHING_DUE]
    return [PayoutOutcome.COLLECTED, PayoutOutcome.NOT_COLLECTED]


def record(seller, actor, outcome, reference="", note=""):
    """Freeze the seller's figures and record what happened."""
    reference, note = reference.strip(), note.strip()
    with transaction.atomic():
        seller = (Seller.objects.select_for_update(of=("self",))
                  .select_related("event", "linked_staff").get(pk=seller.pk))
        problems = blockers(seller)
        if problems:
            raise PayoutError(" ".join(problems))
        f = calculate(seller)
        if outcome not in allowed_outcomes(f):
            raise PayoutError(f"{PayoutOutcome(outcome).label} doesn't fit these figures.")
        if outcome == PayoutOutcome.PAID and not reference:
            raise PayoutError("Enter the transaction or check number.")
        settlement = Settlement.objects.create(
            seller=seller, checked_in_count=f.checked_in, sold_count=f.sold,
            unsold_fee_count=f.unsold_count, sheets=f.sheets, gross_cents=f.gross,
            commission_cents=f.commission, listing_fee_cents=f.listing_fee,
            unsold_fee_cents=f.unsold_fee, waived_commission_cents=f.waived_commission,
            waived_listing_cents=f.waived_listing, waived_unsold_cents=f.waived_unsold,
            net_cents=f.net, forgiven_cents=f.forgiven, frozen_by=actor)
        payout = Payout.objects.create(settlement=settlement, outcome=outcome,
                                       reference=reference, note=note, recorded_by=actor)
        audit(actor, "payout.recorded", event=seller.event, target=seller, outcome=outcome,
              net_cents=f.net, forgiven_cents=f.forgiven, reference=reference)
    return payout


def record_automatic(event, actor):
    """Record Nothing due and Balance forgiven for every seller ready for one."""
    recorded = 0
    for seller in delivered_sellers(event).select_related("event", "linked_staff"):
        if blockers(seller):
            continue
        outcome = allowed_outcomes(calculate(seller))[0]
        if outcome in AUTOMATIC:
            record(seller, actor, outcome)
            recorded += 1
    return recorded


def payout_recorded_for(checkout):
    return Payout.objects.filter(
        settlement__seller__listings__checkout_lines__checkout=checkout).exists()


def closing_blockers(event):
    """What must be true before an event moves to Post-sale, which closes it."""
    problems = []
    open_items = Listing.objects.filter(
        seller__event=event, status__in=[ItemStatus.AVAILABLE, ItemStatus.IN_CHECKOUT,
                                         ItemStatus.MISSING, ItemStatus.ERROR])
    if open_items.exists():
        counts = {}
        for status in open_items.values_list("status", flat=True):
            counts[status] = counts.get(status, 0) + 1
        problems.append("Games still " + ", ".join(
            f"{ItemStatus(s).label}: {n}" for s, n in counts.items()) + ".")
    if Checkout.objects.filter(event=event, state__in=ACTIVE_STATES).exists():
        problems.append("A cart is still open.")
    if SoldItemFlag.objects.filter(event=event, resolved_at__isnull=True).exists():
        problems.append("A Sold flag is open.")
    if RegisterConflict.objects.filter(event=event, resolved_at__isnull=True).exists():
        problems.append("A two-register conflict is open.")
    missing = delivered_sellers(event).filter(settlement__isnull=True).count()
    if missing:
        problems.append(f"{missing} seller{'s' if missing != 1 else ''} who delivered games "
                        f"{'have' if missing != 1 else 'has'} no payout outcome.")
    return problems
