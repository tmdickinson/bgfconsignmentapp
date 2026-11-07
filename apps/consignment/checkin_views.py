"""Check-in screens. The game waiting for a decision is kept in the volunteer's session.

Scanning the next barcode accepts the waiting game. Outdated, deactivated and
already-checked-in games wait for an explicit decision instead.
"""

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Seller
from apps.accounts.services import parse_seller_id
from apps.core.overrides import OverrideForm
from apps.core.permissions import event_view

from .models import ItemStatus, SlipVersion
from .services import checkin as svc


class DuplicateSlipForm(OverrideForm):
    correct_game = forms.ChoiceField(
        label="Which game has the correct slip?", widget=forms.RadioSelect,
        choices=[("first", "The game checked in earlier"), ("second", "The game just scanned")])


class CheckedInOverrideForm(OverrideForm):
    outcome = forms.ChoiceField(
        label="Outcome", widget=forms.RadioSelect,
        choices=[("keep", "Leave its status as it is"), ("error", "Set it to Error")])


def _seller(event, seller_pk, user):
    seller = get_object_or_404(Seller.objects.select_related("event", "linked_staff"),
                               pk=seller_pk, event=event)
    if svc.is_own_seller(user, seller):
        raise PermissionDenied("Staff can't check in their own games.")
    return seller


def _key(seller):
    return f"checkin:{seller.pk}"


def _pending(request, seller):
    data = request.session.get(_key(seller))
    if not data:
        return None, None
    slip = (SlipVersion.objects.select_related("listing__seller__event", "listing__current_slip")
            .prefetch_related("prices__window").filter(pk=data["slip"]).first())
    return (slip, data["kind"]) if slip else (None, None)


def _set_pending(request, seller, slip=None, kind=None):
    if slip is None:
        request.session.pop(_key(seller), None)
    else:
        request.session[_key(seller)] = {"slip": slip.pk, "kind": kind}


@event_view
def checkin_sellers(request, event):
    q = request.GET.get("q", "").strip()
    sellers = event.sellers.select_related("event", "linked_staff").annotate(
        listed=Count("listings", filter=Q(listings__status=ItemStatus.LISTED)),
        checked_in=Count("listings", filter=Q(listings__checked_in_at__isnull=False)),
    )
    if q:
        parsed = parse_seller_id(q)
        if parsed and parsed[0] == event.code:
            match = sellers.filter(number=parsed[1]).first()
            if match and not svc.is_own_seller(request.user, match):
                return redirect("consignment:checkin_screen", event_pk=event.pk, seller_pk=match.pk)
        filters = Q(name__icontains=q)
        if q.isdigit():
            filters |= Q(number=int(q))
        sellers = sellers.filter(filters)
    rows = [(s, svc.is_own_seller(request.user, s)) for s in sellers]
    return render(request, "consignment/checkin_sellers.html",
                  {"event": event, "rows": rows, "q": q, "open": event.status == "checkin"})


@event_view
def checkin_screen(request, event, seller_pk):
    seller = _seller(event, seller_pk, request.user)
    if event.status != "checkin":
        messages.error(request, "Check-in is only open while the event is in Check-in.")
        return redirect("consignment:checkin_sellers", event_pk=event.pk)
    report = svc.get_report(seller, request.user)
    slip, kind = _pending(request, seller)
    dup_form = DuplicateSlipForm(prefix="dup", action="checkin.duplicate_slip",
                                 requested_by=request.user, event=event)
    ovr_form = CheckedInOverrideForm(prefix="ovr", action="checkin.checked_in",
                                     requested_by=request.user, event=event)

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action in ("scan", "finish"):
                if slip and kind != svc.OK:
                    raise svc.CheckinError("Decide on the current game first.")
                if slip:
                    _set_pending(request, seller)  # Cleared first, so a failed accept can't stick.
                    svc.accept(report, request.user, slip)
                if action == "finish":
                    return redirect("consignment:checkin_report", event_pk=event.pk,
                                    seller_pk=seller.pk)
                new_slip, new_kind = svc.scan(report, request.user, request.POST.get("code", ""))
                _set_pending(request, seller, new_slip, new_kind)
            elif slip is None:
                pass  # The decision was already made, for example in another tab.
            elif action == "reject":
                svc.reject(report, request.user, slip, request.POST.get("reason", ""))
                _set_pending(request, seller)
                messages.warning(request, f"{slip.item_id} rejected. The game is Deactivated.")
            elif action == "confirm" and kind in svc.ALERT_KINDS:
                svc.accept(report, request.user, slip, confirmed=True)
                _set_pending(request, seller)
                messages.success(request, f"{slip.item_id} checked in on the slip's information.")
            elif action == "double" and kind == svc.CHECKED_IN:
                svc.double_scan(report, request.user, slip)
                _set_pending(request, seller)
                messages.info(request, "Recorded as a double scan. Nothing changed.")
            elif action == "duplicate" and kind == svc.CHECKED_IN:
                dup_form = DuplicateSlipForm(request.POST, prefix="dup",
                                             action="checkin.duplicate_slip",
                                             requested_by=request.user, event=event,
                                             target=slip.listing)
                if dup_form.is_valid():
                    svc.resolve_duplicate_slip(report, request.user, slip, dup_form.admin.account,
                                               dup_form.cleaned_data["admin_note"],
                                               dup_form.cleaned_data["correct_game"])
                    _set_pending(request, seller)
                    messages.success(request, "Resolved. Return the other game to the seller.")
            elif action == "override" and kind == svc.CHECKED_IN:
                ovr_form = CheckedInOverrideForm(request.POST, prefix="ovr",
                                                 action="checkin.checked_in",
                                                 requested_by=request.user, event=event,
                                                 target=slip.listing)
                if ovr_form.is_valid():
                    svc.resolve_checked_in(report, request.user, slip, ovr_form.admin.account,
                                           ovr_form.cleaned_data["admin_note"],
                                           ovr_form.cleaned_data["outcome"] == "error")
                    _set_pending(request, seller)
                    messages.success(request, "Override recorded.")
        except svc.CheckinError as e:
            messages.error(request, str(e))
        if not (dup_form.is_bound and dup_form.errors or ovr_form.is_bound and ovr_form.errors):
            return redirect("consignment:checkin_screen", event_pk=event.pk, seller_pk=seller.pk)
        # An override form had errors, so show it again with the waiting game.

    return render(request, "consignment/checkin_screen.html", {
        "event": event, "seller": seller, "slip": slip, "kind": kind,
        "comparison": svc.comparison(slip) if slip and kind in svc.ALERT_KINDS else None,
        "dup_form": dup_form, "ovr_form": ovr_form, **svc.summary(seller),
    })


@event_view
def checkin_report(request, event, seller_pk):
    seller = _seller(event, seller_pk, request.user)
    report = svc.get_report(seller, request.user)
    if request.method == "POST":
        svc.complete(report, request.user)
        messages.success(request, f"Check-in complete for {seller.seller_id}. The report is "
                                  "saved to the seller's account.")
        return redirect("consignment:checkin_sellers", event_pk=event.pk)
    return render(request, "consignment/checkin_report.html",
                  {"event": event, "seller": seller, **svc.summary(seller)})
