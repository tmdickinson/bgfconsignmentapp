import pytest
from django.core import mail
from django.urls import reverse

from apps.accounts.models import StaffProfile
from apps.accounts.services import AccountError, update_staff
from apps.core.models import AuditLog


@pytest.mark.django_db
def test_admin_adds_volunteer_with_event_access(login, admin, event,
                                                django_capture_on_commit_callbacks):
    client = login(admin)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("accounts:staff_new"), {
            "name": "Kim Park", "email": "kim@example.com", "is_volunteer": "on",
            "events": [event.pk]})
    assert response.status_code == 302
    profile = StaffProfile.objects.get(name="Kim Park")
    assert profile.volunteer_id.startswith("VOL-KP")
    assert profile.admin_id is None
    assert profile.event_access.filter(event=event, revoked_at__isnull=True).exists()
    assert len(mail.outbox) == 1 and profile.volunteer_id in mail.outbox[0].body
    assert not profile.account.has_usable_password()
    assert AuditLog.objects.filter(action="staff.created", actor=admin).exists()


@pytest.mark.django_db
def test_staff_needs_a_role(login, admin):
    response = login(admin).post(reverse("accounts:staff_new"),
                                 {"name": "No Role", "email": "n@example.com"})
    assert response.status_code == 200
    assert not StaffProfile.objects.filter(name="No Role").exists()


@pytest.mark.django_db
def test_volunteer_id_is_permanent(admin, volunteer):
    profile = volunteer.staff
    original = profile.volunteer_id
    update_staff(admin, profile, name="Renamed Person", email=volunteer.email, is_admin=False,
                 is_volunteer=True)
    assert profile.volunteer_id == original
    update_staff(admin, profile, name="Renamed Person", email=volunteer.email, is_admin=True,
                 is_volunteer=False)
    update_staff(admin, profile, name="Renamed Person", email=volunteer.email, is_admin=False,
                 is_volunteer=True)
    profile.refresh_from_db()
    assert profile.volunteer_id == original


@pytest.mark.django_db
def test_making_someone_admin_issues_a_badge(admin, volunteer):
    profile = update_staff(admin, volunteer.staff, name="Val Volunteer", email=volunteer.email,
                           is_admin=True, is_volunteer=True)
    assert profile.admin_id.startswith("ADM-")
    assert profile.badge_issued_at is not None


@pytest.mark.django_db
def test_admin_cannot_remove_own_admin_role_or_deactivate_self(admin):
    from apps.accounts.services import set_account_active

    with pytest.raises(AccountError):
        update_staff(admin, admin.staff, name="Alice", email=admin.email, is_admin=False,
                     is_volunteer=True)
    with pytest.raises(AccountError):
        set_account_active(admin, admin, False)


@pytest.mark.django_db
def test_staff_with_no_role_cannot_sign_in(client, admin, volunteer):
    from apps.accounts.services import find_accounts

    StaffProfile.objects.filter(pk=volunteer.staff.pk).update(is_volunteer=False)
    assert find_accounts(volunteer.email) == []


@pytest.mark.django_db
def test_first_admin_command(db, monkeypatch, capsys):
    from django.core.management import CommandError, call_command

    passwords = iter(["a-strong-pass-phrase", "a-strong-pass-phrase"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(passwords))
    call_command("create_first_admin", name="First Admin", email="first@example.com")
    profile = StaffProfile.objects.get(name="First Admin")
    assert profile.is_admin and profile.account.check_password("a-strong-pass-phrase")
    with pytest.raises(CommandError):
        call_command("create_first_admin", name="Second", email="second@example.com")


@pytest.mark.django_db
def test_first_admin_command_rejects_weak_password(db, monkeypatch):
    from django.core.management import CommandError, call_command

    monkeypatch.setattr("getpass.getpass", lambda prompt="": "password")
    with pytest.raises(CommandError):
        call_command("create_first_admin", name="First Admin", email="first@example.com")
    assert not StaffProfile.objects.exists()
