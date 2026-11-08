"""Fees, payouts, closing and reports (SPEC sections 9, 11 and 12)."""

import pytest
from django.urls import reverse

from apps.accounts.services import link_seller, set_event_access
from apps.consignment.models import (
    ImmutableError,
    ItemStatus,
    Listing,
    PayoutOutcome,
    PickupEntry,
    Register,
    SoldItemFlag,
)
from apps.consignment.services import checkin as csvc
from apps.consignment.services import checkout as osvc
from apps.consignment.services import fees, payouts
from apps.consignment.services import listings as lsvc
from apps.consignment.services import pickup as psvc
from apps.events.services import EventError, advance_status, set_waiver
from conftest import PASSWORD

from .conftest import COMPLETE


@pytest.fixture
def stock(admin):
    """Give a seller `n` checked-in games, each priced `dollars` in every window."""
    def make(seller, n, dollars=8, **details):
        event = seller.event
        event.status = "presale"
        event.save()
        windows = list(event.windows.all())
        listings = [lsvc.create_listing(seller, seller.account, {**COMPLETE, "name": f"Game {i}",
                                                                 **details},
                                        {w.pk: dollars for w in windows}) for i in range(n)]
        lsvc.generate_slips(seller, seller.account)
        event.status = "checkin"
        event.save()
        report = csvc.get_report(seller, admin)
        for lst in listings:
            lst.refresh_from_db()
            csvc.accept(report, admin, lst.current_slip)
            lst.refresh_from_db()
        return listings
    return make


@pytest.fixture
def till(admin):
    """Sell games through a real checkout, with the event in Sale."""
    state = {}

    def sell(games):
        event = games[0].seller.event
        event.refresh_from_db()
        if event.status != "sale":
            event.status = "sale"
            event.save()
        if event.active_window_id is None:
            osvc.set_active_window(admin, event, event.windows.first())
            event.refresh_from_db()
        register = state.get(event.pk) or Register.objects.create(event=event, name="R1")
        state[event.pk] = register
        c = osvc.start_regular(admin, event, register)
        for g in games:
            g.refresh_from_db()
            assert osvc.scan(c, admin, g.accepted_slip.item_id).ok
        osvc.proceed_to_payment(c, admin)
        return osvc.payment_received(c, admin)
    return sell


def set_status(games, status):
    Listing.objects.filter(pk__in=[g.pk for g in games]).update(status=status)


def to_pickup(event):
    event.refresh_from_db()
    event.status = "pickup"
    event.save()


# ---------- Fees ----------


@pytest.mark.django_db
def test_spec_example_seller_a(seller, stock, till):
    games = stock(seller, 23)
    till(games[:15])  # 15 × $8 = $120
    set_status(games[15:], ItemStatus.RETURNED)
    f = fees.figures(seller)
    assert (f.checked_in, f.sold, f.gross) == (23, 15, 12000)
    assert (f.commission, f.sheets, f.listing_fee) == (1200, 2, 400)
    assert (f.unsold_count, f.unsold_fee) == (8, 200)
    assert f.net == 10200 and f.payout_due == 10200 and not f.projected


@pytest.mark.django_db
@pytest.mark.parametrize("charged,net,forgiven,owed", [(False, 0, 325, 0), (True, -325, 0, 325)])
def test_spec_example_seller_b(seller, stock, charged, net, forgiven, owed):
    seller.event.charge_negative_balances = charged
    seller.event.save()
    games = stock(seller, 5)
    set_status(games, ItemStatus.ABANDONED)
    f = fees.figures(seller)
    assert (f.listing_fee, f.unsold_fee) == (200, 125)
    assert (f.net, f.forgiven, f.balance_owed, f.payout_due) == (net, forgiven, owed, 0)


@pytest.mark.django_db
def test_seller_with_nothing_checked_in_pays_nothing(seller, make_listing):
    make_listing()
    f = fees.figures(seller)
    assert f.fees == 0 and f.net == 0 and f.checked_in == 0


@pytest.mark.django_db
def test_flat_listing_fee(seller, stock):
    seller.event.listing_fee_type, seller.event.flat_fee_cents = "flat", 500
    seller.event.save()
    stock(seller, 41)
    f = fees.figures(seller)
    assert f.listing_fee == 500 and f.sheets == 0


@pytest.mark.django_db
@pytest.mark.parametrize("n,sheets", [(1, 1), (20, 1), (21, 2), (40, 2), (41, 3)])
def test_partial_sheets_count_as_full(seller, stock, n, sheets):
    stock(seller, n)
    assert fees.figures(seller).sheets == sheets


