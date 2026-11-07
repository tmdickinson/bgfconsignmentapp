import datetime

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.consignment.models import (
    Checkout,
    CheckoutLine,
    CheckoutState,
    ItemStatus,
    PricingMode,
    RegisterConflict,
    SoldItemFlag,
)
from apps.consignment.services import checkout as svc
from apps.events.services import EventError, advance_status

from .conftest import cart, code, status

# ---------- Windows ----------


@pytest.mark.django_db
def test_window_change_blocked_while_regular_cart_open(shop):
    c = cart(shop)
    saturday = shop.event.windows.get(position=2)
    with pytest.raises(svc.SaleError, match="Regular carts are open"):
        svc.set_active_window(shop.admin, shop.event, saturday)
    svc.cancel(c, shop.vol)
    svc.set_active_window(shop.admin, shop.event, saturday)
    shop.event.refresh_from_db()
    assert shop.event.active_window == saturday


@pytest.mark.django_db
def test_admin_carts_do_not_block_window_changes(shop):
    svc.open_admin_cart(shop.admin, shop.event, shop.r2, PricingMode.CHOSEN,
                        shop.event.windows.first())
    svc.set_active_window(shop.admin, shop.event, shop.event.windows.get(position=2))


@pytest.mark.django_db
def test_only_the_last_window_can_be_closed_and_checkout_then_ends(shop):
    with pytest.raises(svc.SaleError, match="last"):
        svc.close_last_window(shop.admin, shop.event)
    svc.set_active_window(shop.admin, shop.event, shop.event.windows.get(position=3))
    svc.close_last_window(shop.admin, shop.event)
    shop.event.refresh_from_db()
    assert shop.event.active_window is None
    assert "last price window has closed" in svc.regular_checkout_closed_reason(shop.event)
    with pytest.raises(svc.SaleError):
        cart(shop)


@pytest.mark.django_db
def test_warning_when_next_window_start_passes(shop):
    saturday = shop.event.windows.get(position=2)
    saturday.scheduled_start = timezone.now() + datetime.timedelta(hours=1)
    saturday.save()
    assert svc.window_warning(shop.event) is None
    saturday.scheduled_start = timezone.now() - datetime.timedelta(minutes=1)
    saturday.save()
    assert "Saturday was scheduled to start" in svc.window_warning(shop.event)
    shop.event.refresh_from_db()
    assert shop.event.active_window.position == 1  # Never changed by the clock


@pytest.mark.django_db
@pytest.mark.parametrize("status_", ["checkin", "pickup"])
def test_regular_checkout_only_during_sale(shop, status_):
    shop.event.status = status_
    shop.event.save()
    with pytest.raises(svc.SaleError, match="only during Sale"):
        cart(shop)


# ---------- Building a sale ----------


@pytest.mark.django_db
def test_scan_reserves_game_at_active_window_price(shop):
    c = cart(shop)
    assert svc.scan(c, shop.vol, code(shop.games[0])).ok
    line = c.lines.get()
    assert status(shop.games[0]) == ItemStatus.IN_CHECKOUT
    assert line.price_cents == 3000 and line.commission_cents == 300
    assert line.slip == shop.games[0].accepted_slip


@pytest.mark.django_db
def test_price_comes_from_the_accepted_slip_not_later_edits(shop):
    game = shop.games[0]
    game.name = "Changed later"
    game.save()
    game.prices.update(dollars=99)
    c = cart(shop)
    svc.scan(c, shop.vol, code(game))
    assert c.lines.get().price_cents == 3000


@pytest.mark.django_db
def test_scan_errors_change_nothing(shop, seller, make_listing):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    assert "already in this cart" in svc.scan(c, shop.vol, code(shop.games[0])).message
    assert "another event" in svc.scan(c, shop.vol, "S27B-001-0001").message
    assert "wasn't found" in svc.scan(c, shop.vol, "F26A-001-0099").message
    shop.games[1].status = ItemStatus.MISSING
    shop.games[1].save()
    result = svc.scan(c, shop.vol, code(shop.games[1]))
    assert not result.ok and "Missing" in result.message
    assert c.lines.count() == 1 and status(shop.games[1]) == ItemStatus.MISSING


@pytest.mark.django_db
def test_only_the_accepted_slip_version_scans(shop, seller):
    from apps.consignment.models import SlipVersion

    game = shop.games[0]
    stray = SlipVersion.objects.create(listing=game, item_id="F26A-001-0050", sequence=50,
                                       name="x", publisher="x", description="x",
                                       condition="good", slip_size="large",
                                       donate_if_unsold=False)
    result = svc.scan(cart(shop), shop.vol, stray.item_id)
    assert not result.ok and "accepted at check-in" in result.message


