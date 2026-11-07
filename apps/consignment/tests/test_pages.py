import pytest
from django.urls import reverse

from apps.consignment import views
from apps.consignment.models import ItemStatus, Listing
from apps.consignment.services import listings as svc

from .conftest import form_data


def seller_urls(seller, listing):
    return [
        ("get", reverse("consignment:listing_detail", args=[seller.pk, listing.pk])),
        ("get", reverse("consignment:listing_edit", args=[seller.pk, listing.pk])),
        ("post", reverse("consignment:listing_deactivate", args=[seller.pk, listing.pk])),
        ("get", reverse("consignment:listing_reactivate", args=[seller.pk, listing.pk])),
    ]


@pytest.mark.django_db
def test_sellers_cannot_reach_each_others_games(login, event, seller, make_seller, make_listing):
    other = make_seller(event, "Other")
    theirs = make_listing(owner=other)
    svc.generate_slips(other, other.account)
    client = login(seller.account)
    # Their listing through my seller URL: not found.
    for method, url in seller_urls(seller, theirs):
        assert getattr(client, method)(url).status_code == 404, url
    # Their listing through their seller URL: not allowed.
    for method, url in seller_urls(other, theirs):
        assert getattr(client, method)(url).status_code == 403, url
    for url in [reverse("consignment:listing_new", args=[other.pk]),
                reverse("consignment:slips_generate", args=[other.pk])]:
        assert client.post(url, form_data(event)).status_code == 403
    theirs.refresh_from_db()
    assert theirs.status == ItemStatus.LISTED and Listing.objects.filter(seller=other).count() == 1


@pytest.mark.django_db
def test_cannot_print_another_sellers_slips(login, event, seller, make_seller, make_listing):
    other = make_seller(event, "Other")
    make_listing(owner=other)
    slip = svc.generate_slips(other, other.account)[0]
    response = login(seller.account).get(
        reverse("consignment:slips_print", args=[seller.pk]), {"slip": slip.pk})
    assert response.status_code == 302


@pytest.fixture
def printed(monkeypatch):
    """The slips passed to the PDF builder, since the PDF text itself is compressed."""
    captured = []
    real = views.slips_pdf

    def capture(slips):
        captured[:] = slips
        return real(slips)

    monkeypatch.setattr(views, "slips_pdf", capture)
    return captured


@pytest.mark.django_db
def test_generate_then_print(login, seller, make_listing, printed):
    make_listing()
    make_listing(name="Azul", slip_size="small", donate_if_unsold=True)
    client = login(seller.account)
    client.post(reverse("consignment:slips_generate", args=[seller.pk]))
    page = client.get(reverse("accounts:seller_home", args=[seller.pk])).content.decode()
    assert "Open the new slips to print" in page
    slips = [lst.current_slip for lst in seller.listings.all()]
    response = client.get(reverse("consignment:slips_print", args=[seller.pk]),
                          {"slip": [s.pk for s in slips]})
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert sorted(s.item_id for s in printed) == ["F26A-001-0001", "F26A-001-0002"]
    assert {s.size for s in printed} == {"large", "small"}


@pytest.mark.django_db
def test_outdated_slips_are_not_printed(login, seller, make_listing, prices):
    from .conftest import COMPLETE

    listing = make_listing()
    old = svc.generate_slips(seller, seller.account)[0]
    svc.deactivate(listing, seller.account)
    svc.reactivate(listing, seller.account, {**COMPLETE, "name": "Wingspan 2"}, prices())
    response = login(seller.account).get(
        reverse("consignment:slips_print", args=[seller.pk]), {"slip": old.pk})
    assert response.status_code == 302


@pytest.mark.django_db
@pytest.mark.parametrize("status,allowed", [("presale", True), ("checkin", True),
                                            ("sale", False), ("setup", False)])
def test_reprints_through_end_of_checkin(login, seller, make_listing, status, allowed):
    make_listing()
    slip = svc.generate_slips(seller, seller.account)[0]
    seller.event.status = status
    seller.event.save()
    response = login(seller.account).get(
        reverse("consignment:slips_print", args=[seller.pk]), {"slip": slip.pk})
    assert (response.status_code == 200) == allowed


@pytest.mark.django_db
def test_reactivate_with_changes_through_the_edit_page(login, seller, make_listing):
    listing = make_listing()
    svc.generate_slips(seller, seller.account)
    client = login(seller.account)
    client.post(reverse("consignment:listing_deactivate", args=[seller.pk, listing.pk]))
    assert "Make changes" in client.get(
        reverse("consignment:listing_reactivate", args=[seller.pk, listing.pk])).content.decode()
    client.post(reverse("consignment:listing_edit", args=[seller.pk, listing.pk]),
                form_data(seller.event, condition="new"))
    listing.refresh_from_db()
    assert listing.status == ItemStatus.LISTED and listing.current_slip is None


@pytest.mark.django_db
def test_seller_pages_render(login, seller, make_listing):
    listing = make_listing(name="", publisher="")
    done = make_listing(name="Done")
    svc.deactivate(listing, seller.account)
    svc.generate_slips(seller, seller.account)
    svc.deactivate(done, seller.account)
    client = login(seller.account)
    page = client.get(reverse("accounts:seller_home", args=[seller.pk])).content.decode()
    assert "Deactivated games" in page and "F26A-001-0001" in page
    for url in [reverse("consignment:listing_new", args=[seller.pk]),
                reverse("consignment:listing_detail", args=[seller.pk, done.pk]),
                reverse("consignment:listing_reactivate", args=[seller.pk, done.pk]),
                reverse("consignment:listing_edit", args=[seller.pk, done.pk])]:
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_staff_item_page(login, admin, volunteer, seller, make_listing):
    from apps.accounts.services import set_event_access

    listing = make_listing()
    url = reverse("consignment:item_detail", args=[listing.pk])
    assert login(admin).get(url).status_code == 200
    assert login(volunteer).get(url).status_code == 403
    set_event_access(admin, volunteer.staff, [seller.event])
    assert login(volunteer).get(url).status_code == 200
    assert login(seller.account).get(url).status_code == 403
    assert "Wingspan" in login(admin).get(
        reverse("accounts:seller_edit", args=[seller.pk])).content.decode()


@pytest.mark.django_db
def test_sample_slips_for_admins(login, admin, volunteer, event, printed):
    url = reverse("consignment:sample_slips", args=[event.pk])
    response = login(admin).get(url)
    assert response.content.startswith(b"%PDF") and printed[0].item_id == "F26A-999-0001"
    assert login(volunteer).get(url).status_code == 403
