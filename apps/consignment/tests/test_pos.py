"""Register screens, and double-sale protection under real concurrent transactions."""

import threading

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.consignment.models import CheckoutLine, CheckoutState, ItemStatus, RegisterConflict
from apps.consignment.pos_views import COOKIE, COOKIE_SALT
from apps.consignment.services import checkout as svc

from .conftest import cart, code, status


def at_register(account, register):
    """A fresh sign-in on a laptop set to `register`."""
    client = Client()
    client.force_login(account, backend="django.contrib.auth.backends.ModelBackend")
    client.get("/")  # Creates the response cookie jar
    from django.core.signing import get_cookie_signer

    signer = get_cookie_signer(salt=COOKIE + COOKIE_SALT)
    client.cookies[COOKIE] = signer.sign(str(register.pk))
    return client


def pos_url(shop):
    return reverse("consignment:pos", args=[shop.event.pk])


@pytest.mark.django_db
def test_laptop_without_register_must_choose(shop, login):
    client = login(shop.vol)
    response = client.get(pos_url(shop))
    assert response.url == reverse("consignment:choose_register", args=[shop.event.pk])
    response = client.post(response.url, {"register": shop.r2.pk})
    assert response.url == pos_url(shop)
    assert "Register 2" in client.get(pos_url(shop)).content.decode()


@pytest.mark.django_db
def test_full_sale_at_the_register(shop):
    client = at_register(shop.vol, shop.r1)
    page = client.get(pos_url(shop)).content.decode()
    assert "Friday" in page and "Active price window" in page
    client.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    client.post(pos_url(shop), {"action": "scan", "code": code(shop.games[1]).lower()})
    c = svc.active_checkout(shop.vol, shop.event)
    client.post(pos_url(shop), {"action": "remove", "line": c.lines.get(listing=shop.games[1]).pk})
    client.post(pos_url(shop), {"action": "proceed"})
    c.refresh_from_db()
    page = client.get(pos_url(shop)).content.decode()
    assert c.sale_id in page and "MOVE THESE ITEMS TO THE RESTOCK TABLE NOW" in page
    assert "Azul" in page and "$30.00" in page
    confirm = reverse("consignment:pos_confirm", args=[shop.event.pk, "paid"])
    assert "Payment received?" in client.get(confirm).content.decode()
    client.post(confirm)
    c.refresh_from_db()
    assert c.state == CheckoutState.COMPLETE
    assert status(shop.games[0]) == ItemStatus.SOLD and status(shop.games[1]) == ItemStatus.AVAILABLE


@pytest.mark.django_db
def test_failed_first_scan_leaves_no_open_cart(shop):
    client = at_register(shop.vol, shop.r1)
    client.post(pos_url(shop), {"action": "scan", "code": "nonsense"})
    assert svc.active_checkout(shop.vol, shop.event) is None


@pytest.mark.django_db
def test_cancel_needs_confirmation(shop):
    client = at_register(shop.vol, shop.r1)
    client.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    url = reverse("consignment:pos_confirm", args=[shop.event.pk, "cancel"])
    assert "Cancel this sale?" in client.get(url).content.decode()
    assert status(shop.games[0]) == ItemStatus.IN_CHECKOUT
    client.post(url)
    assert status(shop.games[0]) == ItemStatus.AVAILABLE


@pytest.mark.django_db
def test_signing_in_on_another_register_moves_the_sale(shop):
    old = at_register(shop.vol, shop.r1)
    old.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    new = at_register(shop.vol, shop.r2)
    assert "moved to this register" in new.get(pos_url(shop), follow=True).content.decode()
    c = svc.active_checkout(shop.vol, shop.event)
    assert c.register == shop.r2
    page = old.get(pos_url(shop)).content.decode()
    assert "Your sale moved" in page and "Register 2" in page
    old.post(pos_url(shop), {"action": "scan", "code": code(shop.games[1])})
    assert c.lines.count() == 1
    status_page = old.get(reverse("consignment:pos_status", args=[shop.event.pk])).content.decode()
    assert "moved to register Register 2" in status_page


@pytest.mark.django_db
def test_status_poll_shows_conflict_at_both_registers(shop):
    first = at_register(shop.vol, shop.r1)
    second = at_register(shop.other, shop.r2)
    first.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    second.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    assert RegisterConflict.objects.filter(resolved_at__isnull=True).count() == 1
    url = reverse("consignment:pos_status", args=[shop.event.pk])
    for client in (first, second):
        assert "Two-register conflict" in client.get(url).content.decode()