@pytest.mark.django_db
def test_removed_game_stays_reserved_and_can_be_scanned_back(shop):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    line = c.lines.get()
    svc.remove_line(c, shop.vol, line.pk)
    assert status(shop.games[0]) == ItemStatus.IN_CHECKOUT
    assert svc.scan(c, shop.vol, code(shop.games[0])).message.endswith("added back.")
    line.refresh_from_db()
    assert line.live and line.removed_at is None


@pytest.mark.django_db
def test_game_removed_in_another_cart_is_an_error_only_for_the_scanner(shop):
    first = cart(shop)
    svc.scan(first, shop.vol, code(shop.games[0]))
    svc.remove_line(first, shop.vol, first.lines.get().pk)
    second = cart(shop, shop.other, shop.r2)
    result = svc.scan(second, shop.other, code(shop.games[0]))
    assert not result.ok and "Val Volunteer" in result.message and "Register 1" in result.message
    assert not RegisterConflict.objects.exists()


@pytest.mark.django_db
def test_one_active_checkout_per_volunteer_and_per_register(shop):
    cart(shop)
    with pytest.raises(svc.SaleError):
        cart(shop, register=shop.r2)
    with pytest.raises(svc.SaleError, match="Register 1"):
        cart(shop, shop.other, shop.r1)


@pytest.mark.django_db
def test_database_enforces_one_active_checkout_per_owner(shop):
    cart(shop)
    with pytest.raises(IntegrityError), transaction.atomic():
        Checkout.objects.create(event=shop.event, owner=shop.vol, register=shop.r2)


@pytest.mark.django_db
def test_database_enforces_one_live_line_per_game(shop):
    a, b = cart(shop), cart(shop, shop.other, shop.r2)
    svc.scan(a, shop.vol, code(shop.games[0]))
    line = a.lines.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        CheckoutLine.objects.create(checkout=b, listing=line.listing, slip=line.slip,
                                    window=line.window, price_cents=100, commission_cents=10)


@pytest.mark.django_db
def test_moving_a_sale_to_another_register(shop):
    c = cart(shop)
    svc.move_to_register(c, shop.r2)
    c.refresh_from_db()
    assert c.register == shop.r2 and c.register_changed_at is not None


# ---------- Payment ----------


@pytest.mark.django_db
def test_proceed_creates_sale_id_and_locks_contents(shop):
    c = cart(shop)
    for g in shop.games[:3]:
        svc.scan(c, shop.vol, code(g))
    svc.remove_line(c, shop.vol, c.lines.get(listing=shop.games[2]).pk)
    c = svc.proceed_to_payment(c, shop.vol)
    vid = shop.vol.staff.volunteer_id
    assert c.sale_id.startswith(vid[-4:] + "-") and len(c.sale_id) == 9
    assert c.total_cents == 6000 and c.state == CheckoutState.AWAITING_PAYMENT
    assert [line.slip.name for line in svc.restock_lines(c)] == ["Catan"]
    assert "locked" in svc.scan(c, shop.vol, code(shop.games[3])).message
    with pytest.raises(svc.SaleError):
        svc.remove_line(c, shop.vol, c.lines.first().pk)


@pytest.mark.django_db
def test_empty_cart_cannot_proceed(shop):
    with pytest.raises(svc.SaleError, match="empty"):
        svc.proceed_to_payment(cart(shop), shop.vol)


@pytest.mark.django_db
def test_payment_received_sells_and_restocks(shop):
    c = cart(shop)
    for g in shop.games[:2]:
        svc.scan(c, shop.vol, code(g))
    svc.remove_line(c, shop.vol, c.lines.get(listing=shop.games[1]).pk)
    svc.proceed_to_payment(c, shop.vol)
    with pytest.raises(svc.SaleError):
        svc.proceed_to_payment(c, shop.vol)
    svc.payment_received(c, shop.vol)
    assert status(shop.games[0]) == ItemStatus.SOLD
    assert status(shop.games[1]) == ItemStatus.AVAILABLE
    c.refresh_from_db()
    assert c.state == CheckoutState.COMPLETE and c.paid_by == shop.vol
    with pytest.raises(svc.SaleError):
        svc.cancel(c, shop.vol)
    with pytest.raises(svc.SaleError):
        svc.payment_received(c, shop.vol)


@pytest.mark.django_db
def test_cancel_returns_games_except_those_an_admin_changed(shop):
    c = cart(shop)
    for g in shop.games[:3]:
        svc.scan(c, shop.vol, code(g))
    svc.remove_line(c, shop.vol, c.lines.get(listing=shop.games[2]).pk)
    svc.proceed_to_payment(c, shop.vol)
    shop.games[1].status = ItemStatus.MISSING
    shop.games[1].save()
    svc.cancel(c, shop.vol)
    c.refresh_from_db()
    assert c.state == CheckoutState.CANCELED and c.sale_id  # Keeps its Sale ID
    assert [status(g) for g in shop.games[:3]] == [
        ItemStatus.AVAILABLE, ItemStatus.MISSING, ItemStatus.AVAILABLE]
    assert not c.lines.filter(live=True).exists()


