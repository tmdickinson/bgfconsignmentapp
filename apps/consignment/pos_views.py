"""Register screens. A laptop remembers its register in a signed cookie.

A sale belongs to the volunteer. Signing in on another register moves an open
sale there, and the old register only shows that it moved.
"""

from django import forms
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q, Sum
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.overrides import OverrideForm
from apps.core.permissions import admin_required, event_view

from .models import (
    ACTIVE_STATES,
    Checkout,
    CheckoutState,
    PricingMode,
    Register,
    RegisterConflict,
    SoldItemFlag,
)
from .services import checkout as svc

COOKIE = "bgf_register"
COOKIE_SALT = "register"
YEAR = 60 * 60 * 24 * 365


def laptop_register(request, event):
    pk = request.get_signed_cookie(COOKIE, default=None, salt=COOKIE_SALT)
    return Register.objects.filter(pk=pk, event=event).first() if pk else None


def _session_key(event):
    return f"pos:{event.pk}:register"


def _pos(event):
    return redirect("consignment:pos", event_pk=event.pk)


@event_view
def choose_register(request, event):
    if request.method == "POST":
        if request.user.is_admin and request.POST.get("new_name", "").strip():
            name = request.POST["new_name"].strip()[:40]
            try:
                register = Register.objects.create(event=event, name=name)
            except IntegrityError:
                messages.error(request, f"There's already a register named {name}.")
                return redirect("consignment:choose_register", event_pk=event.pk)
            audit(request.user, "register.created", event=event, target=register, name=name)
        else:
            register = get_object_or_404(Register, pk=request.POST.get("register"), event=event)
        response = _pos(event)
        response.set_signed_cookie(COOKIE, register.pk, salt=COOKIE_SALT, max_age=YEAR,
                                   httponly=True, samesite="Lax", secure=request.is_secure())
        request.session[_session_key(event)] = register.pk
        checkout = svc.active_checkout(request.user, event)
        if checkout:
            try:
                svc.move_to_register(checkout, register)
            except svc.SaleError as e:
                messages.error(request, str(e))
        return response
    registers = event.registers.annotate(open=Count("checkouts", filter=Q(
        checkouts__state__in=ACTIVE_STATES, checkouts__reopened_by_override=False)))
    return render(request, "consignment/pos_register.html", {
        "event": event, "registers": registers, "current": laptop_register(request, event)})


def _claim(request, event, register, checkout):
    """The first register visit in a sign-in session moves an open sale to this register."""
    key = _session_key(event)
    if request.session.get(key) is None:
        request.session[key] = register.pk
        if checkout and checkout.register_id != register.pk:
            try:
                svc.move_to_register(checkout, register)
                messages.info(request, "Your open sale moved to this register.")
            except svc.SaleError as e:
                messages.error(request, str(e))


def _context(request, event, register, checkout):
    lines = list(svc.live_lines(checkout)) if checkout else []
    return {
        "event": event, "register": register, "checkout": checkout, "lines": lines,
        "removed": list(svc.restock_lines(checkout)) if checkout else [],
        "count": len(lines), "total": sum(line.price_cents for line in lines),
        "closed_reason": svc.regular_checkout_closed_reason(event),
        "warning": svc.window_warning(event),
        "conflicts": list(svc.open_conflicts(checkout)) if checkout else [],
        "windows": event.windows.all(),
        "chosen_window": request.session.get(f"pos:{event.pk}:window"),
    }


@event_view
def pos(request, event):
    register = laptop_register(request, event)
    if register is None:
        return redirect("consignment:choose_register", event_pk=event.pk)
    checkout = svc.active_checkout(request.user, event)
    _claim(request, event, register, checkout)
    if checkout:
        checkout.refresh_from_db()
    if checkout and checkout.register_id != register.pk:
        return render(request, "consignment/pos_moved.html",
                      {"event": event, "register": register, "checkout": checkout})

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "scan":
                if checkout is None:
                    checkout = svc.start_regular(request.user, event, register)
                window = None
                if checkout.pricing_mode == PricingMode.PER_ITEM:
                    window = event.windows.filter(pk=request.POST.get("window")).first()
                    if window:
                        request.session[f"pos:{event.pk}:window"] = window.pk
                result = svc.scan(checkout, request.user, request.POST.get("code", ""), window)
                level = "success" if result.ok else result.level
                getattr(messages, {"success": "success", "warning": "warning"}.get(
                    level, "error"))(request, result.message)
                if (not result.ok and result.level != "conflict" and not checkout.is_admin_cart
                        and not checkout.lines.exists()):
                    # A first scan that fails leaves no empty regular cart open. A conflict
                    # keeps its cart, which the conflict refers to.
                    svc.cancel(checkout, request.user, "Empty cart")
            elif checkout and action == "remove":
                line = svc.remove_line(checkout, request.user, request.POST.get("line"))
                messages.info(request, f"Removed {line.slip.name}. Put it on the restock table "
                                       "when the sale finishes.")
            elif checkout and action == "proceed":
                svc.proceed_to_payment(checkout, request.user)
        except svc.SaleError as e:
            messages.error(request, str(e))
        return _pos(event)

    return render(request, "consignment/pos.html", _context(request, event, register, checkout))


