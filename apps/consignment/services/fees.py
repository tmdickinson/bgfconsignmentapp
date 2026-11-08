"""Seller figures and fees. See SPEC section 9 and the formulas in DATA_MODEL.md.

Figures come from the sale lines until a seller's payout is recorded, and from
their frozen Settlement after that. Reports never recalculate frozen history.
"""

from dataclasses import dataclass, field
from math import ceil

from django.db.models import Sum

from apps.events.models import FeeWaiver, ListingFeeType, WaiverCategory

from ..models import CheckoutLine, CheckoutState, ItemStatus, PayoutOutcome

FINAL_UNSOLD = {ItemStatus.RETURNED, ItemStatus.ABANDONED}
IN_PROGRESS = {ItemStatus.AVAILABLE, ItemStatus.IN_CHECKOUT, ItemStatus.ERROR}


@dataclass
class Figures:
    seller: object
    checked_in: int = 0
    sold: int = 0
    unsold_count: int = 0
    projected: bool = False  # Unsold fees still in progress
    sheets: int = 0
    gross: int = 0
    commission: int = 0
    listing_fee: int = 0
    unsold_fee: int = 0
    waived_commission: int = 0
    waived_listing: int = 0
    waived_unsold: int = 0
    net: int = 0  # Recorded net: 0 when a shortfall is forgiven
    forgiven: int = 0
    frozen: bool = False
    payout: object = None
    waivers: list = field(default_factory=list)

    @property
    def fees(self):
        return self.commission + self.listing_fee + self.unsold_fee

    @property
    def waived(self):
        return self.waived_commission + self.waived_listing + self.waived_unsold

    @property
    def payout_due(self):
        return max(self.net, 0)

    @property
    def balance_owed(self):
        """What the seller owes, when negative balances are charged."""
        return max(-self.net, 0)

    @property
    def collected(self):
        return self.balance_owed if self.payout and self.payout.outcome == PayoutOutcome.COLLECTED else 0

    @property
    def uncollected(self):
        return (self.balance_owed if self.payout and self.payout.outcome == PayoutOutcome.NOT_COLLECTED
                else 0)

    @property
    def fees_kept(self):
        """Revenue counts only fees actually kept."""
        return self.fees - self.forgiven - self.uncollected


def applicable_waivers(seller):
    """The seller's own waiver plus their category's, if linked to staff. Waived fees combine."""
    categories = []
    staff = seller.linked_staff
    if staff:
        if staff.is_volunteer:
            categories.append(WaiverCategory.VOLUNTEER)
        if staff.is_admin:
            categories.append(WaiverCategory.ADMIN)
    waivers = FeeWaiver.objects.filter(event_id=seller.event_id, revoked_at__isnull=True)
    return list(waivers.filter(seller=seller)) + list(
        waivers.filter(seller__isnull=True, category__in=categories))


def sold_lines(seller):
    return CheckoutLine.objects.filter(listing__seller=seller, live=True,
                                       checkout__state=CheckoutState.COMPLETE)


def figures(seller):
    settlement = getattr(seller, "settlement", None)
    if settlement is not None:
        return _from_settlement(seller, settlement)
    return calculate(seller)


def calculate(seller):
    """Live figures from the sale lines and item statuses."""
    event = seller.event
    f = Figures(seller=seller)
    listings = list(seller.listings.filter(checked_in_at__isnull=False)
                    .select_related("accepted_slip"))
    f.checked_in = len(listings)
    totals = sold_lines(seller).aggregate(gross=Sum("price_cents"), commission=Sum("commission_cents"))
    f.gross = totals["gross"] or 0
    commission = totals["commission"] or 0
    f.sold = sum(1 for lst in listings if lst.status == ItemStatus.SOLD)
    if f.checked_in == 0:
        f.net = f.gross
        return f  # Only sellers with at least one game checked in pay any fee.

    if event.listing_fee_type == ListingFeeType.SHEET:
        f.sheets = ceil(f.checked_in / event.items_per_sheet)
        listing_fee = f.sheets * event.sheet_fee_cents
    else:
        listing_fee = event.flat_fee_cents

    final_unsold = set(FINAL_UNSOLD)
    if event.charge_unsold_on_donated:
        final_unsold.add(ItemStatus.DONATED)
    for lst in listings:
        if lst.status in final_unsold:
            f.unsold_count += 1
        elif lst.status in IN_PROGRESS:
            f.projected = True
            # A game marked for donation ends up Donated unless the seller takes it back.
            if event.charge_unsold_on_donated or not lst.accepted_slip.donate_if_unsold:
                f.unsold_count += 1
    unsold_fee = f.unsold_count * event.unsold_fee_cents

    f.waivers = applicable_waivers(seller)
    waive = {name: any(getattr(w, f"waive_{name}") for w in f.waivers)
             for name in ("commission", "listing", "unsold")}
    for name, charged, amount in (("commission", "commission", commission),
                                  ("listing", "listing_fee", listing_fee),
                                  ("unsold", "unsold_fee", unsold_fee)):
        setattr(f, f"waived_{name}" if waive[name] else charged, amount)

    f.net = f.gross - f.fees
    if f.net < 0 and not event.charge_negative_balances:
        f.forgiven, f.net = -f.net, 0
    return f


def _from_settlement(seller, s):
    return Figures(
        seller=seller, checked_in=s.checked_in_count, sold=s.sold_count,
        unsold_count=s.unsold_fee_count, sheets=s.sheets, gross=s.gross_cents,
        commission=s.commission_cents, listing_fee=s.listing_fee_cents,
        unsold_fee=s.unsold_fee_cents, waived_commission=s.waived_commission_cents,
        waived_listing=s.waived_listing_cents, waived_unsold=s.waived_unsold_cents,
        net=s.net_cents, forgiven=s.forgiven_cents, frozen=True,
        payout=getattr(s, "payout", None),
    )


@dataclass
class Totals:
    gross: int = 0
    commission: int = 0
    listing_fee: int = 0
    unsold_fee: int = 0
    waived_commission: int = 0
    waived_listing: int = 0
    waived_unsold: int = 0
    forgiven: int = 0
    uncollected: int = 0
    collected: int = 0
    payouts: int = 0
    fees_kept: int = 0
    projected: bool = False

    @property
    def fees(self):
        return self.commission + self.listing_fee + self.unsold_fee

    @property
    def waived(self):
        return self.waived_commission + self.waived_listing + self.waived_unsold

    @property
    def balanced(self):
        """The money invariant: total sales + balances collected = payouts + fees kept."""
        return self.gross + self.collected == self.payouts + self.fees_kept


def event_figures(event):
    sellers = (event.sellers.select_related("event", "linked_staff", "settlement__payout")
               .order_by("number"))
    rows = [figures(s) for s in sellers]
    t = Totals()
    for f in rows:
        for name in ("gross", "commission", "listing_fee", "unsold_fee", "waived_commission",
                     "waived_listing", "waived_unsold", "forgiven", "uncollected", "collected",
                     "fees_kept"):
            setattr(t, name, getattr(t, name) + getattr(f, name))
        t.payouts += f.payout_due
        t.projected = t.projected or f.projected
    return rows, t