@pytest.mark.django_db
def test_sold_game_scanned_again_raises_a_flag(shop, login):
    from conftest import PIN

    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(c, shop.vol)
    svc.payment_received(c, shop.vol)
    c2 = cart(shop)
    result = svc.scan(c2, shop.vol, code(shop.games[0]))
    assert not result.ok and "SOLD" in result.message and result.level == "warning"
    svc.scan(c2, shop.vol, code(shop.games[0]))
    flag = SoldItemFlag.objects.get()
    assert flag.resolved_at is None and c2.lines.count() == 0
    from django.urls import reverse

    client = login(shop.vol)
    url = reverse("consignment:alerts", args=[shop.event.pk])
    client.post(url, {"key": f"f{flag.pk}", f"f{flag.pk}-admin_badge": shop.admin.staff.admin_id,
                      f"f{flag.pk}-admin_pin": PIN, f"f{flag.pk}-admin_note": "Holding for buyer"})
    flag.refresh_from_db()
    assert flag.resolved_by == shop.admin and flag.note == "Holding for buyer"


# ---------- Two-register conflicts ----------


@pytest.fixture
def conflict(shop):
    first = cart(shop)
    svc.scan(first, shop.vol, code(shop.games[0]))
    svc.scan(first, shop.vol, code(shop.games[1]))
    second = cart(shop, shop.other, shop.r2)
    svc.scan(second, shop.other, code(shop.games[2]))
    result = svc.scan(second, shop.other, code(shop.games[0]))
    assert result.level == "conflict"
    return first, second, RegisterConflict.objects.get()


@pytest.mark.django_db
def test_conflict_blocks_both_registers(shop, conflict):
    first, second, _ = conflict
    for c, who in ((first, shop.vol), (second, shop.other)):
        with pytest.raises(svc.SaleError, match="conflict"):
            svc.proceed_to_payment(c, who)
    assert svc.open_conflicts(first).count() == 1 == svc.open_conflicts(second).count()


