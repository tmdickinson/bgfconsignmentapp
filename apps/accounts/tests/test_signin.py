import re
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import find_accounts, set_account_active
from conftest import PASSWORD

LOGIN = reverse("accounts:login")


def sign_in(client, identifier, password=PASSWORD):
    return client.post(LOGIN, {"identifier": identifier, "password": password})


def signed_in_as(client):
    return client.session.get("_auth_user_id")


@pytest.mark.django_db
def test_staff_sign_in_by_volunteer_id_ignoring_case(client, volunteer):
    response = sign_in(client, volunteer.staff.volunteer_id.lower())
    assert response.status_code == 302
    assert signed_in_as(client) == str(volunteer.pk)


@pytest.mark.django_db
def test_staff_sign_in_by_email(client, admin):
    sign_in(client, "ALICE@example.com")
    assert signed_in_as(client) == str(admin.pk)


@pytest.mark.django_db
def test_seller_sign_in_by_seller_id_and_email(client, seller):
    sign_in(client, seller.seller_id.lower())
    assert signed_in_as(client) == str(seller.account.pk)
    client.logout()
    sign_in(client, seller.email)
    assert signed_in_as(client) == str(seller.account.pk)


@pytest.mark.django_db
def test_seller_id_with_short_number_matches(client, seller):
    sign_in(client, f"{seller.event.code}-1")
    assert signed_in_as(client) == str(seller.account.pk)


@pytest.mark.django_db
def test_wrong_password_fails(client, volunteer):
    response = sign_in(client, volunteer.email, "wrong-password")
    assert response.status_code == 200
    assert signed_in_as(client) is None
    assert b"don&#x27;t match" in response.content


@pytest.mark.django_db
def test_admin_id_is_not_a_sign_in(client, admin):
    sign_in(client, admin.staff.admin_id)
    assert signed_in_as(client) is None


@pytest.mark.django_db
def test_email_matching_several_accounts_asks_which(client, make_staff, make_event, make_seller):
    staff = make_staff("Kim Park", email="kim@example.com")
    s1 = make_seller(make_event("F26A"), name="Kim Park", email="kim@example.com")
    response = sign_in(client, "kim@example.com")
    assert response.url == reverse("accounts:login_choose")
    assert signed_in_as(client) is None
    page = client.get(reverse("accounts:login_choose")).content.decode()
    assert s1.seller_id in page and staff.staff.volunteer_id in page
    client.post(reverse("accounts:login_choose"), {"account": s1.account.pk})
    assert signed_in_as(client) == str(s1.account.pk)


@pytest.mark.django_db
def test_choice_only_offers_accounts_whose_password_matched(client, make_staff, event, make_seller):
    staff = make_staff("Kim Park", email="kim@example.com")
    s1 = make_seller(event, name="Kim Park", email="kim@example.com")
    s1.account.set_password("a-different-password")
    s1.account.save()
    sign_in(client, "kim@example.com")
    assert signed_in_as(client) == str(staff.pk)


@pytest.mark.django_db
def test_choice_cannot_pick_an_account_not_offered(client, make_staff, event, make_seller):
    make_staff("Kim Park", email="kim@example.com")
    make_seller(event, name="Kim Park", email="kim@example.com")
    other = make_seller(event, name="Other", email="other@example.com")
    sign_in(client, "kim@example.com")
    client.post(reverse("accounts:login_choose"), {"account": other.account.pk})
    assert signed_in_as(client) is None


@pytest.mark.django_db
def test_deactivated_account_cannot_sign_in(client, admin, volunteer):
    set_account_active(admin, volunteer, False)
    sign_in(client, volunteer.email)
    assert signed_in_as(client) is None


@pytest.mark.django_db
def test_deactivated_account_loses_its_session(login, admin, volunteer):
    client = login(volunteer)
    set_account_active(admin, volunteer, False)
    assert client.get(reverse("home")).status_code == 302


