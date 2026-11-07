import pytest
from django.urls import reverse

from apps.accounts.services import link_seller, set_event_access
from apps.consignment.models import CheckinOutcome, ItemStatus
from apps.consignment.services import checkin as svc
from apps.consignment.services import listings as lsvc
from conftest import PASSWORD, PIN

from .conftest import COMPLETE


@pytest.fixture
def games(seller, make_listing):
    """Three games with slips, and the event moved to Check-in."""
    listings = [make_listing(name=n) for n in ("Wingspan", "Azul", "Catan")]
    lsvc.generate_slips(seller, seller.account)
    for listing in listings:
        listing.refresh_from_db()
    return listings


def open_checkin(event):
    event.status = "checkin"
    event.save()


def back_to_presale(event):
    """For test setup only: listings change only during Presale."""
    event.status = "presale"
    event.save()


@pytest.fixture
def desk(login, admin, volunteer, seller, games):
    """A volunteer's check-in screen for the seller."""
    open_checkin(seller.event)
    set_event_access(admin, volunteer.staff, [seller.event])
    client = login(volunteer)
    url = reverse("consignment:checkin_screen", args=[seller.event.pk, seller.pk])

    def post(action, **data):
        return client.post(url, {"action": action, **data}, follow=True)

    post.client, post.url = client, url
    return post


def item(listing):
    listing.refresh_from_db()
    return listing


@pytest.mark.django_db
def test_scanning_the_next_slip_accepts_the_current_game(desk, games, volunteer):
    a, b, c = games
    page = desk("scan", code=a.current_slip.item_id).content.decode()
    assert "Wingspan" in page and item(a).status == ItemStatus.LISTED
    desk("scan", code=b.current_slip.item_id)
    a = item(a)
    assert a.status == ItemStatus.AVAILABLE and a.accepted_slip == a.current_slip
    assert a.checked_in_by == volunteer and a.checked_in_at is not None
    assert [(s.from_status, s.to_status) for s in a.status_changes.all()][-1] == (
        "listed", "available")
    response = desk("finish")
    assert response.redirect_chain[-1][0].endswith("/report/")
    assert item(b).status == ItemStatus.AVAILABLE
    page = response.content.decode()
    assert "Catan" in page and "Check-in Complete" in page
    assert item(c).status == ItemStatus.LISTED


@pytest.mark.django_db
def test_complete_saves_the_report_for_the_seller(desk, games, seller, login):
    desk("scan", code=games[0].current_slip.item_id)
    desk("finish")
    desk.client.post(reverse("consignment:checkin_report", args=[seller.event.pk, seller.pk]))
    seller.checkin_report.refresh_from_db()
    assert seller.checkin_report.completed_at is not None
    page = login(seller.account).get(
        reverse("consignment:seller_checkin_report", args=[seller.pk])).content.decode()
    assert "Wingspan" in page and "Azul" in page


@pytest.mark.django_db
def test_seller_can_return_with_more_games(desk, games, seller):
    desk("scan", code=games[0].current_slip.item_id)
    desk("finish")
    desk.client.post(reverse("consignment:checkin_report", args=[seller.event.pk, seller.pk]))
    desk("scan", code=games[1].current_slip.item_id)
    desk("finish")
    assert item(games[1]).status == ItemStatus.AVAILABLE
    assert seller.checkin_report.entries.filter(outcome="accepted").count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("typed", ["001-0002", "f26a-001-0002", " F26A-1-2 "])
def test_typed_and_lowercase_codes(desk, games, typed):
    desk("scan", code=typed)
    desk("finish")
    assert item(games[1]).status == ItemStatus.AVAILABLE


@pytest.mark.django_db
@pytest.mark.parametrize("code,outcome", [
    ("F26A-001-0099", CheckinOutcome.NOT_FOUND),
    ("nonsense", CheckinOutcome.NOT_FOUND),
    ("S27B-001-0001", CheckinOutcome.WRONG_EVENT),
])
def test_unknown_and_wrong_event_codes_are_not_accepted(desk, seller, code, outcome):
    page = desk("scan", code=code).content.decode()
    assert "Not accepted" in page
    entry = seller.checkin_report.entries.get()
    assert entry.outcome == outcome and entry.listing is None


@pytest.mark.django_db
def test_wrong_event_even_when_the_item_exists(desk, make_event, make_seller, make_listing, seller):
    other_event = make_event("S27B")
    other = make_seller(other_event, "Other Year")
    make_listing(owner=other)
    slip = lsvc.generate_slips(other, other.account)[0]
    desk("scan", code=slip.item_id)
    assert seller.checkin_report.entries.get().outcome == CheckinOutcome.WRONG_EVENT
    assert item(slip.listing).status == ItemStatus.LISTED


