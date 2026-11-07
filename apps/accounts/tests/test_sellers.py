from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import LinkError, link_seller
from conftest import PASSWORD


@pytest.mark.django_db
def test_seller_numbers_are_sequential_per_event(make_event, make_seller):
    a, b = make_event("F26A"), make_event("S27B")
    s1, s2 = make_seller(a, "One"), make_seller(a, "Two")
    s3 = make_seller(b, "Three")
    assert (s1.seller_id, s2.seller_id, s3.seller_id) == ("F26A-001", "F26A-002", "S27B-001")


@pytest.mark.django_db
def test_seller_email_unique_within_event_only(make_event, make_seller):
    from apps.accounts.services import DuplicateSeller

    a, b = make_event("F26A"), make_event("S27B")
    make_seller(a, "One", email="same@example.com")
    with pytest.raises(DuplicateSeller):
        make_seller(a, "Again", email="SAME@example.com")
    assert make_seller(b, "Next year", email="same@example.com")


@pytest.mark.django_db
def test_seller_cannot_open_another_sellers_pages(login, event, make_seller):
    mine, theirs = make_seller(event, "Mine"), make_seller(event, "Theirs")
    client = login(mine.account)
    assert client.get(reverse("accounts:seller_home", args=[mine.pk])).status_code == 200
    for name in ("accounts:seller_home", "accounts:seller_rules"):
        assert client.get(reverse(name, args=[theirs.pk])).status_code == 403
    assert client.post(reverse("accounts:seller_rules", args=[theirs.pk]),
                       {"agree": "1"}).status_code == 403


@pytest.mark.django_db
def test_home_sends_seller_to_their_page(login, seller):
    response = login(seller.account).get(reverse("home"))
    assert response.url == reverse("accounts:seller_home", args=[seller.pk])


@pytest.mark.django_db
def test_rules_must_be_accepted_first(login, event, make_seller):
    seller = make_seller(event, accepted=False)
    client = login(seller.account)
    home = reverse("accounts:seller_home", args=[seller.pk])
    rules = reverse("accounts:seller_rules", args=[seller.pk])
    assert client.get(home).url == rules
    page = client.get(rules).content.decode()
    assert "10%" in page and "$2.00" in page and "$0.25" in page
    client.post(rules, {})
    seller.refresh_from_db()
    assert seller.rules_accepted_at is None
    client.post(rules, {"agree": "1"})
    seller.refresh_from_db()
    assert seller.rules_accepted_at is not None
    assert client.get(home).status_code == 200


@pytest.mark.django_db
def test_staff_links_own_seller_account(login, volunteer, seller):
    client = login(volunteer)
    response = client.post(reverse("accounts:my_account"), {
        "link-seller_id": seller.seller_id.lower(), "link-password": PASSWORD})
    assert response.status_code == 302
    seller.refresh_from_db()
    assert seller.linked_staff == volunteer.staff
    assert client.get(reverse("accounts:seller_home", args=[seller.pk])).status_code == 200


@pytest.mark.django_db
def test_linking_needs_the_seller_password(volunteer, seller):
    with pytest.raises(LinkError):
        link_seller(volunteer, seller.seller_id, "wrong")
    with pytest.raises(LinkError):
        link_seller(volunteer, "F26A-999", PASSWORD)
    seller.refresh_from_db()
    assert seller.linked_staff is None


@pytest.mark.django_db
def test_linking_is_slowed_after_failures(login, volunteer, seller, settings):
    client = login(volunteer)
    for _ in range(settings.LOGIN_FAILURE_LIMIT):
        client.post(reverse("accounts:my_account"),
                    {"link-seller_id": seller.seller_id, "link-password": "wrong"})
    response = client.post(reverse("accounts:my_account"),
                           {"link-seller_id": seller.seller_id, "link-password": PASSWORD})
    assert b"Too many" in response.content
    seller.refresh_from_db()
    assert seller.linked_staff is None


@pytest.mark.django_db
def test_one_link_per_seller_and_per_staff_per_event(make_staff, event, make_seller):
    a, b = make_staff("Ann Able"), make_staff("Ben Baker")
    s1, s2 = make_seller(event, "One"), make_seller(event, "Two")
    link_seller(a, s1.seller_id, PASSWORD)
    with pytest.raises(LinkError, match="someone else"):
        link_seller(b, s1.seller_id, PASSWORD)
    with pytest.raises(LinkError, match="already have"):
        link_seller(a, s2.seller_id, PASSWORD)


@pytest.mark.django_db
def test_cannot_link_to_a_closed_event(volunteer, seller):
    seller.event.status = "postsale"
    seller.event.save()
    with pytest.raises(LinkError, match="closed"):
        link_seller(volunteer, seller.seller_id, PASSWORD)


@pytest.mark.django_db
def test_staff_cannot_open_unlinked_seller(login, volunteer, admin, seller):
    for account in (volunteer, admin):
        response = login(account).get(reverse("accounts:seller_home", args=[seller.pk]))
        assert response.status_code == 403


@pytest.mark.django_db
def test_linked_volunteer_loses_access_after_30_days(login, volunteer, seller):
    link_seller(volunteer, seller.seller_id, PASSWORD)
    event = seller.event
    event.status, event.moved_to_postsale_at = "postsale", timezone.now() - timedelta(days=31)
    event.save()
    response = login(volunteer).get(reverse("accounts:seller_home", args=[seller.pk]))
    assert response.status_code == 403


@pytest.mark.django_db
def test_admins_list_staff_who_are_sellers(login, admin, volunteer, seller, make_seller):
    make_seller(seller.event, "Not Staff")
    link_seller(volunteer, seller.seller_id, PASSWORD)
    page = login(admin).get(reverse("events:staff_sellers", args=[seller.event.pk])).content.decode()
    assert seller.seller_id in page and "Val Volunteer" in page and "Not Staff" not in page


@pytest.mark.django_db
def test_volunteer_search_sellers(login, admin, volunteer, event, make_seller):
    from apps.accounts.services import set_event_access

    make_seller(event, "Pat Jones")
    make_seller(event, "Lee Smith")
    set_event_access(admin, volunteer.staff, [event])
    client = login(volunteer)
    page = client.get(reverse("events:sellers", args=[event.pk]), {"q": "smith"}).content.decode()
    assert "Lee Smith" in page and "Pat Jones" not in page
    page = client.get(reverse("events:sellers", args=[event.pk]), {"q": "f26a-001"}).content.decode()
    assert "Pat Jones" in page and "Lee Smith" not in page
    assert "pat.jones@example.com" not in page  # Emails are for admins only
