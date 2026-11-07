import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.events import services
from apps.events.models import Event, FeeWaiver, Status
from apps.events.services import EventError, advance_status


@pytest.mark.django_db
def test_admin_creates_event_in_setup(login, admin):
    response = login(admin).post(reverse("events:new"), {
        "code": "f26a", "name": "Fall 2026", "start_date": "2026-11-20",
        "end_date": "2026-11-22", "rules_text": "Be nice.", "listing_fee_type": "sheet",
        "sheet_fee_cents": "2.00", "items_per_sheet": "20", "flat_fee_cents": "0",
        "commission_percent": "10", "unsold_fee_cents": "0.25",
        "window_names": "Friday\nSaturday\n\nSunday"})
    assert response.status_code == 302
    event = Event.objects.get(code="F26A")
    assert event.status == Status.SETUP
    assert event.sheet_fee_cents == 200 and event.unsold_fee_cents == 25
    assert [w.name for w in event.windows.all()] == ["Friday", "Saturday", "Sunday"]
    assert event.schedule.count() == 5


@pytest.mark.django_db
def test_event_code_must_be_unique_and_four_characters(login, admin, event):
    client = login(admin)
    base = {"name": "X", "start_date": "2026-11-20", "end_date": "2026-11-22",
            "rules_text": "r", "listing_fee_type": "sheet", "sheet_fee_cents": "2",
            "items_per_sheet": "20", "flat_fee_cents": "0", "commission_percent": "10",
            "unsold_fee_cents": ".25", "window_names": "One"}
    for code in ("F26A", "F26", "F2-A"):
        assert client.post(reverse("events:new"), {**base, "code": code}).status_code == 200
    assert Event.objects.count() == 1


@pytest.mark.django_db
def test_status_moves_forward_one_step(admin, make_event):
    event = make_event(status=Status.SETUP)
    for expected in [Status.PRESALE, Status.CHECKIN, Status.SALE, Status.PICKUP, Status.POSTSALE]:
        event = advance_status(admin, event, event.status)
        assert event.status == expected
    assert event.moved_to_postsale_at is not None and event.closed_by == admin
    with pytest.raises(EventError):
        advance_status(admin, event, event.status)
    rows = {r.status: r for r in event.schedule.all()}
    assert all(r.moved_by == admin for r in rows.values())


@pytest.mark.django_db
def test_stale_status_submit_is_refused(admin, make_event):
    event = make_event(status=Status.SETUP)
    advance_status(admin, event, Status.SETUP)
    with pytest.raises(EventError, match="already changed"):
        advance_status(admin, event, Status.SETUP)
    event.refresh_from_db()
    assert event.status == Status.PRESALE


@pytest.mark.django_db
def test_status_page_requires_confirmation(login, admin, make_event):
    event = make_event(status=Status.SETUP)
    client = login(admin)
    url = reverse("events:advance", args=[event.pk])
    assert b"can't be undone" in client.get(url).content
    client.post(url, {"expected": "setup"})
    event.refresh_from_db()
    assert event.status == Status.SETUP
    client.post(url, {"expected": "setup", "confirm": "yes"})
    event.refresh_from_db()
    assert event.status == Status.PRESALE


@pytest.mark.django_db
def test_presale_needs_a_price_window(admin, make_event):
    event = make_event(status=Status.SETUP)
    event.windows.all().delete()
    assert services.status_blockers(event)
    with pytest.raises(EventError, match="price window"):
        advance_status(admin, event, Status.SETUP)


@pytest.mark.django_db
def test_reminders_never_change_status(admin, make_event):
    event = make_event(status=Status.SETUP)
    past = timezone.now() - datetime.timedelta(hours=1)
    services.update_schedule(admin, event, {"presale": past})
    event.refresh_from_db()
    assert event.status == Status.SETUP
    assert [r.status for r in services.due_reminders(event)] == ["presale"]
    advance_status(admin, event, Status.SETUP)
    event.refresh_from_db()
    assert services.due_reminders(event) == []


@pytest.mark.django_db
def test_reminder_shown_to_admins(login, admin, make_event):
    event = make_event(status=Status.SETUP)
    services.update_schedule(admin, event, {"presale": timezone.now() - datetime.timedelta(minutes=5)})
    assert b"Reminder" in login(admin).get(reverse("home")).content


@pytest.mark.django_db
def test_first_seller_locks_fees(admin, event, make_seller):
    assert not event.fees_locked
    make_seller(event)
    event.refresh_from_db()
    assert event.fees_locked
    with pytest.raises(EventError, match="locked"):
        services.update_event(admin, event, {"commission_percent": 15})
    # Other settings can still change.
    services.update_event(admin, event, {"name": "Renamed"})
    event.refresh_from_db()
    assert event.name == "Renamed" and event.commission_percent == 10