@pytest.mark.django_db
def test_commission_rounds_half_up_per_item(seller, stock, till):
    seller.event.commission_percent = 15
    seller.event.save()
    games = stock(seller, 3, dollars=3)  # 15% of $3 = 45¢ each
    till(games)
    assert fees.figures(seller).commission == 135
    seller.event.refresh_from_db()


@pytest.mark.django_db
def test_commission_uses_rate_and_price_recorded_on_the_sale(seller, stock, till, admin):
    games = stock(seller, 1, dollars=7)
    till(games)
    seller.event.commission_percent = 50  # Locked in practice; history still wins.
    seller.event.save()
    f = fees.figures(seller)
    assert (f.gross, f.commission) == (700, 70)


@pytest.mark.django_db
@pytest.mark.parametrize("on_donated,expected", [(False, 2), (True, 3)])
def test_unsold_fee_on_donated_follows_the_setting(seller, stock, on_donated, expected):
    seller.event.charge_unsold_on_donated = on_donated
    seller.event.save()
    games = stock(seller, 4)
    set_status(games[:1], ItemStatus.RETURNED)
    set_status(games[1:2], ItemStatus.ABANDONED)
    set_status(games[2:3], ItemStatus.DONATED)
    set_status(games[3:], ItemStatus.MISSING)
    f = fees.figures(seller)
    assert f.unsold_count == expected and not f.projected


@pytest.mark.django_db
def test_unsold_fees_in_progress_are_projected(seller, stock):
    games = stock(seller, 2)
    stock_donate = stock(seller, 1, donate_if_unsold=True)
    f = fees.figures(seller)
    assert f.projected and f.unsold_count == 2  # The donation-marked game isn't counted
    set_status(games + stock_donate, ItemStatus.RETURNED)
    f = fees.figures(seller)
    assert not f.projected and f.unsold_count == 3


@pytest.mark.django_db
def test_individual_waiver(seller, stock, till, admin):
    games = stock(seller, 3)
    till(games[:1])
    set_status(games[1:], ItemStatus.RETURNED)
    set_waiver(admin, seller.event, category="seller", seller=seller, commission=True,
               listing=False, unsold=True)
    f = fees.figures(seller)
    assert (f.commission, f.waived_commission) == (0, 80)
    assert (f.unsold_fee, f.waived_unsold) == (0, 50)
    assert f.listing_fee == 200 and f.net == 800 - 200


@pytest.mark.django_db
def test_category_waivers_apply_to_linked_staff(seller, stock, admin, volunteer, make_staff):
    stock(seller, 1)
    set_waiver(admin, seller.event, category="volunteer", commission=False, listing=True,
               unsold=False)
    set_waiver(admin, seller.event, category="admin", commission=False, listing=False,
               unsold=True)
    assert fees.figures(seller).listing_fee == 200
    link_seller(volunteer, seller.seller_id, PASSWORD)
    seller.refresh_from_db()
    f = fees.figures(seller)
    assert f.listing_fee == 0 and f.waived_listing == 200 and f.unsold_fee == 25


@pytest.mark.django_db
def test_staff_with_both_roles_get_both_waivers(seller, stock, admin, make_staff):
    both = make_staff("Bo Both", is_admin=True, is_volunteer=True)
    stock(seller, 1)
    set_waiver(admin, seller.event, category="volunteer", commission=False, listing=True,
               unsold=False)
    set_waiver(admin, seller.event, category="admin", commission=False, listing=False,
               unsold=True)
    link_seller(both, seller.seller_id, PASSWORD)
    seller.refresh_from_db()
    f = fees.figures(seller)
    assert f.listing_fee == 0 and f.unsold_fee == 0


@pytest.mark.django_db
def test_waivers_never_create_a_negative_net_beyond_sales(seller, stock, admin):
    games = stock(seller, 2)
    set_status(games, ItemStatus.RETURNED)
    set_waiver(admin, seller.event, category="seller", seller=seller, commission=True,
               listing=True, unsold=True)
    f = fees.figures(seller)
    assert f.fees == 0 and f.net == 0 and f.forgiven == 0 and f.waived == 250


# ---------- Pickup ----------


@pytest.fixture
def pickup_shop(seller, stock, till, make_listing):
    games = stock(seller, 5)
    till(games[:1])
    to_pickup(seller.event)
    return games


