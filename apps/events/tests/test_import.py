import io

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.accounts.models import Seller
from apps.events import google_forms
from apps.events.form_import import import_csv, process_response
from apps.events.models import FormResponse, GoogleConnection, Status

CSV = """Timestamp,Name,Email,Preferred payment method
11/1/2026 10:00,Pat Jones,pat@example.com,PayPal
11/1/2026 10:05,Lee Smith,lee@example.com,Check
11/1/2026 10:09,Pat Again,PAT@example.com,Venmo
11/1/2026 10:12,,nobody@example.com,Cash
"""


def upload(text, name="responses.csv"):
    return SimpleUploadedFile(name, text.encode())


@pytest.mark.django_db
def test_csv_import_creates_sellers_and_flags_the_rest(admin, event,
                                                       django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        summary = import_csv(event, upload(CSV), admin)
    assert summary["created"] == 2 and summary["duplicate"] == 1 and summary["incomplete"] == 1
    assert [s.seller_id for s in Seller.objects.filter(event=event)] == ["F26A-001", "F26A-002"]
    assert Seller.objects.get(number=1).payment_method == "PayPal"
    assert len(mail.outbox) == 2
    review = FormResponse.objects.filter(event=event, needs_review=True)
    assert review.count() == 2 and not review.filter(seller__isnull=False).exists()


@pytest.mark.django_db
def test_each_row_is_processed_once(admin, event):
    import_csv(event, upload(CSV), admin)
    summary = import_csv(event, upload(CSV), admin)
    assert summary["created"] == 0 and summary["already_processed"] == 4
    assert Seller.objects.count() == 2


@pytest.mark.django_db
def test_import_never_changes_existing_accounts(admin, event, make_seller):
    existing = make_seller(event, "Pat Original", email="pat@example.com")
    import_csv(event, upload(CSV), admin)
    existing.refresh_from_db()
    assert existing.name == "Pat Original" and existing.payment_method == "PayPal"
    assert FormResponse.objects.filter(outcome="duplicate").count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("status", [Status.SETUP, Status.CHECKIN, Status.SALE])
def test_submissions_outside_presale_go_to_review(admin, make_event, status):
    event = make_event(status=status)
    summary = import_csv(event, upload(CSV), admin)
    assert summary["outside_presale"] == 4
    assert not Seller.objects.exists()


@pytest.mark.django_db
def test_custom_field_mapping(admin, event):
    event.field_mapping = {"name": "Your full name", "email": "E-mail",
                           "payment_method": "How should we pay you?"}
    event.save()
    text = "Your full name,E-mail,How should we pay you?\nKim Park,kim@example.com,Zelle\n"
    assert import_csv(event, upload(text), admin)["created"] == 1
    assert Seller.objects.get().payment_method == "Zelle"


@pytest.mark.django_db
def test_missing_columns_are_reported(admin, event):
    with pytest.raises(ValueError, match="Missing columns: Email"):
        import_csv(event, upload("Name,Payment\nKim,Cash\n"), admin)


@pytest.mark.django_db
def test_csv_upload_page(login, admin, event):
    client = login(admin)
    response = client.post(reverse("events:import", args=[event.pk]),
                           {"import_csv": "1", "csv-file": io.BytesIO(CSV.encode())}, follow=True)
    assert b"2 accounts created" in response.content


@pytest.mark.django_db
def test_admin_creates_seller_from_review(login, admin, make_event):
    event = make_event(status=Status.SETUP)
    import_csv(event, upload(CSV), admin)
    response = FormResponse.objects.filter(event=event).order_by("id").first()
    client = login(admin)
    page = client.get(reverse("events:seller_new", args=[event.pk]), {"response": response.pk})
    assert b"pat@example.com" in page.content
    client.post(reverse("events:seller_new", args=[event.pk]), {
        "response": response.pk, "name": "Pat Jones", "email": "pat@example.com",
        "payment_method": "PayPal"})
    response.refresh_from_db()
    assert response.seller.name == "Pat Jones" and response.reviewed_by == admin


@pytest.mark.django_db
def test_mark_reviewed(login, admin, make_event):
    event = make_event(status=Status.SETUP)
    import_csv(event, upload(CSV), admin)
    response = FormResponse.objects.first()
    login(admin).post(reverse("events:review", args=[event.pk]),
                      {"response": response.pk, "note": "Late signup, told to wait"})
    response.refresh_from_db()
    assert response.reviewed_by == admin and response.review_note.startswith("Late")


def test_form_id_from_url():
    url = "https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
    assert google_forms.form_id_from_url(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    with pytest.raises(google_forms.GoogleError, match="public link"):
        google_forms.form_id_from_url("https://docs.google.com/forms/d/e/1FAIpQLSf/viewform")
    with pytest.raises(google_forms.GoogleError):
        google_forms.form_id_from_url("https://example.com/")


@pytest.mark.django_db
def test_google_sync_processes_new_responses_once(admin, event, monkeypatch):
    event.form_url = "https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
    event.save()
    GoogleConnection.objects.create(event=event, refresh_token="token", authorized_by=admin)
    from django.utils.dateparse import parse_datetime

    rows = [
        ("r2", parse_datetime("2026-11-01T10:05:00Z"),
         {"Name": "Lee Smith", "Email": "lee@example.com", "Preferred payment method": "Check"}),
        ("r1", parse_datetime("2026-11-01T10:00:00Z"),
         {"Name": "Pat Jones", "Email": "pat@example.com", "Preferred payment method": "PayPal"}),
    ]
    monkeypatch.setattr(google_forms, "fetch_responses", lambda form_id, token: rows)
    assert google_forms.sync_event(event, admin)["created"] == 2
    assert Seller.objects.get(number=1).name == "Pat Jones"  # Oldest submission first
    assert google_forms.sync_event(event)["created"] == 0
    event.refresh_from_db()
    assert event.last_synced_at is not None


@pytest.mark.django_db
def test_google_sync_failure_is_recorded(admin, event, monkeypatch):
    event.form_url = "https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
    event.save()
    connection = GoogleConnection.objects.create(event=event, refresh_token="t",
                                                 authorized_by=admin)

    def fail(form_id, token):
        raise google_forms.GoogleError("Google Forms returned an error (403).")

    monkeypatch.setattr(google_forms, "fetch_responses", fail)
    with pytest.raises(google_forms.GoogleError):
        google_forms.sync_event(event)
    connection.refresh_from_db()
    assert "403" in connection.last_error


@pytest.mark.django_db
def test_fetch_responses_maps_question_titles(monkeypatch):
    class Reply:
        def __init__(self, data):
            self.status_code, self._data = 200, data

        def json(self):
            return self._data

    def fake_post(url, **kwargs):
        return Reply({"access_token": "a"})

    pages = {
        None: {"responses": [{"responseId": "x1", "createTime": "2026-11-01T10:00:00Z",
                              "respondentEmail": "kim@example.com",
                              "answers": {"q1": {"textAnswers": {"answers": [{"value": "Kim"}]}}}}],
               "nextPageToken": "p2"},
        "p2": {"responses": [{"responseId": "x2", "createTime": "2026-11-01T11:00:00Z",
                              "answers": {"q1": {"textAnswers": {"answers": [{"value": "Lee"}]}}}}]},
    }

    def fake_get(url, params=None, **kwargs):
        if url.endswith("/responses"):
            return Reply(pages[(params or {}).get("pageToken")])
        return Reply({"items": [{"title": "Name", "questionItem": {"question": {"questionId": "q1"}}}]})

    monkeypatch.setattr(google_forms.requests, "post", fake_post)
    monkeypatch.setattr(google_forms.requests, "get", fake_get)
    rows = google_forms.fetch_responses("form", "refresh")
    assert [r[0] for r in rows] == ["x1", "x2"]
    assert rows[0][2] == {"Name": "Kim", "Respondent email": "kim@example.com"}


@pytest.mark.django_db
def test_process_response_directly(event):
    response = process_response(event, source="google", external_id="google:1",
                                answers={"name": "A", "EMAIL": "a@example.com"})
    assert response.outcome == "created"
    assert process_response(event, source="google", external_id="google:1", answers={}) is None
