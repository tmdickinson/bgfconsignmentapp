"""Pickup, payouts, reports and seller statements."""

import csv

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Seller
from apps.accounts.services import parse_seller_id
from apps.core.money import format_cents
from apps.core.permissions import admin_required, event_view, seller_view

from .models import ItemStatus, Listing
from .reports import REPORTS, Money, Report
from .services import fees, payouts
from .services import pickup as svc

# ---------- Pickup ----------


@event_view
def pickup_sellers(request, event):
    q = request.GET.get("q", "").strip()
    sellers = event.sellers.select_related("event").annotate(
        to_return=Count("listings", filter=Q(listings__status=ItemStatus.AVAILABLE)),
        returned=Count("listings", filter=Q(listings__status=ItemStatus.RETURNED)))
    if q:
        parsed = parse_seller_id(q)
        if parsed and parsed[0] == event.code and (match := sellers.filter(number=parsed[1]).first()):
            return redirect("consignment:pickup_screen", event_pk=event.pk, seller_pk=match.pk)
        sellers = sellers.filter(Q(name__icontains=q) | (Q(number=int(q)) if q.isdigit() else Q()))
    return render(request, "consignment/pickup_sellers.html",
                  {"event": event, "sellers": sellers, "q": q, "open": event.status == "pickup"})


def _pending_key(seller):
    return f"pickup:{seller.pk}"


@event_view
def pickup_screen(request, event, seller_pk):
    seller = get_object_or_404(Seller.objects.select_related("event"), pk=seller_pk, event=event)
    if event.status != "pickup":
        messages.error(request, "Pickup is only open while the event is in Pickup.")
        return redirect("consignment:pickup_sellers", event_pk=event.pk)
    key = _pending_key(seller)
    pending = request.session.get(key)
    pending_listing = (Listing.objects.select_related("accepted_slip").filter(
        pk=pending["listing"], seller=seller).first() if pending else None)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "scan":
                if pending_listing:
                    raise svc.PickupError("Answer the question about the current game first.")
                listing, kind, message = svc.scan_return(seller, request.user,
                                                         request.POST.get("code", ""))
                if kind:
                    request.session[key] = {"listing": listing.pk, "kind": kind,
                                            "message": message}
                else:
                    messages.success(request, message)
            elif action == "decide" and pending_listing:
                request.session.pop(key, None)
                svc.decide(pending_listing, request.user, pending["kind"],
                           request.POST.get("seller_takes") == "yes")
                messages.success(request, "Recorded.")
        except svc.PickupError as e:
            messages.error(request, str(e))
        return redirect("consignment:pickup_screen", event_pk=event.pk, seller_pk=seller.pk)

    listings = list(seller.listings.select_related("accepted_slip", "current_slip"))
    return render(request, "consignment/pickup_screen.html", {
        "event": event, "seller": seller, "pending": pending, "pending_listing": pending_listing,
        "to_return": [lst for lst in listings if lst.status == ItemStatus.AVAILABLE],
        "other": [lst for lst in listings if lst.status != ItemStatus.AVAILABLE],
        "frozen": hasattr(seller, "settlement"),
    })


@admin_required
@event_view
def unsold_scan(request, event):
    """Every remaining unsold game is scanned so each is accounted for."""
    if request.method == "POST":
        try:
            ok, message = svc.scan_unsold(event, request.user, request.POST.get("code", ""))
            (messages.success if ok else messages.error)(request, message)
        except svc.PickupError as e:
            messages.error(request, str(e))
        return redirect("consignment:unsold_scan", event_pk=event.pk)
    listings = (Listing.objects.filter(seller__event=event)
                .select_related("accepted_slip", "seller__event").order_by("seller__number"))
    return render(request, "consignment/unsold.html", {
        "event": event,
        "remaining": listings.filter(status=ItemStatus.AVAILABLE),
        "donated": listings.filter(status=ItemStatus.DONATED),
        "abandoned": listings.filter(status=ItemStatus.ABANDONED),
        "open": event.status == "pickup",
    })


@admin_required
@require_POST
def item_status(request, pk):
    listing = get_object_or_404(Listing.objects.select_related("seller__event"), pk=pk)
    try:
        svc.admin_set_status(listing, request.user, request.POST.get("status", ""),
                             request.POST.get("note", ""))
        messages.success(request, "Status changed.")
    except svc.PickupError as e:
        messages.error(request, str(e))
    return redirect("consignment:item_detail", pk=pk)


# ---------- Payouts ----------