@pytest.mark.django_db
def test_conflict_blocks_payment_after_sale_id(shop):
    first = cart(shop)
    svc.scan(first, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(first, shop.vol)
    second = cart(shop, shop.other, shop.r2)
    assert svc.scan(second, shop.other, code(shop.games[0])).level == "conflict"
    with pytest.raises(svc.SaleError, match="conflict"):
        svc.payment_received(first, shop.vol)


@pytest.mark.django_db
def test_admin_keeps_game_with_first_sale(shop, conflict):
    first, second, c = conflict
    svc.resolve_conflict(c, shop.admin, shop.other, keep_scanner=False, note="Buyer 1 has it")
    c.refresh_from_db()
    assert c.kept_by_checkout == first and c.resolved_by == shop.admin
    svc.proceed_to_payment(first, shop.vol)
    svc.proceed_to_payment(second, shop.other)


@pytest.mark.django_db
def test_admin_gives_game_to_second_sale(shop, conflict):
    first, second, c = conflict
    message = svc.resolve_conflict(c, shop.admin, shop.other, keep_scanner=True, note="n")
    assert "Scan the game again" in message
    line = first.lines.get(listing=shop.games[0])
    assert not line.live and line.released_by_admin == shop.admin
    assert status(shop.games[0]) == ItemStatus.AVAILABLE
    assert svc.scan(second, shop.other, code(shop.games[0])).ok


@pytest.mark.django_db
def test_taking_game_from_sale_with_sale_id_cancels_that_sale(shop):
    first = cart(shop)
    svc.scan(first, shop.vol, code(shop.games[0]))
    svc.scan(first, shop.vol, code(shop.games[1]))
    svc.proceed_to_payment(first, shop.vol)
    second = cart(shop, shop.other, shop.r2)
    svc.scan(second, shop.other, code(shop.games[0]))
    svc.resolve_conflict(RegisterConflict.objects.get(), shop.admin, shop.other, True, "n")
    first.refresh_from_db()
    assert first.state == CheckoutState.CANCELED
    assert status(shop.games[1]) == ItemStatus.AVAILABLE


@pytest.mark.django_db
def test_volunteer_in_conflict_can_cancel_own_sale(shop, conflict):
    first, second, c = conflict
    svc.cancel(second, shop.other)
    c.refresh_from_db()
    assert c.resolved_at is not None
    svc.proceed_to_payment(first, shop.vol)


@pytest.mark.django_db
def test_conflict_resolution_needs_badge_and_pin(shop, conflict, login):
    from django.urls import reverse

    _, _, c = conflict
    client = login(shop.other)
    url = reverse("consignment:alerts", args=[shop.event.pk])
    page = client.post(url, {"key": f"c{c.pk}", f"c{c.pk}-keep": "holder",
                             f"c{c.pk}-admin_badge": shop.admin.staff.admin_id,
                             f"c{c.pk}-admin_pin": "0000", f"c{c.pk}-admin_note": "x"})
    c.refresh_from_db()
    assert c.resolved_at is None and b"Wrong PIN" in page.content


# ---------- Admin release, admin carts and overrides ----------


@pytest.mark.django_db
def test_admin_releases_game_from_open_cart_only(shop):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.admin_release(c.lines.get(), shop.admin)
    assert status(shop.games[0]) == ItemStatus.AVAILABLE
    svc.scan(c, shop.vol, code(shop.games[1]))
    svc.proceed_to_payment(c, shop.vol)
    with pytest.raises(svc.SaleError, match="Sale ID"):
        svc.admin_release(c.lines.get(listing=shop.games[1]), shop.admin)


@pytest.mark.django_db
@pytest.mark.parametrize("status_,closed,expected", [
    ("checkin", False, [("chosen", ["Friday"])]),
    ("sale", False, [("chosen", ["Friday", "Saturday", "Sunday"])]),
    ("sale", True, [("per_item", ["Friday", "Saturday", "Sunday"])]),
    ("pickup", False, [("per_item", ["Friday", "Saturday", "Sunday"]),
                       ("chosen", ["Friday", "Saturday", "Sunday"])]),
    ("presale", False, []),
])
def test_admin_cart_modes(shop, status_, closed, expected):
    shop.event.status = status_
    shop.event.last_window_closed_at = timezone.now() if closed else None
    assert [(m, [w.name for w in ws]) for m, ws in svc.admin_cart_modes(shop.event)] == expected


@pytest.mark.django_db
def test_admin_cart_per_item_pricing_and_sale_id(shop):
    sunday = shop.event.windows.get(position=3)
    shop.event.status = "pickup"
    shop.event.save()
    c = svc.open_admin_cart(shop.admin, shop.event, shop.r2, PricingMode.PER_ITEM)
    assert "Choose the price window" in svc.scan(c, shop.admin, code(shop.games[0])).message
    assert svc.scan(c, shop.admin, code(shop.games[0]), sunday).ok
    assert c.lines.get().price_cents == 1000
    svc.proceed_to_payment(c, shop.admin)
    c.refresh_from_db()
    assert c.sale_id.startswith(shop.admin.staff.admin_id[-4:])
    assert c.opened_by_admin == shop.admin


@pytest.mark.django_db
def test_admin_cart_in_checkin_uses_first_window_only(shop):
    shop.event.status = "checkin"
    shop.event.save()
    with pytest.raises(svc.SaleError):
        svc.open_admin_cart(shop.admin, shop.event, shop.r2, PricingMode.CHOSEN,
                            shop.event.windows.get(position=2))
    c = svc.open_admin_cart(shop.admin, shop.event, shop.r2, PricingMode.CHOSEN,
                            shop.event.windows.first())
    assert svc.scan(c, shop.admin, code(shop.games[0])).ok


@pytest.mark.django_db
def test_sale_override_returns_completed_sale_to_payment(shop):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(c, shop.vol)
    svc.payment_received(c, shop.vol)
    cart(shop)  # The volunteer has moved on to another sale.
    svc.sale_override(c, shop.admin, shop.vol, "Card declined after all")
    c.refresh_from_db()
    assert c.state == CheckoutState.AWAITING_PAYMENT and c.reopened_by_override
    assert status(shop.games[0]) == ItemStatus.IN_CHECKOUT
    svc.cancel(c, shop.admin)
    assert status(shop.games[0]) == ItemStatus.AVAILABLE
    with pytest.raises(svc.SaleError):
        svc.sale_override(c, shop.admin, shop.vol, "again")


@pytest.mark.django_db
def test_pickup_blocked_by_open_carts(shop):
    c = svc.open_admin_cart(shop.admin, shop.event, shop.r2, PricingMode.CHOSEN,
                            shop.event.windows.first())
    with pytest.raises(EventError, match="still open"):
        advance_status(shop.admin, shop.event, "sale")
    svc.cancel(c, shop.admin)
    advance_status(shop.admin, shop.event, "sale")


@pytest.mark.django_db
def test_actions_are_audited(shop):
    from apps.core.models import AuditLog

    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.remove_line(c, shop.vol, c.lines.get().pk)
    svc.cancel(c, shop.vol)
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"window.activated", "checkout.started", "checkout.line_removed",
            "checkout.canceled"} <= actions
