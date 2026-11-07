import pytest
from django.urls import reverse

from apps.consignment.models import (
    ImmutableError,
    ItemStatus,
    Listing,
    SlipPrice,
    SlipVersion,
)
from apps.consignment.services import listings as svc
from apps.events.models import Status
from apps.events.services import EventError, update_windows

from .conftest import COMPLETE, form_data


def generate(seller):
    return svc.generate_slips(seller, seller.account)


@pytest.mark.django_db
def test_seller_adds_a_game(login, seller):
    client = login(seller.account)
    response = client.post(reverse("consignment:listing_new", args=[seller.pk]),
                           form_data(seller.event))
    assert response.status_code == 302
    listing = Listing.objects.get(seller=seller)
    assert listing.name == "Wingspan" and listing.status == ItemStatus.LISTED
    assert [p.dollars for p in listing.prices.all()] == [30, 20, 10]
    assert listing.status_changes.get().to_status == ItemStatus.LISTED


@pytest.mark.django_db
def test_incomplete_listing_can_be_saved(login, seller):
    client = login(seller.account)
    client.post(reverse("consignment:listing_new", args=[seller.pk]),
                {"name": "Draft", "slip_size": "small"})
    listing = Listing.objects.get()
    assert "publisher" in svc.problems(listing)
    assert any(p.startswith("price for Friday") for p in svc.problems(listing))


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["0", "-5", "1.5", "ten"])
def test_prices_must_be_whole_dollars_of_at_least_one(login, seller, value):
    client = login(seller.account)
    window = seller.event.windows.first()
    response = client.post(reverse("consignment:listing_new", args=[seller.pk]),
                           form_data(seller.event, **{f"price_{window.pk}": value}))
    assert response.status_code == 200
    assert not Listing.objects.exists()


@pytest.mark.django_db
def test_prices_can_be_in_any_order(seller, prices):
    listing = svc.create_listing(seller, seller.account, COMPLETE, prices(5, 40, 1))
    assert svc.problems(listing) == []


@pytest.mark.django_db
def test_database_rejects_prices_under_a_dollar(seller, make_listing):
    from django.db import IntegrityError

    listing = make_listing()
    price = listing.prices.first()
    price.dollars = 0
    with pytest.raises(IntegrityError):
        price.save()


@pytest.mark.django_db
def test_fair_condition_needs_details(make_listing):
    listing = make_listing(condition="fair")
    assert "condition details" in svc.problems(listing)
    listing.condition_note = "Missing a die"
    listing.save()
    assert svc.problems(listing) == []


@pytest.mark.django_db
def test_bundle_needs_a_count(make_listing):
    assert "number of items in the bundle" in svc.problems(make_listing(is_bundle=True))
    assert svc.problems(make_listing(is_bundle=True, bundle_count=3)) == []


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Status.SETUP, Status.CHECKIN, Status.SALE])
def test_listings_change_only_in_presale(seller, make_listing, status, login):
    listing = make_listing()
    seller.event.status = status
    seller.event.save()
    with pytest.raises(svc.ListingError):
        svc.create_listing(seller, seller.account, COMPLETE, {})
    with pytest.raises(svc.ListingError):
        svc.update_listing(listing, seller.account, COMPLETE, {})
    with pytest.raises(svc.ListingError):
        svc.deactivate(listing, seller.account)
    with pytest.raises(svc.ListingError):
        generate(seller)
    client = login(seller.account)
    client.post(reverse("consignment:listing_new", args=[seller.pk]), form_data(seller.event))
    assert Listing.objects.count() == 1


@pytest.mark.django_db
def test_generation_requires_every_active_listing_complete(seller, make_listing):
    make_listing()
    draft = make_listing(name="", publisher="")
    with pytest.raises(svc.ListingError, match="Untitled"):
        generate(seller)
    assert not SlipVersion.objects.exists()
    svc.deactivate(draft, seller.account)
    assert len(generate(seller)) == 1


@pytest.mark.django_db
def test_item_ids_are_sequential_per_seller(seller, make_listing, event, make_seller):
    make_listing()
    make_listing(name="Azul")
    slips = generate(seller)
    assert [s.item_id for s in slips] == ["F26A-001-0001", "F26A-001-0002"]
    other = make_seller(event, "Other")
    make_listing(owner=other)
    assert svc.generate_slips(other, other.account)[0].item_id == "F26A-002-0001"


@pytest.mark.django_db
def test_generation_covers_only_listings_without_a_valid_slip(seller, make_listing):
    make_listing()
    generate(seller)
    assert generate(seller) == []
    make_listing(name="Azul")
    assert [s.name for s in generate(seller)] == ["Azul"]