@pytest.mark.django_db
def test_repeated_failures_are_blocked(client, volunteer, settings):
    for _ in range(settings.LOGIN_FAILURE_LIMIT):
        sign_in(client, volunteer.email, "wrong")
    response = sign_in(client, volunteer.email)
    assert signed_in_as(client) is None
    assert b"Too many failed attempts" in response.content


@pytest.mark.django_db
def test_seller_logins_stop_30_days_after_postsale(client, seller):
    event = seller.event
    event.status, event.moved_to_postsale_at = "postsale", timezone.now() - timedelta(days=29)
    event.save()
    assert find_accounts(seller.seller_id)
    event.moved_to_postsale_at = timezone.now() - timedelta(days=31)
    event.save()
    assert find_accounts(seller.seller_id) == []
    sign_in(client, seller.seller_id)
    assert signed_in_as(client) is None


@pytest.mark.django_db
def test_expired_seller_session_is_ended(login, seller):
    client = login(seller.account)
    event = seller.event
    event.status, event.moved_to_postsale_at = "postsale", timezone.now() - timedelta(days=31)
    event.save()
    response = client.get(reverse("accounts:seller_home", args=[seller.pk]))
    assert response.url == LOGIN
    assert signed_in_as(client) is None


def link_from(message):
    return re.search(r"http://testserver(/password/set/\S+)", message.body).group(1)


@pytest.mark.django_db
def test_new_seller_gets_set_password_email(event, django_capture_on_commit_callbacks):
    from apps.accounts.services import create_seller

    with django_capture_on_commit_callbacks(execute=True):
        seller = create_seller(event, name="New Person", email="new@example.com")
    assert len(mail.outbox) == 1
    assert seller.seller_id in mail.outbox[0].body
    assert "/password/set/" in mail.outbox[0].body


@pytest.mark.django_db
def test_set_password_link_works_once(client, event, django_capture_on_commit_callbacks):
    from apps.accounts.services import create_seller

    with django_capture_on_commit_callbacks(execute=True):
        seller = create_seller(event, name="New Person", email="new@example.com")
    url = link_from(mail.outbox[0])
    new = "a-brand-new-password"
    response = client.post(url, {"password1": new, "password2": new})
    assert response.status_code == 302
    seller.account.refresh_from_db()
    assert seller.account.check_password(new)
    client.logout()
    assert client.get(url).status_code == 400


@pytest.mark.django_db
def test_set_password_rejects_weak_passwords(client, event, django_capture_on_commit_callbacks):
    from apps.accounts.services import create_seller

    with django_capture_on_commit_callbacks(execute=True):
        create_seller(event, name="New Person", email="new@example.com")
    response = client.post(link_from(mail.outbox[0]), {"password1": "short", "password2": "short"})
    assert response.status_code == 200
    assert b"too short" in response.content


@pytest.mark.django_db
def test_reset_email_lists_every_matching_account(client, make_staff, event, make_seller):
    make_staff("Kim Park", email="kim@example.com")
    make_seller(event, name="Kim Park", email="kim@example.com")
    response = client.post(reverse("accounts:forgot_password"), {"identifier": "kim@example.com"})
    assert response.status_code == 200
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body.count("/password/set/") == 2


@pytest.mark.django_db
def test_reset_for_unknown_address_looks_the_same(client):
    response = client.post(reverse("accounts:forgot_password"), {"identifier": "nobody@example.com"})
    assert response.status_code == 200
    assert b"Check your email" in response.content
    assert mail.outbox == []


@pytest.mark.django_db
def test_next_url_must_be_on_this_site(client, volunteer):
    response = client.post(LOGIN + "?next=https://evil.example/",
                           {"identifier": volunteer.email, "password": PASSWORD})
    assert response.url == "/"


@pytest.mark.django_db
def test_password_spaces_are_kept(client, volunteer):
    volunteer.set_password(" spaced password ")
    volunteer.save()
    sign_in(client, volunteer.email, "spaced password")
    assert signed_in_as(client) is None
    sign_in(client, volunteer.email, " spaced password ")
    assert signed_in_as(client) == str(volunteer.pk)