@pytest.mark.django_db
def test_available_game_is_returned(pickup_shop, seller, volunteer):
    listing, kind, message = psvc.scan_return(seller, volunteer, pickup_shop[1].accepted_slip.item_id)
    assert kind is None and "returned" in message
    pickup_shop[1].refresh_from_db()
    assert pickup_shop[1].status == ItemStatus.RETURNED
    assert pickup_shop[1].pickup_entries.get().outcome == "returned"


@pytest.mark.django_db
def test_sold_game_is_not_returned_and_raises_flag(pickup_shop, seller, volunteer):
    with pytest.raises(psvc.PickupError, match="SOLD"):
        psvc.scan_return(seller, volunteer, pickup_shop[0].accepted_slip.item_id)
    assert SoldItemFlag.objects.get().context == "pickup"
    assert PickupEntry.objects.get().outcome == "refused"


@pytest.mark.django_db
@pytest.mark.parametrize("status", [ItemStatus.ERROR, ItemStatus.MISSING])
def test_error_and_missing_need_an_admin_first(pickup_shop, seller, volunteer, status):
    set_status(pickup_shop[1:2], status)
    with pytest.raises(psvc.PickupError, match="admin must resolve"):
        psvc.scan_return(seller, volunteer, pickup_shop[1].accepted_slip.item_id)


@pytest.mark.django_db
def test_listed_game_seller_may_take(seller, stock, make_listing, volunteer, pickup_shop):
    seller.event.status = "presale"
    seller.event.save()
    extra = make_listing(name="Never brought")
    lsvc.generate_slips(seller, seller.account)
    to_pickup(seller.event)
    extra.refresh_from_db()
    listing, kind, _ = psvc.scan_return(seller, volunteer, extra.current_slip.item_id)
    assert kind == psvc.TAKE
    psvc.decide(listing, volunteer, kind, seller_takes=True)
    extra.refresh_from_db()
    assert extra.status == ItemStatus.RETURNED and not extra.is_checked_in
    assert fees.figures(seller).unsold_count == 4  # Never checked in: no unsold fee


@pytest.mark.django_db
def test_donated_game_kept_or_taken_back(pickup_shop, seller, volunteer):
    seller.event.charge_unsold_on_donated = False
    seller.event.save()
    set_status(pickup_shop[1:3], ItemStatus.DONATED)
    before = fees.figures(seller).unsold_count
    for game, takes in ((pickup_shop[1], False), (pickup_shop[2], True)):
        listing, kind, _ = psvc.scan_return(seller, volunteer, game.accepted_slip.item_id)
        assert kind == psvc.DONATED
        psvc.decide(listing, volunteer, kind, seller_takes=takes)
    statuses = [Listing.objects.get(pk=g.pk).status for g in pickup_shop[1:3]]
    assert statuses == [ItemStatus.DONATED, ItemStatus.RETURNED]
    assert fees.figures(seller).unsold_count == before + 1  # Taken back now pays the fee


@pytest.mark.django_db
def test_abandoned_game_taken_back_keeps_the_fee(pickup_shop, seller, volunteer):
    set_status(pickup_shop[1:], ItemStatus.ABANDONED)
    before = fees.figures(seller).unsold_fee
    listing, kind, _ = psvc.scan_return(seller, volunteer, pickup_shop[1].accepted_slip.item_id)
    psvc.decide(listing, volunteer, kind, seller_takes=True)
    assert fees.figures(seller).unsold_fee == before


@pytest.mark.django_db
def test_another_sellers_game_is_not_returned(pickup_shop, seller, volunteer, make_seller, stock):
    other = make_seller(seller.event, "Other")
    theirs = stock(other, 1)
    to_pickup(seller.event)
    with pytest.raises(psvc.PickupError, match="belongs to seller"):
        psvc.scan_return(seller, volunteer, theirs[0].accepted_slip.item_id)


@pytest.mark.django_db
def test_pickup_only_in_pickup(pickup_shop, seller, volunteer):
    seller.event.status = "sale"
    seller.event.save()
    with pytest.raises(psvc.PickupError, match="Pickup"):
        psvc.scan_return(seller, volunteer, pickup_shop[1].accepted_slip.item_id)


