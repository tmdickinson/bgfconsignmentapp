"""Reading responses from a Google Form through the Google Forms API.

Someone with access to the form authorizes the app once per event. The app
asks only for read access to the form and its responses.
"""

import re
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.audit import audit

from .form_import import process_response, summarize

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://forms.googleapis.com/v1/forms"
SCOPES = "https://www.googleapis.com/auth/forms.body.readonly https://www.googleapis.com/auth/forms.responses.readonly"
TIMEOUT = 20
# A heading for the respondent's email when the form collects it automatically.
RESPONDENT_EMAIL = "Respondent email"


class GoogleError(Exception):
    pass


def is_configured():
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def form_id_from_url(url):
    """The form ID from its edit link, docs.google.com/forms/d/<id>/edit."""
    if "/forms/d/e/" in url:
        raise GoogleError("That is the form's public link. Use the edit link instead.")
    match = re.search(r"/forms/d/([A-Za-z0-9_-]{10,})", url or "")
    if not match:
        raise GoogleError("That doesn't look like a Google Form link.")
    return match.group(1)


def redirect_uri():
    return settings.SITE_URL + reverse("events:google_callback")


def authorization_url(state):
    return AUTH_URL + "?" + urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri(),
        "response_type": "code", "scope": SCOPES, "access_type": "offline",
        "prompt": "consent", "state": state,
    })


def new_state():
    return secrets.token_urlsafe(24)


def _post_token(data):
    response = requests.post(TOKEN_URL, timeout=TIMEOUT, data={
        "client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET,
        **data,
    })
    if response.status_code != 200:
        raise GoogleError(f"Google refused the authorization ({response.status_code}).")
    return response.json()


def exchange_code(code):
    """Trade the code from the consent screen for a refresh token."""
    token = _post_token({"code": code, "grant_type": "authorization_code",
                         "redirect_uri": redirect_uri()}).get("refresh_token")
    if not token:
        raise GoogleError("Google didn't return a refresh token. Try connecting again.")
    return token


def _get(url, access_token, params=None):
    response = requests.get(url, timeout=TIMEOUT, params=params,
                            headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        raise GoogleError(f"Google Forms returned an error ({response.status_code}).")
    return response.json()


def fetch_responses(form_id, refresh_token):
    """Every response as (response_id, submitted_at, {question title: answer})."""
    access = _post_token({"refresh_token": refresh_token,
                          "grant_type": "refresh_token"})["access_token"]
    form = _get(f"{API}/{form_id}", access)
    titles = {}
    for item in form.get("items", []):
        question = item.get("questionItem", {}).get("question")
        if question:
            titles[question["questionId"]] = item.get("title", "")
    results, page = [], None
    while True:
        data = _get(f"{API}/{form_id}/responses", access, {"pageToken": page} if page else None)
        for r in data.get("responses", []):
            answers = {}
            for qid, answer in r.get("answers", {}).items():
                values = [a.get("value", "") for a in answer.get("textAnswers", {}).get("answers", [])]
                answers[titles.get(qid, qid)] = ", ".join(values)
            if r.get("respondentEmail"):
                answers[RESPONDENT_EMAIL] = r["respondentEmail"]
            results.append((r["responseId"], parse_datetime(r.get("lastSubmittedTime") or
                                                            r.get("createTime") or ""), answers))
        page = data.get("nextPageToken")
        if not page:
            return results


def sync_event(event, actor=None):
    """Fetch new responses for one event and process them. Returns a summary."""
    connection = getattr(event, "google", None)
    if connection is None:
        raise GoogleError("This event isn't connected to Google.")
    try:
        rows = fetch_responses(form_id_from_url(event.form_url), connection.refresh_token)
    except (GoogleError, requests.RequestException, KeyError) as e:
        connection.last_error = str(e)
        connection.save(update_fields=["last_error"])
        raise GoogleError(str(e)) from e
    # Oldest first, so seller numbers follow submission order.
    rows.sort(key=lambda r: (r[1] is None, r[1] or timezone.now()))
    created = []
    for response_id, submitted_at, answers in rows:
        response = process_response(event, source="google", external_id=f"google:{response_id}",
                                    answers=answers, submitted_at=submitted_at, actor=actor)
        if response:
            created.append(response)
    connection.last_error = ""
    connection.save(update_fields=["last_error"])
    event.last_synced_at = timezone.now()
    event.save(update_fields=["last_synced_at"])
    summary = summarize(created)
    audit(actor, "import.google", event=event, target=event, **summary)
    return summary