@admin_required
@event_view
def payout_list(request, event):
    if request.method == "POST" and request.POST.get("action") == "automatic":
        n = payouts.record_automatic(event, request.user)
        messages.success(request, f"Recorded {n} automatic outcome{'s' if n != 1 else ''}.")
        return redirect("consignment:payouts", event_pk=event.pk)
    rows, totals = fees.event_figures(event)
    rows = [(f, payouts.blockers(f.seller) if not f.frozen else []) for f in rows
            if f.checked_in]
    return render(request, "consignment/payouts.html", {
        "event": event, "rows": rows, "totals": totals,
        "waiting": sum(1 for f, _ in rows if not f.frozen),
    })


class PayoutForm(forms.Form):
    outcome = forms.ChoiceField(widget=forms.RadioSelect)
    reference = forms.CharField(max_length=100, required=False,
                                label="Transaction or check number",
                                help_text="Required for Paid. Optional otherwise.")
    note = forms.CharField(max_length=500, required=False)

    def __init__(self, *args, outcomes, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["outcome"].choices = [(o.value, o.label) for o in outcomes]
        if len(outcomes) == 1:
            self.fields["outcome"].initial = outcomes[0].value


@admin_required
@event_view
def payout_seller(request, event, seller_pk):
    seller = get_object_or_404(Seller.objects.select_related("event", "linked_staff"),
                               pk=seller_pk, event=event)
    f = fees.figures(seller)
    outcomes = payouts.allowed_outcomes(f)
    form = PayoutForm(request.POST or None, outcomes=outcomes)
    if request.method == "POST" and form.is_valid():
        try:
            payouts.record(seller, request.user, form.cleaned_data["outcome"],
                           form.cleaned_data["reference"], form.cleaned_data["note"])
        except payouts.PayoutError as e:
            form.add_error(None, str(e))
        else:
            messages.success(request, f"Payout recorded for {seller.seller_id}. Their figures "
                                      "are frozen.")
            return redirect("consignment:payouts", event_pk=event.pk)
    return render(request, "consignment/payout_seller.html", {
        "event": event, "seller": seller, "f": f, "form": form,
        "blockers": [] if f.frozen else payouts.blockers(seller),
    })


# ---------- Reports ----------


def _cell(value, for_csv):
    if isinstance(value, Money):
        return f"{value / 100:.2f}" if for_csv else format_cents(value)
    return value


def csv_response(report: Report, filename):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(report.headings)
    for row in report.rows + ([report.totals] if report.totals else []):
        writer.writerow([_cell(v, True) for v in row])
    return response


def render_report(request, report, filename, back, **context):
    if request.GET.get("format") == "csv":
        return csv_response(report, filename)
    return render(request, "consignment/report.html", {
        "report": report, "back": back, **context,
        "rows": [[_cell(v, False) for v in row] for row in report.rows],
        "totals": [_cell(v, False) for v in report.totals] if report.totals else None,
        "numeric": [isinstance(v, int) for v in (report.rows[0] if report.rows else [])],
    })


@admin_required
@event_view
def reports_index(request, event):
    return render(request, "consignment/reports.html", {
        "event": event, "reports": [(slug, title, about) for slug, (title, about, _) in
                                    REPORTS.items()]})


@admin_required
@event_view
def report(request, event, slug):
    if slug not in REPORTS:
        raise Http404
    return render_report(request, REPORTS[slug][2](event), f"{event.code}-{slug}.csv",
                         back=("consignment:reports", event.pk), event=event)


# ---------- Seller statement ----------


def statement_report(seller):
    lines = {line.listing_id: line for line in fees.sold_lines(seller).select_related("window")}
    rows = []
    for lst in seller.listings.select_related("accepted_slip", "current_slip"):
        line = lines.get(lst.pk)
        slip = lst.accepted_slip or lst.current_slip
        rows.append([slip.item_id if slip else "", slip.name if slip else lst.name,
                     lst.get_status_display(), line.window.name if line else "",
                     Money(line.price_cents) if line else "",
                     Money(line.commission_cents) if line else ""])
    return Report(f"{seller.seller_id} statement",
                  ["Item ID", "Game", "Status", "Sold in window", "Price", "Commission"], rows)


@seller_view
def seller_statement(request, seller):
    f = fees.figures(seller)
    report = statement_report(seller)
    if request.GET.get("format") == "csv":
        return csv_response(report, f"{seller.seller_id}-statement.csv")
    return render(request, "consignment/seller_statement.html", {
        "seller": seller, "event": seller.event, "f": f,
        "rows": [[_cell(v, False) for v in row] for row in report.rows],
        "headings": report.headings,
    })
