"""Admin reports (SPEC section 12). Each builds headings and rows for HTML and CSV alike.

Money cells are integer cents, formatted for display and as plain dollars in CSV.
"""

from dataclasses import dataclass, field

from django.utils import timezone

from apps.events.models import FeeWaiver

from .models import CheckoutKind, CheckoutLine, CheckoutState, ItemStatus, Listing
from .services.fees import event_figures


class Money(int):
    """Marks a cell as cents."""


@dataclass
class Report:
    title: str
    headings: list
    rows: list
    totals: list | None = None
    notes: list = field(default_factory=list)


def _when(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""


def financial(event):
    rows_, t = event_figures(event)
    rows = []
    for f in rows_:
        s = f.seller
        rows.append([
            s.seller_id, s.name, s.payment_method, f.checked_in, f.sold, Money(f.gross),
            Money(f.commission), Money(f.listing_fee), Money(f.unsold_fee),
            "Projected" if f.projected else "", Money(f.waived), Money(f.payout_due),
            Money(f.balance_owed), Money(f.forgiven),
            f.payout.get_outcome_display() if f.payout else "",
            f.payout.reference if f.payout else "",
        ])
    totals = ["Total", "", "", sum(f.checked_in for f in rows_), sum(f.sold for f in rows_),
              Money(t.gross), Money(t.commission), Money(t.listing_fee), Money(t.unsold_fee),
              "Projected" if t.projected else "", Money(t.waived), Money(t.payouts),
              Money(sum(f.balance_owed for f in rows_)), Money(t.forgiven), "", ""]
    return Report("Financial report", [
        "Seller ID", "Name", "Payment method", "Checked in", "Sold", "Gross sales", "Commission",
        "Listing fee", "Unsold fees", "Unsold fees status", "Waived", "Net payout",
        "Balance owed", "Forgiven", "Payout outcome", "Reference"], rows, totals,
        ["Unsold fees marked Projected are still in progress."] if t.projected else [])


def revenue(event):
    _, t = event_figures(event)
    rows = [
        ["Commission kept", Money(t.commission)],
        ["Listing fees kept", Money(t.listing_fee)],
        ["Unsold fees kept", Money(t.unsold_fee)],
        ["Less forgiven balances", Money(-t.forgiven)],
        ["Less uncollected balances", Money(-t.uncollected)],
        ["Fees kept (revenue)", Money(t.fees_kept)],
        ["Waived commission (not revenue)", Money(t.waived_commission)],
        ["Waived listing fees (not revenue)", Money(t.waived_listing)],
        ["Waived unsold fees (not revenue)", Money(t.waived_unsold)],
        ["Forgiven balances (not revenue)", Money(t.forgiven)],
        ["Uncollected balances (not revenue)", Money(t.uncollected)],
        ["Total sales", Money(t.gross)],
        ["Balances collected", Money(t.collected)],
        ["Seller payouts", Money(t.payouts)],
    ]
    notes = ["Check: total sales + balances collected = payouts + fees kept. "
             + ("Balanced." if t.balanced else "NOT BALANCED. Investigate before closing.")]
    if t.projected:
        notes.append("Unsold fees are still projected.")
    return Report("Revenue", ["Line", "Amount"], rows, notes=notes)


def sales(event):
    checkouts = (event.checkouts.filter(state=CheckoutState.COMPLETE)
                 .select_related("owner", "register", "opened_by_admin", "window")
                 .order_by("sale_id"))
    rows = [[c.sale_id, _when(c.paid_at), c.owner.display_name, c.register.name,
             c.get_kind_display(), c.opened_by_admin.display_name if c.opened_by_admin else "",
             c.lines.filter(live=True).count(), Money(c.total_cents)] for c in checkouts]
    return Report("Completed sales", ["Sale ID", "Paid", "Staff", "Register", "Kind",
                                      "Authorized by", "Items", "Total"], rows,
                  ["Match these to Square by Sale ID."])


def sale_lines(event):
    lines = (CheckoutLine.objects.filter(checkout__event=event)
             .exclude(checkout__sale_id__isnull=True)
             .select_related("checkout", "slip", "window", "listing__seller__event",
                             "removed_by", "released_by_admin")
             .order_by("checkout__sale_id", "added_at"))
    rows = []
    for line in lines:
        state = ("Released" if line.released_at else "Removed" if line.removed_at
                 else "Bought")
        rows.append([line.checkout.sale_id, line.checkout.get_state_display(),
                     line.slip.item_id, line.slip.name, line.listing.seller.seller_id,
                     line.window.name, Money(line.price_cents), Money(line.commission_cents),
                     state, _when(line.added_at)])
    return Report("Sale transaction detail", [
        "Sale ID", "Sale state", "Item ID", "Game", "Seller ID", "Window", "Price", "Commission",
        "Line", "Scanned"], rows, ["Every line of every sale with a Sale ID, including "
                                   "canceled sales and removed games."])


def waivers(event):
    rows = [[w.get_category_display(), w.seller.seller_id if w.seller else "",
             w.seller.name if w.seller else "", ", ".join(w.waived_labels), w.note,
             w.set_by.display_name, _when(w.set_at), _when(w.revoked_at) or "Current"]
            for w in FeeWaiver.objects.filter(event=event).select_related("seller__event",
                                                                          "set_by")]
    return Report("Fee waivers", ["Category", "Seller ID", "Seller", "Waived", "Note", "Set by",
                                  "Set", "Replaced"], rows)


def admin_carts(event):
    checkouts = (event.checkouts.filter(kind=CheckoutKind.ADMIN, sale_id__isnull=False)
                 .select_related("opened_by_admin", "window", "register"))
    rows = [[c.sale_id, c.get_state_display(), c.opened_by_admin.display_name,
             c.get_pricing_mode_display(), c.window.name if c.window else "", c.register.name,
             _when(c.paid_at or c.canceled_at), Money(c.total_cents)] for c in checkouts]
    return Report("Admin-cart sales", ["Sale ID", "State", "Authorized by", "Pricing", "Window",
                                       "Register", "When", "Total"], rows)


def donated_abandoned(event):
    listings = (Listing.objects.filter(seller__event=event,
                                       status__in=[ItemStatus.DONATED, ItemStatus.ABANDONED])
                .select_related("accepted_slip", "seller__event")
                .order_by("status", "seller__number"))
    rows = [[lst.accepted_slip.item_id, lst.accepted_slip.name, lst.seller.seller_id,
             lst.get_status_display(), "Yes" if lst.accepted_slip.donate_if_unsold else "No"]
            for lst in listings]
    return Report("Donated and abandoned games", ["Item ID", "Game", "Seller ID", "Status",
                                                  "Marked for donation"], rows,
                  ["The games the convention should hold at the end."])


def staff_sellers(event):
    sellers = event.sellers.filter(linked_staff__isnull=False).select_related("linked_staff",
                                                                              "event")
    rows = [[s.seller_id, s.name, s.linked_staff.name,
             " and ".join(r for r, on in (("Admin", s.linked_staff.is_admin),
                                          ("Volunteer", s.linked_staff.is_volunteer)) if on),
             _when(s.linked_at)] for s in sellers]
    return Report("Staff who are sellers", ["Seller ID", "Seller name", "Staff member", "Roles",
                                            "Linked"], rows)


# Slug: (title, description, builder)
REPORTS = {
    "financial": ("Financial report", "Per seller and in total: sales, each fee, net payout or "
                  "balance, payment method and payout outcome", financial),
    "revenue": ("Revenue", "Fees kept, with waived, forgiven and uncollected amounts shown "
                "separately, and the money check", revenue),
    "sales": ("Completed sales", "Every completed sale by Sale ID, for matching to Square", sales),
    "sale-lines": ("Sale transaction detail", "Every line of every sale, for investigating "
                   "discrepancies", sale_lines),
    "waivers": ("Fee waivers", "Every waiver and who set it", waivers),
    "admin-carts": ("Admin-cart sales", "Sales outside the regular windows and who authorized "
                    "them", admin_carts),
    "donated-abandoned": ("Donated and abandoned games", "What the convention should hold at "
                          "the end", donated_abandoned),
    "staff-sellers": ("Staff who are sellers", "Staff linked to a seller account in this event",
                      staff_sellers),
}
