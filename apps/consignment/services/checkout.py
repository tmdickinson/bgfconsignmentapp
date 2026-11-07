"""Point-of-sale rules. See SPEC section 10.

Double sales are prevented in the database: one live cart line per game, one
active sale per volunteer and per register, and row locks on every change.
"""

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.audit import audit
from apps.core.ids import normalize_code, random_chars
from apps.core.money import percent_of
from apps.events.models import Event, Status

from ..models import (
    ACTIVE_STATES,
    Checkout,
    CheckoutKind,
    CheckoutLine,
    CheckoutState,
    FlagContext,
    ItemStatus,
    Listing,
    PricingMode,
    RegisterConflict,
    SaleOverride,
    SlipPrice,
    SlipVersion,
    SoldItemFlag,
)
from .checkin import parse_item_id
from .listings import set_status


class SaleError(Exception):
    pass


# ---------- Price windows ----------


def active_carts(event, kind=None):
    carts = Checkout.objects.filter(event=event, state__in=ACTIVE_STATES)
    return carts.filter(kind=kind) if kind else carts


def regular_checkout_closed_reason(event):
    """Why volunteers can't sell right now, or None if they can."""
    if event.status != Status.SALE:
        return f"Regular checkout runs only during Sale. The event is in {event.get_status_display()}."
    if event.active_window_id is None:
        return ("The last price window has closed." if event.last_window_closed_at
                else "No price window is active yet. An admin must choose one.")
    return None


def window_warning(event, now=None):
    """A register warning once the next window's scheduled start passes without a change."""
    if event.active_window_id is None:
        return None
    now = now or timezone.now()
    active = event.active_window
    following = event.windows.filter(position=active.position + 1).first()
    if following and following.scheduled_start and following.scheduled_start <= now:
        return (f"{following.name} was scheduled to start at "
                f"{timezone.localtime(following.scheduled_start):%-I:%M %p}. "
                f"Prices are still {active.name}'s until an admin changes the window.")
    if following is None and active.scheduled_end and active.scheduled_end <= now:
        return (f"{active.name} was scheduled to end at "
                f"{timezone.localtime(active.scheduled_end):%-I:%M %p}. "
                "Sales continue until an admin closes it.")
    return None


def _lock_event(event):
    return Event.objects.select_for_update().get(pk=event.pk)


def set_active_window(actor, event, window):
    """Blocked while any regular cart is open. Admin carts don't block."""
    with transaction.atomic():
        event = _lock_event(event)
        if event.status not in (Status.CHECKIN, Status.SALE):
            raise SaleError("The active window can be set during Check-in and Sale.")
        if window.event_id != event.pk:
            raise SaleError("That window belongs to another event.")
        if active_carts(event, CheckoutKind.REGULAR).exists():
            raise SaleError("Regular carts are open. They must finish or be canceled first.")
        now = timezone.now()
        old = event.active_window
        if old and old.pk != window.pk:
            old.closed_at = now
            old.save(update_fields=["closed_at"])
        window.opened_at, window.closed_at = now, None
        window.save(update_fields=["opened_at", "closed_at"])
        event.active_window, event.last_window_closed_at = window, None
        event.save(update_fields=["active_window", "last_window_closed_at"])
        audit(actor, "window.activated", event=event, target=window,
              previous=old.name if old else None, window=window.name)
    return event


def close_last_window(actor, event):
    """The last window ends only when an admin closes it."""
    with transaction.atomic():
        event = _lock_event(event)
        active = event.active_window
        if active is None or event.windows.filter(position__gt=active.position).exists():
            raise SaleError("Only the last price window can be closed.")
        if active_carts(event, CheckoutKind.REGULAR).exists():
            raise SaleError("Regular carts are open. They must finish or be canceled first.")
        now = timezone.now()
        active.closed_at = now
        active.save(update_fields=["closed_at"])
        event.active_window, event.last_window_closed_at = None, now
        event.save(update_fields=["active_window", "last_window_closed_at"])
        audit(actor, "window.closed_last", event=event, target=active, window=active.name)
    return event


# ---------- Carts ----------


def admin_cart_modes(event):
    """Pricing choices for admin carts in the event's current status (SPEC section 10)."""
    windows = list(event.windows.all())
    if event.status == Status.CHECKIN:
        return [(PricingMode.CHOSEN, windows[:1])]
    if event.status == Status.SALE:
        if event.last_window_closed_at:
            return [(PricingMode.PER_ITEM, windows)]
        return [(PricingMode.CHOSEN, windows)]
    if event.status == Status.PICKUP:
        return [(PricingMode.PER_ITEM, windows), (PricingMode.CHOSEN, windows)]
    return []


