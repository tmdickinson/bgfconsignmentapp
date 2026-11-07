"""Seller accounts from the Google Form and CSV. Both follow the same rules.

Each response is processed once. Duplicates, incomplete rows and anything
outside Presale go to the review list without creating an account.
"""

import csv
import hashlib
import io
import json

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from apps.accounts.models import Seller
from apps.accounts.services import DuplicateSeller, create_seller
from apps.core.audit import audit

from .models import FormResponse, ResponseOutcome, Status

APP_FIELDS = [
    ("name", "Seller name"),
    ("email", "Email"),
    ("payment_method", "Preferred payment method"),
]
DEFAULT_MAPPING = {
    "name": "Name",
    "email": "Email",
    "payment_method": "Preferred payment method",
}


def mapping_for(event):
    return {**DEFAULT_MAPPING, **(event.field_mapping or {})}


def extract(event, answers):
    """Pick the app's fields out of a response, matching headings without regard to case."""
    by_heading = {k.strip().lower(): (v or "").strip() for k, v in answers.items()}
    return {field: by_heading.get(heading.strip().lower(), "")
            for field, heading in mapping_for(event).items()}


def _valid_email(value):
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def process_response(event, *, source, external_id, answers, submitted_at=None, actor=None):
    """Handle one response. Returns the new FormResponse, or None if already processed."""
    if FormResponse.objects.filter(event=event, external_id=external_id).exists():
        return None
    fields = extract(event, answers)
    event.refresh_from_db(fields=["status"])
    if event.status != Status.PRESALE:
        outcome = ResponseOutcome.OUTSIDE_PRESALE
    elif not fields["name"] or not _valid_email(fields["email"]):
        outcome = ResponseOutcome.INCOMPLETE
    elif Seller.objects.filter(event=event, email__iexact=fields["email"]).exists():
        outcome = ResponseOutcome.DUPLICATE
    else:
        outcome = ResponseOutcome.CREATED

    try:
        with transaction.atomic():
            response = FormResponse.objects.create(
                event=event, external_id=external_id, source=source, payload=answers,
                submitted_at=submitted_at, outcome=outcome,
                needs_review=outcome != ResponseOutcome.CREATED,
            )
            if outcome == ResponseOutcome.CREATED:
                try:
                    with transaction.atomic():
                        response.seller = create_seller(event, actor=actor, **fields)
                except DuplicateSeller:
                    response.outcome, response.needs_review = ResponseOutcome.DUPLICATE, True
                response.save()
    except IntegrityError:
        return None  # Another sync processed it at the same moment.
    return response


def summarize(responses):
    counts = {o.value: 0 for o in ResponseOutcome}
    for r in responses:
        counts[r.outcome] += 1
    return counts


def csv_row_id(row):
    canonical = json.dumps(sorted((k.strip().lower(), (v or "").strip()) for k, v in row.items()))
    return "csv:" + hashlib.sha256(canonical.encode()).hexdigest()


def import_csv(event, uploaded, actor):
    """Import an uploaded CSV file. Returns a summary of outcomes plus skipped rows."""
    try:
        text = uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("The file isn't UTF-8 text. Save it as CSV UTF-8 and try again.") from e
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file is empty.")
    headings = {h.strip().lower() for h in reader.fieldnames if h}
    missing = [h for h in mapping_for(event).values() if h.strip().lower() not in headings]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    created, skipped = [], 0
    for row in reader:
        row = {k: v for k, v in row.items() if k}
        if not any((v or "").strip() for v in row.values()):
            continue
        response = process_response(event, source="csv", external_id=csv_row_id(row),
                                    answers=row, actor=actor)
        if response is None:
            skipped += 1
        else:
            created.append(response)
    summary = {**summarize(created), "already_processed": skipped}
    audit(actor, "import.csv", event=event, target=event, **summary)
    return summary