@pytest.mark.django_db
def test_another_sellers_game_is_not_accepted(desk, seller, make_seller, make_listing):
    back_to_presale(seller.event)
    other = make_seller(seller.event, "Other")
    make_listing(owner=other)
    slip = lsvc.generate_slips(other, other.account)[0]
    open_checkin(seller.event)
    desk("scan", code=slip.item_id)
    desk("finish")
    assert item(slip.listing).status == ItemStatus.LISTED
    assert seller.checkin_report.entries.get().outcome == CheckinOutcome.OTHER_SELLER


@pytest.mark.django_db
def test_reject_deactivates_with_a_reason(desk, games, seller):
    desk("scan", code=games[0].current_slip.item_id)
    desk("reject", reason="")
    assert item(games[0]).status == ItemStatus.LISTED
    desk("reject", reason="Different edition")
    game = item(games[0])
    assert game.status == ItemStatus.DEACTIVATED and not game.is_checked_in
    entry = seller.checkin_report.entries.get()
    assert entry.outcome == CheckinOutcome.REJECTED and entry.reason == "Different edition"
    assert "Different edition" in desk("finish").content.decode()


@pytest.fixture
def outdated(seller, games, prices):
    """Wingspan's first slip outdated by a price change, with a new slip printed."""
    back_to_presale(seller.event)
    game = games[0]
    old = game.current_slip
    lsvc.deactivate(game, seller.account)
    lsvc.reactivate(game, seller.account, {**COMPLETE, "name": "Wingspan"}, prices(25, 20, 10))
    new = lsvc.generate_slips(seller, seller.account)[0]
    open_checkin(seller.event)
    return old, new


@pytest.mark.django_db
def test_outdated_slip_needs_confirmation(desk, games, outdated, seller):
    old, new = outdated
    page = desk("scan", code=old.item_id).content.decode()
    assert "outdated" in page and "$30.00" in page and "$25.00" in page
    page = desk("scan", code=games[1].current_slip.item_id).content.decode()
    assert "Decide on the current game first" in page
    assert item(games[1]).status == ItemStatus.LISTED
    desk("confirm")
    game = item(games[0])
    assert game.status == ItemStatus.AVAILABLE and game.accepted_slip == old
    assert seller.checkin_report.entries.get().outcome == CheckinOutcome.OUTDATED_ACCEPTED
    # The listing is checked in once, whichever slip version is scanned.
    page = desk("scan", code=new.item_id).content.decode()
    assert "already checked in" in page


@pytest.mark.django_db
def test_deactivated_listing_is_reactivated_on_acceptance(desk, games, seller):
    game = games[0]
    back_to_presale(seller.event)
    lsvc.deactivate(game, seller.account)
    open_checkin(seller.event)
    assert "listing is deactivated" in desk("scan", code=game.current_slip.item_id).content.decode()
    desk("confirm")
    game = item(game)
    assert game.status == ItemStatus.AVAILABLE
    assert game.status_changes.last().from_status == ItemStatus.DEACTIVATED
    assert seller.checkin_report.entries.get().outcome == CheckinOutcome.REACTIVATED


@pytest.mark.django_db
def test_outdated_and_deactivated_gives_one_combined_warning(desk, games, outdated, seller):
    old, _ = outdated
    back_to_presale(seller.event)
    lsvc.deactivate(games[0], seller.account)
    open_checkin(seller.event)
    page = desk("scan", code=old.item_id).content.decode()
    assert "outdated, and the game's listing is deactivated" in page
    desk("reject", reason="Seller doesn't agree")
    assert item(games[0]).status == ItemStatus.DEACTIVATED


@pytest.mark.django_db
def test_double_scan_changes_nothing(desk, games, seller):
    code = games[0].current_slip.item_id
    desk("scan", code=code)
    desk("finish")
    assert "already checked in" in desk("scan", code=code).content.decode()
    desk("double")
    assert item(games[0]).status == ItemStatus.AVAILABLE
    assert seller.checkin_report.entries.last().outcome == CheckinOutcome.DOUBLE_SCAN


@pytest.fixture
def checked_in_again(desk, games):
    code = games[0].current_slip.item_id
    desk("scan", code=code)
    desk("finish")
    desk("scan", code=code)
    return games[0]


@pytest.mark.django_db
def test_two_games_with_one_slip_needs_an_admin(desk, checked_in_again, admin, seller):
    badge = admin.staff.admin_id
    page = desk("duplicate", **{"dup-correct_game": "second", "dup-admin_badge": badge,
                                "dup-admin_pin": "0000", "dup-admin_note": "x"}).content.decode()
    assert "Wrong PIN" in page
    assert not seller.checkin_report.entries.filter(outcome="duplicate_slip").exists()
    desk("duplicate", **{"dup-correct_game": "second", "dup-admin_badge": badge,
                         "dup-admin_pin": PIN, "dup-admin_note": "Kept the sleeved copy"})
    entry = seller.checkin_report.entries.get(outcome="duplicate_slip")
    assert entry.approved_by_admin == admin and "sleeved" in entry.reason
    assert item(checked_in_again).status == ItemStatus.AVAILABLE