def active_checkout(account, event):
    return (Checkout.objects.select_related("register", "window", "owner")
            .filter(owner=account, event=event, state__in=ACTIVE_STATES,
                    reopened_by_override=False).first())


def _register_busy(register, owner):
    busy = (Checkout.objects.filter(register=register, state__in=ACTIVE_STATES,
                                    reopened_by_override=False)
            .exclude(owner=owner).select_related("owner").first())
    if busy:
        raise SaleError(f"Register {register.name} has an open sale by {busy.owner.display_name}. "
                        "It must finish first, or use another register.")


def _create(event, owner, register, **fields):
    _register_busy(register, owner)
    try:
        with transaction.atomic():
            return Checkout.objects.create(event=event, owner=owner, register=register, **fields)
    except IntegrityError as e:
        raise SaleError("You already have an open sale, or this register is busy.") from e


def start_regular(account, event, register):
    reason = regular_checkout_closed_reason(event)
    if reason:
        raise SaleError(reason)
    checkout = _create(event, account, register, window=event.active_window)
    audit(account, "checkout.started", event=event, target=checkout, register=register.name)
    return checkout


def open_admin_cart(admin, event, register, mode, window=None):
    modes = dict(admin_cart_modes(event))
    if mode not in modes:
        raise SaleError("Admin carts can't use that pricing now.")
    if mode == PricingMode.CHOSEN and window not in modes[mode]:
        raise SaleError("Choose a price window for the whole sale.")
    checkout = _create(event, admin, register, kind=CheckoutKind.ADMIN, opened_by_admin=admin,
                       pricing_mode=mode, window=window if mode == PricingMode.CHOSEN else None)
    audit(admin, "checkout.admin_cart_opened", event=event, target=checkout, mode=mode,
          window=window.name if window else None, register=register.name)
    return checkout


def move_to_register(checkout, register):
    """Signing in on another register moves the open sale there."""
    if checkout.register_id == register.pk:
        return checkout
    _register_busy(register, checkout.owner)
    old = checkout.register
    checkout.register, checkout.register_changed_at = register, timezone.now()
    try:
        with transaction.atomic():
            checkout.save(update_fields=["register", "register_changed_at"])
    except IntegrityError as e:
        raise SaleError(f"Register {register.name} is busy.") from e
    audit(checkout.owner, "checkout.moved", event=checkout.event, target=checkout,
          old=old.name, new=register.name)
    return checkout


def _lock_checkout(checkout):
    return (Checkout.objects.select_for_update(of=("self",))
            .select_related("event", "window", "register", "owner").get(pk=checkout.pk))


def _require_still_allowed(checkout):
    event = checkout.event
    if checkout.kind == CheckoutKind.REGULAR:
        reason = regular_checkout_closed_reason(event)
        if reason:
            raise SaleError(reason)
    elif not admin_cart_modes(event):
        raise SaleError("Admin carts aren't available in this status.")


def open_conflicts(checkout):
    return RegisterConflict.objects.filter(
        Q(holding_checkout=checkout) | Q(scanning_checkout=checkout), resolved_at__isnull=True
    ).select_related("listing", "holding_checkout__owner", "holding_checkout__register",
                     "scanning_checkout__owner", "scanning_checkout__register")


@dataclass
class ScanResult:
    ok: bool
    message: str
    level: str = "error"  # error, warning (Sold), conflict


def _holder_text(line):
    c = line.checkout
    return f"{c.owner.display_name}'s cart at register {c.register.name}"