@event_view
def pos_confirm(request, event, action):
    """Payment Received and Cancel Sale each need a confirmation."""
    if action not in ("paid", "cancel"):
        return HttpResponseBadRequest()
    register = laptop_register(request, event)
    checkout = svc.active_checkout(request.user, event)
    if checkout is None or register is None or checkout.register_id != register.pk:
        return _pos(event)
    if request.method == "POST":
        try:
            if action == "paid":
                svc.payment_received(checkout, request.user)
                messages.success(request, f"Sale {checkout.sale_id} complete.")
            else:
                svc.cancel(checkout, request.user)
                messages.warning(request, f"Sale {checkout.sale_id or ''} canceled. "
                                          "Put every game from it back on the tables.")
        except svc.SaleError as e:
            messages.error(request, str(e))
        return _pos(event)
    return render(request, "consignment/pos_confirm.html", {
        **_context(request, event, register, checkout), "action": action})


@event_view
def pos_status(request, event):
    """Polled by the register every few seconds: conflicts, a moved sale and window warnings."""
    register = laptop_register(request, event)
    checkout = svc.active_checkout(request.user, event)
    event.refresh_from_db()
    return render(request, "consignment/_pos_status.html", {
        "event": event, "register": register, "checkout": checkout,
        "moved": bool(checkout and register and checkout.register_id != register.pk),
        "conflicts": list(svc.open_conflicts(checkout)) if checkout else [],
        "warning": svc.window_warning(event),
        "closed_reason": svc.regular_checkout_closed_reason(event),
    })


class ConflictForm(OverrideForm):
    keep = forms.ChoiceField(widget=forms.RadioSelect, label="Which sale keeps the game?",
                             choices=[("holder", "The sale that had it first"),
                                      ("scanner", "The sale that scanned it second")])


@event_view
def alerts(request, event):
    """Open conflicts and Sold flags. An admin resolves them here with badge and PIN."""
    conflicts = RegisterConflict.objects.filter(event=event, resolved_at__isnull=True).select_related(
        "listing__accepted_slip", "holding_checkout__owner", "holding_checkout__register",
        "scanning_checkout__owner", "scanning_checkout__register")
    flags = SoldItemFlag.objects.filter(event=event, resolved_at__isnull=True).select_related(
        "listing__accepted_slip", "listing__seller__event", "raised_by")
    forms_by_key = {}
    for c in conflicts:
        forms_by_key[f"c{c.pk}"] = ConflictForm(
            request.POST if request.POST.get("key") == f"c{c.pk}" else None, prefix=f"c{c.pk}",
            action="conflict.resolve", requested_by=request.user, event=event, target=c.listing)
    for f in flags:
        forms_by_key[f"f{f.pk}"] = OverrideForm(
            request.POST if request.POST.get("key") == f"f{f.pk}" else None, prefix=f"f{f.pk}",
            action="sold_flag.resolve", requested_by=request.user, event=event, target=f.listing)
    if request.method == "POST":
        key = request.POST.get("key", "")
        form = forms_by_key.get(key)
        if form is None:
            messages.info(request, "That alert was already resolved.")
            return redirect("consignment:alerts", event_pk=event.pk)
        if form.is_valid():
            admin, note = form.admin.account, form.cleaned_data["admin_note"]
            try:
                if key.startswith("c"):
                    conflict = conflicts.get(pk=key[1:])
                    messages.success(request, svc.resolve_conflict(
                        conflict, admin, request.user, form.cleaned_data["keep"] == "scanner", note))
                else:
                    svc.resolve_sold_flag(flags.get(pk=key[1:]), admin, note)
                    messages.success(request, "Flag closed. The admin has the game.")
            except svc.SaleError as e:
                messages.error(request, str(e))
            return redirect("consignment:alerts", event_pk=event.pk)
    return render(request, "consignment/alerts.html", {
        "event": event,
        "conflicts": [(c, forms_by_key[f"c{c.pk}"]) for c in conflicts],
        "flags": [(f, forms_by_key[f"f{f.pk}"]) for f in flags],
    })


# ---------- Admin screens ----------


