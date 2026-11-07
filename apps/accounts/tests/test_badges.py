import pytest
from django.urls import reverse

from apps.accounts.models import PIN_LOCK_AFTER
from apps.accounts.services import reissue_badge, unlock_badge
from apps.core.models import AuditLog
from apps.core.overrides import OverrideError, OverrideForm, verify_override
from conftest import PIN


def override(admin, pin=PIN, **kwargs):
    kwargs.setdefault("note", "Checked the game")
    return verify_override(admin.staff.admin_id, pin, action="test", **kwargs)


@pytest.mark.django_db
def test_override_is_logged_under_the_admin(admin, volunteer):
    profile = override(admin, requested_by=volunteer)
    assert profile.account == admin
    log = AuditLog.objects.get(action="override.test")
    assert log.actor == admin
    assert log.details == {"note": "Checked the game", "requested_by": volunteer.pk}


@pytest.mark.django_db
def test_badge_scan_ignores_case(admin):
    assert verify_override(admin.staff.admin_id.lower(), PIN, action="test", note="x")


@pytest.mark.django_db
def test_override_does_not_sign_the_admin_in(login, volunteer, admin):
    client = login(volunteer)
    override(admin)
    assert client.session["_auth_user_id"] == str(volunteer.pk)


@pytest.mark.django_db
def test_note_required_where_asked(admin):
    with pytest.raises(OverrideError, match="note"):
        override(admin, note="  ")
    assert override(admin, note="", note_required=False)


@pytest.mark.django_db
def test_five_wrong_pins_lock_the_badge(admin):
    for i in range(PIN_LOCK_AFTER):
        with pytest.raises(OverrideError):
            override(admin, pin="0000")
        admin.staff.refresh_from_db()
        assert admin.staff.failed_pin_count == i + 1
    assert admin.staff.badge_locked
    with pytest.raises(OverrideError, match="locked"):
        override(admin)


@pytest.mark.django_db
def test_correct_pin_resets_the_count(admin):
    with pytest.raises(OverrideError):
        override(admin, pin="0000")
    override(admin)
    admin.staff.refresh_from_db()
    assert admin.staff.failed_pin_count == 0


@pytest.mark.django_db
def test_unlock_and_reissue(admin, make_staff):
    other = make_staff("Bob Board", is_admin=True, is_volunteer=False)
    for _ in range(PIN_LOCK_AFTER):
        with pytest.raises(OverrideError):
            override(admin, pin="0000")
    admin.staff.refresh_from_db()
    unlock_badge(other, admin.staff)
    assert override(admin)
    old = admin.staff.admin_id
    reissue_badge(other, admin.staff)
    with pytest.raises(OverrideError, match="recognized"):
        verify_override(old, PIN, action="test", note="x")
    assert override(admin)


@pytest.mark.django_db
def test_volunteers_and_deactivated_admins_have_no_override(admin, volunteer, make_staff):
    from apps.accounts.services import set_account_active

    other = make_staff("Bob Board", is_admin=True, is_volunteer=False)
    set_account_active(admin, other, False)
    with pytest.raises(OverrideError):
        verify_override(other.staff.admin_id, PIN, action="test", note="x")


@pytest.mark.django_db
def test_admin_without_pin_cannot_override(make_staff):
    other = make_staff("Bob Board", is_admin=True, is_volunteer=False)
    with pytest.raises(OverrideError, match="PIN"):
        verify_override(other.staff.admin_id, "1234", action="test", note="x")


@pytest.mark.django_db
def test_override_form(admin):
    form = OverrideForm({"admin_badge": admin.staff.admin_id, "admin_pin": "0000",
                         "admin_note": "n"}, action="test")
    assert not form.is_valid()
    form = OverrideForm({"admin_badge": admin.staff.admin_id, "admin_pin": PIN,
                         "admin_note": "n"}, action="test")
    assert form.is_valid() and form.admin.account == admin


@pytest.mark.django_db
def test_admin_sets_pin_on_account_page(login, admin):
    from conftest import PASSWORD

    client = login(admin)
    response = client.post(reverse("accounts:my_account"), {
        "pin-current": PASSWORD, "pin-pin": "7391", "pin-pin2": "7391"})
    assert response.status_code == 302
    admin.staff.refresh_from_db()
    assert admin.staff.check_pin("7391")
    response = client.post(reverse("accounts:my_account"), {
        "pin-current": PASSWORD, "pin-pin": "12a4", "pin-pin2": "12a4"})
    assert response.status_code == 200


@pytest.mark.django_db
def test_badge_pdf(login, admin):
    response = login(admin).get(reverse("accounts:staff_badge", args=[admin.staff.pk]))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