def scan(checkout, actor, code, window=None):
    """Add a scanned game to the cart.

    A rejected scan never changes the game or the cart, except for a two-register
    conflict, which is recorded so both registers are blocked.
    """
    event = checkout.event
    code = normalize_code(code)
    parsed = parse_item_id(event, code)
    if parsed is None:
        return ScanResult(False, f"{code or 'That'} isn't an Item ID.")
    if parsed[0] != event.code:
        return ScanResult(False, f"{parsed[1]} belongs to another event.")
    slip = SlipVersion.objects.filter(item_id=parsed[1]).first()
    if slip is None:
        return ScanResult(False, f"{parsed[1]} wasn't found.")

    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state != CheckoutState.OPEN:
            return ScanResult(False, "This sale is locked for payment. Finish or cancel it first.")
        _require_still_allowed(checkout)
        listing = (Listing.objects.select_for_update(of=("self",))
                   .select_related("accepted_slip").get(pk=slip.listing_id))
        name = listing.accepted_slip.name if listing.accepted_slip else listing.name
        if listing.accepted_slip_id and slip.pk != listing.accepted_slip_id:
            return ScanResult(False, f"{slip.item_id} isn't the slip accepted at check-in "
                                     f"({listing.accepted_slip.item_id}). Check the game.")
        own = checkout.lines.filter(listing=listing).first()
        if own and own.live:
            return ScanResult(False, f"{name} is already in this cart.")

        if listing.status == ItemStatus.SOLD:
            flag, created = SoldItemFlag.objects.get_or_create(
                listing=listing, resolved_at__isnull=True,
                defaults={"event": event, "context": FlagContext.CHECKOUT, "raised_by": actor})
            if created:
                audit(actor, "sold_flag.raised", event=event, target=listing, context="checkout")
            return ScanResult(False, f"{name} ({slip.item_id}) is already SOLD. Set the game aside, "
                                     "not back on the tables. An admin has been alerted.", "warning")

        if listing.status == ItemStatus.IN_CHECKOUT:
            holder = (CheckoutLine.objects.select_related("checkout__owner", "checkout__register")
                      .filter(listing=listing, live=True).exclude(checkout=checkout).first())
            if holder:
                conflict, created = RegisterConflict.objects.get_or_create(
                    listing=listing, scanning_checkout=checkout, resolved_at__isnull=True,
                    defaults={"event": event, "holding_checkout": holder.checkout,
                              "raised_by": actor})
                if created:
                    audit(actor, "conflict.raised", event=event, target=listing,
                          holding=holder.checkout.pk, scanning=checkout.pk)
                return ScanResult(False, f"{name} is also in {_holder_text(holder)}. Neither "
                                         "register can take payment until an admin resolves it.",
                                  "conflict")
            other = (CheckoutLine.objects.select_related("checkout__owner", "checkout__register")
                     .filter(listing=listing, checkout__state__in=ACTIVE_STATES,
                             released_at__isnull=True).exclude(checkout=checkout).first())
            if other:
                return ScanResult(False, f"{name} was removed from {_holder_text(other)} and is "
                                         "held until that sale is resolved.")
            if own is None:
                return ScanResult(False, f"{name} is held in another cart.")
            # Removed from this cart by mistake: scanned back in.
            own.live, own.removed_at, own.removed_by = True, None, None
            own.save(update_fields=["live", "removed_at", "removed_by"])
            return ScanResult(True, f"{name} added back.")

        if listing.status != ItemStatus.AVAILABLE:
            return ScanResult(False, f"{name} can't be sold. It is {listing.get_status_display()}.")

        if checkout.pricing_mode != PricingMode.PER_ITEM:
            window = checkout.window
        if window is None:
            return ScanResult(False, "Choose the price window for this game.")
        price = SlipPrice.objects.get(slip=listing.accepted_slip, window=window).price_cents
        commission = percent_of(price, event.commission_percent)
        if own:  # Released earlier by an admin, now back in this cart.
            own.live, own.removed_at, own.removed_by = True, None, None
            own.released_at = own.released_by_admin = None
            own.window, own.price_cents, own.commission_cents = window, price, commission
            own.save()
        else:
            CheckoutLine.objects.create(checkout=checkout, listing=listing,
                                        slip=listing.accepted_slip, window=window,
                                        price_cents=price, commission_cents=commission)
        set_status(listing, ItemStatus.IN_CHECKOUT, actor, f"Scanned into cart {checkout.pk}")
        listing.save(update_fields=["status"])
    return ScanResult(True, f"Added {name}.")


def remove_line(checkout, actor, line_id):
    """Removed games stay reserved until the sale is resolved, then go to restock."""
    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state != CheckoutState.OPEN:
            raise SaleError("This sale is locked for payment.")
        line = checkout.lines.select_related("slip").filter(pk=line_id, live=True).first()
        if line is None:
            raise SaleError("That game isn't in the cart.")
        line.live, line.removed_at, line.removed_by = False, timezone.now(), actor
        line.save(update_fields=["live", "removed_at", "removed_by"])
        audit(actor, "checkout.line_removed", event=checkout.event, target=checkout,
              item_id=line.slip.item_id)
    return line


def live_lines(checkout):
    return checkout.lines.filter(live=True).select_related("slip", "window", "listing")


def restock_lines(checkout):
    """Games scanned and then removed, which go back on the tables."""
    return checkout.lines.filter(removed_at__isnull=False, released_at__isnull=True,
                                 live=False).select_related("slip")


