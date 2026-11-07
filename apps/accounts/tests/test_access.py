"""Sign-in is required everywhere, and roles are enforced on the server."""

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

PUBLIC = {"accounts:login", "accounts:login_choose", "accounts:forgot_password",
          "accounts:set_password"}


def all_named_urls():
    def walk(patterns, namespace=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, (namespace + p.namespace + ":") if p.namespace
                                else namespace)
            elif isinstance(p, URLPattern) and p.name:
                yield namespace + p.name, p
    return list(walk(get_resolver().url_patterns))


def build(name, pattern):
    kwargs = {}
    for key in pattern.pattern.converters:
        kwargs[key] = "x" if key in ("uidb64", "token", "action") else 1
    return reverse(name, kwargs=kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize("name,pattern", all_named_urls(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_page_requires_sign_in(client, name, pattern):
    url = build(name, pattern)
    response = client.get(url)
    if name in PUBLIC:
        assert response.status_code in (200, 302, 400)
        if response.status_code == 302:
            assert "/login" in response.url
    else:
        assert response.status_code == 302, url
        assert response.url.startswith(reverse("accounts:login")), url


ADMIN_PAGES = [
    ("accounts:staff_list", {}), ("accounts:staff_new", {}), ("events:new", {}),
]
ADMIN_EVENT_PAGES = ["edit", "windows", "schedule", "advance", "waivers", "seller_new",
                     "staff_sellers", "import", "review"]


@pytest.mark.django_db
def test_volunteer_cannot_open_admin_pages(login, volunteer, event):
    client = login(volunteer)
    for name, kwargs in ADMIN_PAGES:
        assert client.get(reverse(name, kwargs=kwargs)).status_code == 403, name
    for name in ADMIN_EVENT_PAGES:
        url = reverse(f"events:{name}", kwargs={"event_pk": event.pk})
        assert client.get(url).status_code == 403, name
    profile = volunteer.staff
    assert client.get(reverse("accounts:staff_edit", args=[profile.pk])).status_code == 403
    assert client.post(reverse("accounts:staff_action", args=[profile.pk, "unlock-badge"])
                       ).status_code == 403


@pytest.mark.django_db
def test_seller_cannot_open_staff_pages(login, seller, event):
    client = login(seller.account)
    for url in [reverse("events:list"), reverse("events:detail", args=[event.pk]),
                reverse("events:sellers", args=[event.pk]), reverse("accounts:my_account"),
                reverse("accounts:staff_list"), reverse("accounts:seller_edit", args=[seller.pk])]:
        assert client.get(url).status_code == 403, url


@pytest.mark.django_db
def test_volunteer_sees_only_events_with_access(login, admin, volunteer, make_event):
    from apps.accounts.services import set_event_access

    granted, other = make_event("F26A"), make_event("S27B")
    set_event_access(admin, volunteer.staff, [granted])
    client = login(volunteer)
    assert client.get(reverse("events:detail", args=[granted.pk])).status_code == 200
    assert client.get(reverse("events:detail", args=[other.pk])).status_code == 403
    page = client.get(reverse("events:list")).content.decode()
    assert "F26A" in page and "S27B" not in page


@pytest.mark.django_db
def test_revoked_access_is_kept_as_history(admin, volunteer, event, login):
    from apps.accounts.services import set_event_access

    set_event_access(admin, volunteer.staff, [event])
    set_event_access(admin, volunteer.staff, [])
    rows = list(volunteer.staff.event_access.all())
    assert len(rows) == 1 and rows[0].revoked_by == admin
    assert login(volunteer).get(reverse("events:detail", args=[event.pk])).status_code == 403


@pytest.mark.django_db
def test_volunteer_loses_event_access_when_it_closes(login, admin, volunteer, event):
    from apps.accounts.services import set_event_access

    set_event_access(admin, volunteer.staff, [event])
    event.status = "postsale"
    event.save()
    client = login(volunteer)
    assert client.get(reverse("events:detail", args=[event.pk])).status_code == 403
    assert event.code not in client.get(reverse("events:list")).content.decode()
    assert login(admin).get(reverse("events:detail", args=[event.pk])).status_code == 200