@pytest.mark.django_db
def test_unsold_scanning(pickup_shop, seller, admin, stock):
    seller.event.status = "presale"
    seller.event.save()
    donate = stock(seller, 1, donate_if_unsold=True)[0]
    to_pickup(seller.event)
    ok, message = psvc.scan_unsold(seller.event, admin, donate.accepted_slip.item_id)
    assert ok and "Donated" in message
    ok, _ = psvc.scan_unsold(seller.event, admin, pickup_shop[1].accepted_slip.item_id)
    assert ok and Listing.objects.get(pk=pickup_shop[1].pk).status == ItemStatus.ABANDONED
    ok, message = psvc.scan_unsold(seller.event, admin, pickup_shop[0].accepted_slip.item_id)
    assert not ok and "SOLD" in message and SoldItemFlag.objects.exists()


@pytest.mark.django_db
def test_admin_status_moves(pickup_shop, admin):
    game = pickup_shop[1]
    with pytest.raises(psvc.PickupError, match="note"):
        psvc.admin_set_status(game, admin, ItemStatus.MISSING, "")
    psvc.admin_set_status(game, admin, ItemStatus.MISSING, "Not on the tables")
    game.refresh_from_db()
    assert game.status == ItemStatus.MISSING
    assert game.status_changes.last().approved_by_admin == admin
    with pytest.raises(psvc.PickupError):
        psvc.admin_set_status(game, admin, ItemStatus.SOLD, "no")


# ---------- Payouts ----------


@pytest.fixture
def settled(pickup_shop, seller, volunteer):
    """Every game accounted for: one sold, the rest returned."""
    for g in pickup_shop[1:]:
        psvc.scan_return(seller, volunteer, g.accepted_slip.item_id)
    return pickup_shop


@pytest.mark.django_db
def test_payout_needs_every_game_accounted_for(pickup_shop, seller, admin):
    problems = payouts.blockers(seller)
    assert any("accounted for" in p for p in problems)
    with pytest.raises(payouts.PayoutError):
        payouts.record(seller, admin, PayoutOutcome.PAID, "chk 1")


@pytest.mark.django_db
def test_payout_blocked_by_open_sold_flag(settled, seller, volunteer, admin):
    with pytest.raises(psvc.PickupError):
        psvc.scan_return(seller, volunteer, settled[0].accepted_slip.item_id)
    assert any("Sold flag" in p for p in payouts.blockers(seller))


@pytest.mark.django_db
def test_paid_needs_a_reference_and_freezes_figures(settled, seller, admin, volunteer):
    f = fees.figures(seller)
    assert f.net == 800 - 80 - 200 - 100  # $8 sale, 10%, 1 sheet, 4 unsold
    with pytest.raises(payouts.PayoutError, match="check number"):
        payouts.record(seller, admin, PayoutOutcome.PAID, "")
    with pytest.raises(payouts.PayoutError, match="doesn't fit"):
        payouts.record(seller, admin, PayoutOutcome.NOTHING_DUE)
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 1042")
    seller.refresh_from_db()
    frozen = fees.figures(seller)
    assert frozen.frozen and frozen.net == f.net and frozen.payout.reference == "Check 1042"
    # Later changes don't touch frozen figures.
    seller.event.unsold_fee_cents = 999
    seller.event.save()
    set_waiver(admin, seller.event, category="seller", seller=seller, commission=True,
               listing=True, unsold=True)
    seller.refresh_from_db()
    assert fees.figures(seller).net == f.net
    with pytest.raises(ImmutableError):
        seller.settlement.save()
    with pytest.raises(payouts.PayoutError, match="already"):
        payouts.record(seller, admin, PayoutOutcome.PAID, "again")


@pytest.mark.django_db
def test_frozen_seller_games_cannot_change_at_pickup(settled, seller, admin, volunteer):
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 1")
    seller.refresh_from_db()
    set_status(settled[1:2], ItemStatus.DONATED)
    with pytest.raises(psvc.PickupError, match="frozen"):
        psvc.scan_return(seller, volunteer, settled[1].accepted_slip.item_id)


@pytest.mark.django_db
def test_automatic_outcomes(seller, make_seller, stock, admin, volunteer):
    seller.event.charge_negative_balances = False
    seller.event.save()
    forgiven = stock(seller, 2)
    even = make_seller(seller.event, "Even Steven")
    waived = stock(even, 1)
    set_waiver(admin, seller.event, category="seller", seller=even, commission=True,
               listing=True, unsold=True)
    to_pickup(seller.event)
    set_status(forgiven + waived, ItemStatus.RETURNED)
    assert payouts.record_automatic(seller.event, admin) == 2
    seller.refresh_from_db()
    even.refresh_from_db()
    assert seller.settlement.payout.outcome == PayoutOutcome.FORGIVEN
    assert seller.settlement.forgiven_cents == 250 and seller.settlement.net_cents == 0
    assert even.settlement.payout.outcome == PayoutOutcome.NOTHING_DUE