def _new_sale_id(checkout):
    staff = (checkout.opened_by_admin if checkout.is_admin_cart else checkout.owner).staff
    source = staff.admin_id if checkout.is_admin_cart else staff.staff_id
    while True:
        sale_id = f"{source[-4:]}-{random_chars(4)}"
        if not Checkout.objects.filter(event=checkout.event, sale_id=sale_id).exists():
            return sale_id


def _require_no_conflicts(checkout):
    if open_conflicts(checkout).exists():
        raise SaleError("A two-register conflict must be resolved by an admin first.")


def proceed_to_payment(checkout, actor):
    """Create the Sale ID and lock the contents and total."""
    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state != CheckoutState.OPEN:
            raise SaleError("This sale isn't open.")
        _require_still_allowed(checkout)
        _require_no_conflicts(checkout)
        lines = list(live_lines(checkout))
        if not lines:
            raise SaleError("The cart is empty.")
        checkout.sale_id = _new_sale_id(checkout)
        checkout.total_cents = sum(line.price_cents for line in lines)
        checkout.state, checkout.proceeded_at = CheckoutState.AWAITING_PAYMENT, timezone.now()
        checkout.save(update_fields=["sale_id", "total_cents", "state", "proceeded_at"])
        audit(actor, "checkout.proceeded", event=checkout.event, target=checkout,
              sale_id=checkout.sale_id, total_cents=checkout.total_cents, items=len(lines))
    return checkout


def _release_listing(listing, actor, note):
    """Put a held game back on sale, unless an admin has since changed its status."""
    if listing.status == ItemStatus.IN_CHECKOUT and not listing.checkout_lines.filter(
            live=True).exists():
        set_status(listing, ItemStatus.AVAILABLE, actor, note)
        listing.save(update_fields=["status"])


def payment_received(checkout, actor):
    """The sale is complete and locked. Bought games become Sold and removed ones Available."""
    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state != CheckoutState.AWAITING_PAYMENT:
            raise SaleError("This sale isn't waiting for payment.")
        _require_no_conflicts(checkout)
        lines = list(live_lines(checkout))
        listings = {lst.pk: lst for lst in Listing.objects.select_for_update(of=("self",))
                    .filter(pk__in=[line.listing_id for line in lines])}
        changed = [lst for lst in listings.values() if lst.status != ItemStatus.IN_CHECKOUT]
        if changed:
            raise SaleError(f"{changed[0].name} is now {changed[0].get_status_display()}. "
                            "Cancel the sale and start again.")
        for listing in listings.values():
            set_status(listing, ItemStatus.SOLD, actor, f"Sale {checkout.sale_id}")
            listing.save(update_fields=["status"])
        for line in restock_lines(checkout):
            _release_listing(Listing.objects.select_for_update(of=("self",)).get(
                pk=line.listing_id), actor, f"Restocked from sale {checkout.sale_id}")
        checkout.state, checkout.paid_at, checkout.paid_by = (
            CheckoutState.COMPLETE, timezone.now(), actor)
        checkout.save(update_fields=["state", "paid_at", "paid_by"])
        audit(actor, "checkout.completed", event=checkout.event, target=checkout,
              sale_id=checkout.sale_id, total_cents=checkout.total_cents)
    return checkout


def cancel(checkout, actor, note=""):
    """Every game in the sale goes back on sale, except any an admin has since changed."""
    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state not in ACTIVE_STATES:
            raise SaleError("This sale is already finished.")
        lines = list(checkout.lines.filter(released_at__isnull=True))
        checkout.lines.filter(live=True).update(live=False)
        for line in lines:
            _release_listing(Listing.objects.select_for_update(of=("self",)).get(
                pk=line.listing_id), actor,
                f"Sale {checkout.sale_id} canceled" if checkout.sale_id else "Cart canceled")
        checkout.state, checkout.canceled_at, checkout.canceled_by = (
            CheckoutState.CANCELED, timezone.now(), actor)
        checkout.save(update_fields=["state", "canceled_at", "canceled_by"])
        open_conflicts(checkout).update(resolved_at=timezone.now(), resolved_by=actor,
                                        note="A sale in the conflict was canceled.")
        audit(actor, "checkout.canceled", event=checkout.event, target=checkout,
              sale_id=checkout.sale_id, note=note)
    return checkout


# ---------- Admin actions ----------