@pytest.mark.django_db
def test_locked_fee_fields_ignore_posted_values(login, admin, event, make_seller):
    make_seller(event)
    client = login(admin)
    response = client.post(reverse("events:edit", args=[event.pk]), {
        "code": event.code, "name": "New name", "start_date": "2026-11-20",
        "end_date": "2026-11-22", "rules_text": "r", "listing_fee_type": "flat",
        "sheet_fee_cents": "9", "items_per_sheet": "5", "flat_fee_cents": "9",
        "commission_percent": "50", "unsold_fee_cents": "9"})
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.name == "New name"
    assert (event.listing_fee_type, event.commission_percent, event.sheet_fee_cents) == (
        "sheet", 10, 200)


@pytest.mark.django_db
def test_event_code_changes_only_in_setup(admin, make_event):
    event = make_event(status=Status.SETUP)
    services.update_event(admin, event, {"code": "W27C"})
    event.refresh_from_db()
    assert event.code == "W27C"
    advance_status(admin, event, Status.SETUP)
    with pytest.raises(EventError):
        services.update_event(admin, event, {"code": "X27D"})


@pytest.mark.django_db
def test_seller_ids_follow_an_event_code_change(admin, make_event, make_seller):
    event = make_event(status=Status.SETUP)
    seller = make_seller(event)
    services.update_event(admin, event, {"code": "W27C"})
    seller.refresh_from_db()
    assert seller.seller_id == "W27C-001"


@pytest.mark.django_db
def test_closed_event_settings_cannot_change(admin, event):
    event.status = Status.POSTSALE
    event.save()
    with pytest.raises(EventError):
        services.update_event(admin, event, {"name": "x"})
    with pytest.raises(EventError):
        services.update_windows(admin, event, [{"name": "One"}])


@pytest.mark.django_db
def test_window_schedules_and_names(admin, event):
    start = timezone.now()
    services.update_windows(admin, event, [
        {"name": "Fri", "scheduled_start": start, "scheduled_end": None},
        {"name": "Sat", "scheduled_start": None, "scheduled_end": None},
    ])
    assert [w.name for w in event.windows.all()] == ["Fri", "Sat"]
    assert event.windows.get(position=1).scheduled_start == start
    with pytest.raises(EventError):
        services.update_windows(admin, event, [])


@pytest.mark.django_db
def test_waivers_keep_history_and_record_admin(admin, event, seller):
    services.set_waiver(admin, event, category="volunteer", commission=True, listing=False,
                        unsold=False)
    services.set_waiver(admin, event, category="volunteer", commission=True, listing=True,
                        unsold=True)
    rows = FeeWaiver.objects.filter(event=event, category="volunteer")
    assert rows.count() == 2
    current = rows.get(revoked_at__isnull=True)
    assert current.set_by == admin and current.waive_unsold
    services.set_waiver(admin, event, category="seller", seller=seller, commission=False,
                        listing=True, unsold=False, note="Board member's child")
    services.set_waiver(admin, event, category="seller", seller=seller, commission=False,
                        listing=False, unsold=False)
    assert not FeeWaiver.objects.filter(seller=seller, revoked_at__isnull=True).exists()
    assert FeeWaiver.objects.filter(seller=seller).count() == 1


@pytest.mark.django_db
def test_waivers_can_change_after_fees_lock_but_not_after_closing(admin, event, seller):
    assert event.fees_locked or Event.objects.get(pk=event.pk).fees_locked
    services.set_waiver(admin, event, category="admin", commission=True, listing=True,
                        unsold=True)
    event.status = Status.POSTSALE
    event.save()
    with pytest.raises(EventError):
        services.set_waiver(admin, event, category="admin", commission=False, listing=False,
                            unsold=False)


@pytest.mark.django_db
def test_waiver_page(login, admin, event, seller):
    client = login(admin)
    url = reverse("events:waivers", args=[event.pk])
    assert client.get(url).status_code == 200
    client.post(url, {"category": "seller", "seller-seller": seller.pk, "seller-listing": "on",
                      "seller-note": "Helper"})
    assert FeeWaiver.objects.get(seller=seller).waive_listing


@pytest.mark.django_db
def test_event_pages_render(login, admin, event, seller):
    client = login(admin)
    for name in ["detail", "edit", "windows", "schedule", "advance", "waivers", "sellers",
                 "seller_new", "staff_sellers", "import", "review"]:
        assert client.get(reverse(f"events:{name}", args=[event.pk])).status_code == 200, name
    for url in [reverse("home"), reverse("events:list"), reverse("accounts:staff_list"),
                reverse("accounts:staff_new"), reverse("accounts:my_account"),
                reverse("accounts:staff_edit", args=[admin.staff.pk]),
                reverse("accounts:seller_edit", args=[seller.pk]), reverse("events:new")]:
        assert client.get(url).status_code == 200, url