@admin_required
@event_view
def sales_control(request, event):
    """Price windows, open carts, admin carts and registers."""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "activate":
                window = get_object_or_404(event.windows, pk=request.POST.get("window"))
                svc.set_active_window(request.user, event, window)
                messages.success(request, f"{window.name} is now the active price window.")
            elif action == "close_last":
                svc.close_last_window(request.user, event)
                messages.success(request, "The last price window is closed. Regular checkout "
                                          "has ended.")
            elif action == "cancel_cart":
                cart = get_object_or_404(Checkout, pk=request.POST.get("checkout"), event=event)
                svc.cancel(cart, request.user, "Canceled by an admin as stuck")
                messages.success(request, f"Canceled {cart.owner.display_name}'s cart.")
            elif action == "admin_cart":
                register = laptop_register(request, event)
                if register is None:
                    raise svc.SaleError("Choose this laptop's register first.")
                mode = request.POST.get("mode")
                window = event.windows.filter(pk=request.POST.get("window")).first()
                svc.open_admin_cart(request.user, event, register, mode, window)
                return _pos(event)
        except svc.SaleError as e:
            messages.error(request, str(e))
        return redirect("consignment:sales_control", event_pk=event.pk)
    event.refresh_from_db()
    carts = svc.active_carts(event).select_related("owner", "register", "window").annotate(
        items=Count("lines", filter=Q(lines__live=True)))
    return render(request, "consignment/sales_control.html", {
        "event": event, "windows": event.windows.all(), "carts": carts,
        "regular_open": any(c.kind == "regular" for c in carts),
        "warning": svc.window_warning(event), "modes": svc.admin_cart_modes(event),
        "register": laptop_register(request, event), "registers": event.registers.all(),
        "is_last": event.active_window and not event.windows.filter(
            position__gt=event.active_window.position).exists(),
    })


@admin_required
@event_view
def sales_list(request, event):
    q = request.GET.get("q", "").strip().upper()
    sales = (Checkout.objects.filter(event=event, sale_id__isnull=False)
             .select_related("owner", "register", "opened_by_admin")
             .annotate(items=Count("lines", filter=Q(lines__live=True))))
    if q:
        sales = sales.filter(sale_id__icontains=q)
    admin_only = request.GET.get("admin") == "1"
    if admin_only:
        sales = sales.filter(kind="admin")
    totals = sales.filter(state=CheckoutState.COMPLETE).aggregate(n=Count("id"),
                                                                    total=Sum("total_cents"))
    return render(request, "consignment/sales_list.html", {
        "event": event, "sales": sales[:500], "q": q, "admin_only": admin_only, "totals": totals})


@admin_required
@event_view
def sale_detail(request, event, pk):
    sale = get_object_or_404(Checkout.objects.select_related("owner", "register", "window",
                                                             "opened_by_admin", "paid_by",
                                                             "canceled_by"), pk=pk, event=event)
    override_form = OverrideForm(request.POST if request.POST.get("action") == "override" else None,
                                 prefix="ovr", action="checkout.override",
                                 requested_by=request.user, event=event, target=sale)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "override" and override_form.is_valid():
                svc.sale_override(sale, override_form.admin.account, request.user,
                                  override_form.cleaned_data["admin_note"])
                messages.warning(request, f"Sale {sale.sale_id} is back at the payment step.")
                return redirect("consignment:sale_detail", event_pk=event.pk, pk=sale.pk)
            if action in ("paid", "cancel") and sale.reopened_by_override:
                if request.POST.get("confirm") != "yes":
                    raise svc.SaleError("Tick the box to confirm.")
                if action == "paid":
                    svc.payment_received(sale, request.user)
                else:
                    svc.cancel(sale, request.user, "Canceled after an override")
                return redirect("consignment:sale_detail", event_pk=event.pk, pk=sale.pk)
        except svc.SaleError as e:
            messages.error(request, str(e))
    return render(request, "consignment/sale_detail.html", {
        "event": event, "sale": sale,
        "lines": sale.lines.select_related("slip", "window", "listing__seller__event",
                                           "removed_by", "released_by_admin"),
        "overrides": sale.overrides.select_related("admin", "requested_by"),
        "override_form": override_form,
    })


@admin_required
@require_POST
def release_item(request, pk):
    """Release a game held in an open cart, from the game's page."""
    from .models import CheckoutLine

    line = get_object_or_404(CheckoutLine.objects.select_related("checkout", "listing"),
                             listing_id=pk, checkout__state__in=ACTIVE_STATES,
                             released_at__isnull=True)
    try:
        svc.admin_release(line, request.user)
        messages.success(request, "Released. The game is Available again.")
    except svc.SaleError as e:
        messages.error(request, str(e))
    return redirect("consignment:item_detail", pk=pk)