@pytest.mark.django_db
def test_other_checked_in_scans_stop_until_admin_override(desk, checked_in_again, admin, games):
    page = desk("scan", code=games[1].current_slip.item_id).content.decode()
    assert "Decide on the current game first" in page
    desk("override", **{"ovr-outcome": "error", "ovr-admin_badge": admin.staff.admin_id,
                        "ovr-admin_pin": PIN, "ovr-admin_note": "Slip found on wrong box"})
    game = item(checked_in_again)
    assert game.status == ItemStatus.ERROR
    assert game.status_changes.last().approved_by_admin == admin


@pytest.mark.django_db
def test_override_note_is_required(desk, checked_in_again, admin):
    desk("override", **{"ovr-outcome": "keep", "ovr-admin_badge": admin.staff.admin_id,
                        "ovr-admin_pin": PIN, "ovr-admin_note": ""})
    assert not checked_in_again.checkin_entries.filter(outcome="override").exists()


@pytest.mark.django_db
def test_staff_cannot_check_in_their_own_games(login, admin, volunteer, seller, games):
    link_seller(volunteer, seller.seller_id, PASSWORD)
    open_checkin(seller.event)
    set_event_access(admin, volunteer.staff, [seller.event])
    url = reverse("consignment:checkin_screen", args=[seller.event.pk, seller.pk])
    assert login(volunteer).get(url).status_code == 403
    page = login(volunteer).get(reverse("consignment:checkin_sellers", args=[seller.event.pk]))
    assert "Your own games" in page.content.decode()
    assert login(admin).get(url).status_code == 200


@pytest.mark.django_db
def test_admins_cannot_check_in_their_own_games_either(login, admin, seller, games):
    link_seller(admin, seller.seller_id, PASSWORD)
    open_checkin(seller.event)
    url = reverse("consignment:checkin_screen", args=[seller.event.pk, seller.pk])
    assert login(admin).get(url).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["presale", "sale"])
def test_checkin_only_during_checkin(seller, games, admin, status):
    seller.event.status = status
    seller.event.save()
    report = svc.get_report(seller, admin)
    with pytest.raises(svc.CheckinError):
        svc.scan(report, admin, games[0].current_slip.item_id)
    with pytest.raises(svc.CheckinError):
        svc.accept(report, admin, games[0].current_slip)


@pytest.mark.django_db
def test_listing_is_checked_in_only_once(seller, games, admin):
    open_checkin(seller.event)
    report = svc.get_report(seller, admin)
    svc.accept(report, admin, games[0].current_slip)
    with pytest.raises(svc.CheckinError):
        svc.accept(report, admin, games[0].current_slip)
    with pytest.raises(svc.CheckinError):
        svc.reject(report, admin, games[0].current_slip, "Too late")


@pytest.mark.django_db
def test_database_requires_an_accepted_slip_for_checked_in_games(games):
    from django.db import IntegrityError
    from django.utils import timezone

    game = games[0]
    game.checked_in_at = timezone.now()
    with pytest.raises(IntegrityError):
        game.save()


@pytest.mark.django_db
def test_volunteer_needs_event_access(login, volunteer, seller, games):
    open_checkin(seller.event)
    for url in [reverse("consignment:checkin_sellers", args=[seller.event.pk]),
                reverse("consignment:checkin_screen", args=[seller.event.pk, seller.pk]),
                reverse("consignment:event_items", args=[seller.event.pk])]:
        assert login(volunteer).get(url).status_code == 403


@pytest.mark.django_db
def test_seller_lookup_by_seller_id_jumps_to_screen(login, admin, seller, games):
    open_checkin(seller.event)
    response = login(admin).get(reverse("consignment:checkin_sellers", args=[seller.event.pk]),
                                {"q": seller.seller_id.lower()})
    assert response.url == reverse("consignment:checkin_screen",
                                   args=[seller.event.pk, seller.pk])


@pytest.mark.django_db
def test_item_browse_and_search(login, admin, seller, games):
    client = login(admin)
    url = reverse("consignment:event_items", args=[seller.event.pk])
    page = client.get(url, {"q": "001-0002"}).content.decode()
    assert "Azul" in page and "Catan" not in page
    page = client.get(url, {"q": "cat"}).content.decode()
    assert "Catan" in page and "Azul" not in page
    page = client.get(url, {"q": seller.seller_id}).content.decode()
    assert all(n in page for n in ("Wingspan", "Azul", "Catan"))
    open_checkin(seller.event)
    svc.accept(svc.get_report(seller, admin), admin, games[0].current_slip)
    page = client.get(url, {"status": "available"}).content.decode()
    assert "Wingspan" in page and "Azul" not in page