@pytest.mark.django_db
def test_charged_balance_collected_or_not(seller, stock, admin):
    seller.event.charge_negative_balances = True
    seller.event.save()
    games = stock(seller, 1)
    to_pickup(seller.event)
    set_status(games, ItemStatus.RETURNED)
    with pytest.raises(payouts.PayoutError):
        payouts.record(seller, admin, PayoutOutcome.FORGIVEN)
    payouts.record(seller, admin, PayoutOutcome.NOT_COLLECTED, note="No response")
    seller.refresh_from_db()
    f = fees.figures(seller)
    assert f.uncollected == 225 and f.fees_kept == 0


@pytest.mark.django_db
def test_sale_override_blocked_after_payout(settled, seller, admin):
    sale = settled[0].checkout_lines.get().checkout
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 9")
    with pytest.raises(osvc.SaleError, match="payout"):
        osvc.sale_override(sale, admin, admin, "Card declined")


# ---------- The money invariant ----------


@pytest.mark.django_db
def test_money_invariant_across_every_outcome(event, make_seller, stock, till, admin, volunteer):
    event.charge_negative_balances = True
    event.save()
    paid, owes_collected, owes_not, waived = (make_seller(event, n) for n in (
        "Paid Pat", "Collected Cal", "Uncollected Uma", "Waived Wes"))
    games = {s: stock(s, n) for s, n in ((paid, 3), (owes_collected, 1), (owes_not, 2),
                                         (waived, 2))}
    till(games[paid][:2] + games[waived][:1])
    set_waiver(admin, event, category="seller", seller=waived, commission=True, listing=False,
               unsold=True)
    to_pickup(event)
    for gs in games.values():
        set_status([g for g in gs if Listing.objects.get(pk=g.pk).status == "available"],
                   ItemStatus.RETURNED)
    payouts.record(paid, admin, PayoutOutcome.PAID, "Check 1")
    payouts.record(owes_collected, admin, PayoutOutcome.COLLECTED)
    payouts.record(owes_not, admin, PayoutOutcome.NOT_COLLECTED)
    payouts.record(waived, admin, PayoutOutcome.PAID, "Check 2")
    _, t = fees.event_figures(event)
    assert t.balanced
    assert t.gross + t.collected == t.payouts + t.fees_kept
    assert t.uncollected == 250 and t.collected == 225


# ---------- Closing ----------


@pytest.mark.django_db
def test_closing_requires_everything_settled(settled, seller, admin, make_seller):
    problems = payouts.closing_blockers(seller.event)
    assert any("no payout outcome" in p for p in problems)
    with pytest.raises(EventError):
        advance_status(admin, seller.event, "pickup")
    make_seller(seller.event, "Brought nothing")  # Needs no outcome
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 1")
    event = advance_status(admin, seller.event, "pickup")
    assert event.is_closed and event.closed_by == admin


@pytest.mark.django_db
@pytest.mark.parametrize("status", [ItemStatus.AVAILABLE, ItemStatus.MISSING, ItemStatus.ERROR])
def test_closing_blocked_by_open_games(pickup_shop, seller, status):
    set_status(pickup_shop[1:], ItemStatus.RETURNED)
    set_status(pickup_shop[1:2], status)
    assert any(status.label in p for p in payouts.closing_blockers(seller.event))


@pytest.mark.django_db
def test_closing_blocked_by_open_sold_flag(settled, seller, volunteer, admin):
    with pytest.raises(psvc.PickupError):
        psvc.scan_return(seller, volunteer, settled[0].accepted_slip.item_id)
    assert any("Sold flag" in p for p in payouts.closing_blockers(seller.event))


@pytest.mark.django_db
def test_past_event_unaffected_by_next_years_settings(settled, seller, admin, make_event,
                                                      make_seller, stock):
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 1")
    advance_status(admin, seller.event, "pickup")
    seller.refresh_from_db()
    before = fees.figures(seller)
    nxt = make_event("F27A")
    nxt.commission_percent, nxt.unsold_fee_cents = 20, 100
    nxt.save()
    stock(make_seller(nxt, "Next Year"), 3)
    after = fees.figures(seller)
    assert (after.net, after.commission, after.unsold_fee) == (
        before.net, before.commission, before.unsold_fee)


# ---------- Pages and reports ----------