@pytest.mark.django_db
def test_admin_screens(shop, login):
    client = login(shop.admin)
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    url = reverse("consignment:sales_control", args=[shop.event.pk])
    page = client.get(url).content.decode()
    assert "Val Volunteer" in page and "can&#x27;t change" not in page
    assert "Regular carts are open" in page
    client.post(url, {"action": "cancel_cart", "checkout": c.pk})
    c.refresh_from_db()
    assert c.state == CheckoutState.CANCELED
    client.post(url, {"action": "activate", "window": shop.event.windows.get(position=2).pk})
    shop.event.refresh_from_db()
    assert shop.event.active_window.name == "Saturday"
    assert login(shop.vol).get(url).status_code == 403


@pytest.mark.django_db
def test_admin_opens_admin_cart_from_sales_control(shop):
    client = at_register(shop.admin, shop.r2)
    client.post(reverse("consignment:sales_control", args=[shop.event.pk]), {
        "action": "admin_cart", "mode": "chosen", "window": shop.event.windows.get(position=3).pk})
    c = svc.active_checkout(shop.admin, shop.event)
    assert c.is_admin_cart and c.window.name == "Sunday"
    client.post(pos_url(shop), {"action": "scan", "code": code(shop.games[0])})
    assert c.lines.get().price_cents == 1000


@pytest.mark.django_db
def test_sale_override_from_sale_page(shop, login):
    from conftest import PIN

    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(c, shop.vol)
    svc.payment_received(c, shop.vol)
    client = login(shop.admin)
    url = reverse("consignment:sale_detail", args=[shop.event.pk, c.pk])
    client.post(url, {"action": "override", "ovr-admin_badge": shop.admin.staff.admin_id,
                      "ovr-admin_pin": PIN, "ovr-admin_note": ""})
    c.refresh_from_db()
    assert c.state == CheckoutState.COMPLETE  # The note is required
    client.post(url, {"action": "override", "ovr-admin_badge": shop.admin.staff.admin_id,
                      "ovr-admin_pin": PIN, "ovr-admin_note": "Card declined"})
    c.refresh_from_db()
    assert c.state == CheckoutState.AWAITING_PAYMENT and c.overrides.get().note == "Card declined"
    client.post(url, {"action": "paid", "confirm": "yes"})
    c.refresh_from_db()
    assert c.state == CheckoutState.COMPLETE and status(shop.games[0]) == ItemStatus.SOLD


@pytest.mark.django_db
def test_release_from_item_page(shop, login):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    client = login(shop.admin)
    assert "Release from the cart" in client.get(
        reverse("consignment:item_detail", args=[shop.games[0].pk])).content.decode()
    client.post(reverse("consignment:release_item", args=[shop.games[0].pk]))
    assert status(shop.games[0]) == ItemStatus.AVAILABLE


@pytest.mark.django_db
def test_sales_list(shop, login):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(c, shop.vol)
    svc.payment_received(c, shop.vol)
    c.refresh_from_db()
    page = login(shop.admin).get(reverse("consignment:sales_list", args=[shop.event.pk]),
                                 {"q": c.sale_id.lower()}).content.decode()
    assert c.sale_id in page and "$30.00" in page


# ---------- Concurrency, against real Postgres transactions ----------


def run_together(*funcs):
    """Run each function in its own thread and database connection, starting together."""
    barrier = threading.Barrier(len(funcs))
    results = [None] * len(funcs)

    def wrap(i, func):
        try:
            barrier.wait()
            results[i] = func()
        except Exception as e:  # noqa: BLE001  (the test inspects it)
            results[i] = e
        finally:
            connection.close()

    threads = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(funcs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


@pytest.mark.django_db(transaction=True)
def test_two_registers_scanning_one_game_at_once(shop):
    game = code(shop.games[0])
    for _ in range(3):
        a, b = cart(shop), cart(shop, shop.other, shop.r2)
        results = run_together(lambda a=a: svc.scan(a, shop.vol, game),
                               lambda b=b: svc.scan(b, shop.other, game))
        assert sorted(r.ok for r in results) == [False, True]
        assert [r.level for r in results if not r.ok] == ["conflict"]
        assert CheckoutLine.objects.filter(listing=shop.games[0], live=True).count() == 1
        svc.cancel(a, shop.vol)
        svc.cancel(b, shop.other)


@pytest.mark.django_db(transaction=True)
def test_payment_recorded_once_under_double_submit(shop):
    c = cart(shop)
    svc.scan(c, shop.vol, code(shop.games[0]))
    svc.proceed_to_payment(c, shop.vol)
    results = run_together(lambda: svc.payment_received(c, shop.vol),
                           lambda: svc.payment_received(c, shop.vol))
    assert sum(isinstance(r, svc.SaleError) for r in results) == 1
    assert status(shop.games[0]) == ItemStatus.SOLD


@pytest.mark.django_db(transaction=True)
def test_one_active_cart_per_volunteer_under_concurrency(shop):
    results = run_together(lambda: cart(shop), lambda: cart(shop, register=shop.r2))
    assert sum(isinstance(r, svc.SaleError) for r in results) == 1
    assert len([r for r in results if not isinstance(r, Exception)]) == 1