def resolve_conflict(conflict, admin, actor, keep_scanner, note):
    """An admin decides which sale keeps the game.

    Taking it from a sale that already has a Sale ID cancels that whole sale.
    Returns a message for the volunteer.
    """
    with transaction.atomic():
        conflict = RegisterConflict.objects.select_for_update().get(pk=conflict.pk)
        if conflict.resolved_at:
            raise SaleError("This conflict was already resolved.")
        holder, scanner = conflict.holding_checkout, conflict.scanning_checkout
        message = "The game stays in the first sale."
        kept = holder
        if keep_scanner:
            kept = scanner
            if holder.state == CheckoutState.AWAITING_PAYMENT:
                cancel(holder, admin, f"Canceled by conflict resolution: {note}")
                message = f"Sale {holder.sale_id} was canceled."
            else:
                line = holder.lines.filter(listing=conflict.listing, live=True).first()
                if line:
                    _release_line(line, admin)
                message = "The game was taken from the first cart."
        conflict.refresh_from_db()
        if conflict.resolved_at is None:
            conflict.resolved_at, conflict.resolved_by = timezone.now(), admin
        conflict.kept_by_checkout, conflict.note = kept, note
        conflict.save()
        audit(admin, "conflict.resolved", event=conflict.event, target=conflict.listing,
              kept=kept.pk, note=note, volunteer=actor.pk)
    if keep_scanner and scanner.state == CheckoutState.OPEN:
        message += " Scan the game again to add it to this sale."
    return message


def _release_line(line, admin):
    line.live, line.released_at, line.released_by_admin = False, timezone.now(), admin
    line.save(update_fields=["live", "released_at", "released_by_admin"])
    _release_listing(Listing.objects.select_for_update(of=("self",)).get(pk=line.listing_id),
                     admin, "Released from a cart by an admin")


def admin_release(line, admin):
    """Release a game held in an open cart. A sale with a Sale ID is canceled as a whole."""
    with transaction.atomic():
        checkout = _lock_checkout(line.checkout)
        if checkout.state != CheckoutState.OPEN:
            raise SaleError("This sale has a Sale ID. Cancel the whole sale instead.")
        line = CheckoutLine.objects.select_related("slip").get(pk=line.pk)
        if line.released_at or not (line.live or line.removed_at):
            raise SaleError("That game isn't held in this cart.")
        _release_line(line, admin)
        RegisterConflict.objects.filter(listing=line.listing, holding_checkout=checkout,
                                        resolved_at__isnull=True).update(
            resolved_at=timezone.now(), resolved_by=admin, note="Released by an admin")
        audit(admin, "checkout.line_released", event=checkout.event, target=checkout,
              item_id=line.slip.item_id)


def resolve_sold_flag(flag, admin, note):
    """The admin takes possession of the game, holding it for the buyer if the sale was valid."""
    with transaction.atomic():
        flag = SoldItemFlag.objects.select_for_update().get(pk=flag.pk)
        if flag.resolved_at:
            raise SaleError("This flag is already closed.")
        flag.resolved_at, flag.resolved_by, flag.note = timezone.now(), admin, note
        flag.save(update_fields=["resolved_at", "resolved_by", "note"])
        audit(admin, "sold_flag.resolved", event=flag.event, target=flag.listing, note=note)


def payout_recorded_for(checkout):
    """Whether any seller in the sale has a recorded payout. Payouts arrive in Phase 5."""
    return False


def sale_override(checkout, admin, actor, note):
    """Return a completed sale to the payment step, when payment couldn't be collected."""
    with transaction.atomic():
        checkout = _lock_checkout(checkout)
        if checkout.state != CheckoutState.COMPLETE:
            raise SaleError("Only completed sales can be returned to payment.")
        if payout_recorded_for(checkout):
            raise SaleError("A payout has been recorded for a seller in this sale.")
        for line in live_lines(checkout):
            listing = Listing.objects.select_for_update(of=("self",)).get(pk=line.listing_id)
            if listing.status == ItemStatus.SOLD:
                set_status(listing, ItemStatus.IN_CHECKOUT, actor,
                           f"Sale {checkout.sale_id} returned to payment", admin)
                listing.save(update_fields=["status"])
        checkout.state, checkout.reopened_by_override = CheckoutState.AWAITING_PAYMENT, True
        checkout.paid_at = checkout.paid_by = None
        checkout.save(update_fields=["state", "reopened_by_override", "paid_at", "paid_by"])
        SaleOverride.objects.create(checkout=checkout, admin=admin, requested_by=actor, note=note)
        audit(admin, "checkout.override", event=checkout.event, target=checkout,
              sale_id=checkout.sale_id, note=note)
    return checkout