@pytest.mark.django_db
def test_slip_freezes_what_was_printed(seller, make_listing):
    listing = make_listing()
    slip = generate(seller)[0]
    assert (slip.name, slip.condition, slip.slip_size) == ("Wingspan", "good", "large")
    assert [p.price_cents for p in slip.prices.all()] == [3000, 2000, 1000]
    listing.refresh_from_db()
    assert listing.current_slip == slip


@pytest.mark.django_db
def test_listing_with_a_slip_cannot_be_edited(seller, make_listing):
    listing = make_listing()
    generate(seller)
    listing.refresh_from_db()
    with pytest.raises(svc.ListingError, match="Deactivate"):
        svc.update_listing(listing, seller.account, {**COMPLETE, "name": "New"}, {})


@pytest.mark.django_db
def test_deactivate_and_reactivate_without_changes_keeps_the_item_id(seller, make_listing):
    listing = make_listing()
    slip = generate(seller)[0]
    svc.deactivate(listing, seller.account)
    listing.refresh_from_db()
    assert listing.status == ItemStatus.DEACTIVATED and listing.current_slip == slip
    svc.reactivate(listing, seller.account)
    listing.refresh_from_db()
    assert listing.status == ItemStatus.LISTED and listing.current_slip == slip
    assert generate(seller) == []
    assert [c.to_status for c in listing.status_changes.all()] == [
        "listed", "deactivated", "listed"]


@pytest.mark.django_db
def test_any_change_on_reactivation_invalidates_the_slip(seller, make_listing, prices):
    listing = make_listing()
    old = generate(seller)[0]
    svc.deactivate(listing, seller.account)
    svc.reactivate(listing, seller.account, COMPLETE, prices(30, 20, 9))
    listing.refresh_from_db()
    old.refresh_from_db()
    assert listing.current_slip is None and old.invalidated_at is not None
    new = generate(seller)[0]
    assert new.item_id == "F26A-001-0002" and new.sequence == 2
    assert [p.price_cents for p in old.prices.all()] == [3000, 2000, 1000]


@pytest.mark.django_db
def test_reactivating_with_identical_details_keeps_the_slip(seller, make_listing, prices):
    listing = make_listing()
    slip = generate(seller)[0]
    svc.deactivate(listing, seller.account)
    svc.reactivate(listing, seller.account, COMPLETE, prices())
    listing.refresh_from_db()
    assert listing.current_slip == slip


@pytest.mark.django_db
def test_bgg_id_change_does_not_invalidate(seller, make_listing, prices):
    listing = make_listing()
    slip = generate(seller)[0]
    svc.deactivate(listing, seller.account)
    svc.reactivate(listing, seller.account, {**COMPLETE, "bgg_id": 266192}, prices())
    listing.refresh_from_db()
    assert listing.current_slip == slip and listing.bgg_id == 266192


@pytest.mark.django_db
def test_slip_history_is_immutable(seller, make_listing):
    make_listing()
    slip = generate(seller)[0]
    slip.name = "Changed"
    with pytest.raises(ImmutableError):
        slip.save()
    with pytest.raises(ImmutableError):
        slip.delete()
    price = slip.prices.first()
    price.price_cents = 100
    with pytest.raises(ImmutableError):
        price.save()
    with pytest.raises(ImmutableError):
        SlipPrice.objects.first().delete()
    slip.refresh_from_db()
    assert slip.name == "Wingspan"


@pytest.mark.django_db
def test_windows_lock_once_a_game_is_listed(admin, event, make_listing):
    rows = [{"name": w.name} for w in event.windows.all()]
    update_windows(admin, event, rows + [{"name": "Monday"}])
    update_windows(admin, event, rows)
    make_listing()
    with pytest.raises(EventError, match="locked"):
        update_windows(admin, event, rows + [{"name": "Monday"}])
    with pytest.raises(EventError, match="locked"):
        update_windows(admin, event, [{"name": "Fri"}, *rows[1:]])
    update_windows(admin, event, [{**r, "scheduled_start": None} for r in rows])


@pytest.mark.django_db
def test_stale_listing_object_cannot_undo_a_slip(seller, make_listing, prices):
    stale = make_listing()
    slip = generate(seller)[0]
    svc.deactivate(stale, seller.account)
    svc.reactivate(stale, seller.account, COMPLETE, prices())
    stale.refresh_from_db()
    assert stale.current_slip == slip