@pytest.mark.django_db
def test_every_report_renders_and_exports(settled, seller, admin, login):
    payouts.record(seller, admin, PayoutOutcome.PAID, "Check 1")
    client = login(admin)
    from apps.consignment.reports import REPORTS

    for slug in REPORTS:
        url = reverse("consignment:report", args=[seller.event.pk, slug])
        assert client.get(url).status_code == 200, slug
        response = client.get(url, {"format": "csv"})
        assert response["Content-Type"].startswith("text/csv"), slug
    body = client.get(reverse("consignment:report", args=[seller.event.pk, "financial"]),
                      {"format": "csv"}).content.decode()
    assert "F26A-001" in body and "4.20" in body and "Check 1" in body
    revenue = client.get(reverse("consignment:report", args=[seller.event.pk, "revenue"]))
    assert b"Balanced." in revenue.content


@pytest.mark.django_db
def test_reports_are_admin_only(settled, seller, volunteer, admin, login):
    set_event_access(admin, volunteer.staff, [seller.event])
    url = reverse("consignment:report", args=[seller.event.pk, "financial"])
    assert login(volunteer).get(url).status_code == 403
    assert login(volunteer).get(reverse("consignment:payouts", args=[seller.event.pk])
                                ).status_code == 403


@pytest.mark.django_db
def test_seller_statement_is_private(settled, seller, make_seller, login):
    other = make_seller(seller.event, "Other")
    url = reverse("consignment:seller_statement", args=[seller.pk])
    page = login(seller.account).get(url).content.decode()
    assert "Net payout" in page and "Game 0" in page and "$8.00" in page
    assert login(other.account).get(url).status_code == 403
    csv_body = login(seller.account).get(url, {"format": "csv"}).content.decode()
    assert "Game 0" in csv_body and "8.00" in csv_body


@pytest.mark.django_db
def test_payout_pages(settled, seller, admin, login):
    client = login(admin)
    page = client.get(reverse("consignment:payouts", args=[seller.event.pk])).content.decode()
    assert seller.seller_id in page and "Record" in page
    url = reverse("consignment:payout_seller", args=[seller.event.pk, seller.pk])
    client.post(url, {"outcome": "paid", "reference": ""})
    seller.refresh_from_db()
    assert not hasattr(seller, "settlement")
    client.post(url, {"outcome": "paid", "reference": "Venmo 55"})
    seller.refresh_from_db()
    assert seller.settlement.payout.reference == "Venmo 55"


@pytest.mark.django_db
def test_pickup_screen_flow(pickup_shop, seller, admin, volunteer, login):
    set_event_access(admin, volunteer.staff, [seller.event])
    set_status(pickup_shop[2:3], ItemStatus.DONATED)
    client = login(volunteer)
    url = reverse("consignment:pickup_screen", args=[seller.event.pk, seller.pk])
    client.post(url, {"action": "scan", "code": pickup_shop[1].accepted_slip.item_id})
    assert Listing.objects.get(pk=pickup_shop[1].pk).status == ItemStatus.RETURNED
    page = client.post(url, {"action": "scan", "code": pickup_shop[2].accepted_slip.item_id},
                       follow=True).content.decode()
    assert "donated" in page and "Seller confirms the donation" in page
    page = client.post(url, {"action": "scan", "code": pickup_shop[3].accepted_slip.item_id},
                       follow=True).content.decode()
    assert "Answer the question" in page
    client.post(url, {"action": "decide", "seller_takes": "yes"})
    assert Listing.objects.get(pk=pickup_shop[2].pk).status == ItemStatus.RETURNED


@pytest.mark.django_db
def test_admin_pages_render(pickup_shop, seller, admin, login):
    client = login(admin)
    for name in ("unsold_scan", "payouts", "reports", "pickup_sellers"):
        assert client.get(reverse(f"consignment:{name}", args=[seller.event.pk])
                          ).status_code == 200, name
    page = client.get(reverse("consignment:item_detail", args=[pickup_shop[1].pk])).content
    assert b"Change status" in page


@pytest.mark.django_db
def test_demo_data_loads_once_and_balances(settings):
    from django.core.management import CommandError, call_command

    from apps.events.models import Event

    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("load_demo")
    call_command("load_demo", "--force")
    past, current = Event.objects.get(code="D25A"), Event.objects.get(code="D26A")
    assert past.is_closed and current.status == "sale"
    assert Listing.objects.filter(seller__event__code__in=["D25A", "D26A"]).count() >= 50
    for event in (past, current):
        assert fees.event_figures(event)[1].balanced
    with pytest.raises(CommandError, match="already"):
        call_command("load_demo", "--force")
