import datetime

import pytest

from apps.accounts.models import Account
from apps.accounts.services import create_seller, create_staff
from apps.events.models import Status
from apps.events.services import create_event

PASSWORD = "correct-horse-battery"
PIN = "4826"


@pytest.fixture
def admin(db):
    profile = create_staff(None, name="Alice Admin", email="alice@example.com", is_admin=True,
                           is_volunteer=False, password=PASSWORD, send_email=False)
    profile.set_pin(PIN)
    profile.save()
    return profile.account


@pytest.fixture
def make_staff(db):
    def make(name="Val Volunteer", email=None, is_admin=False, is_volunteer=True):
        email = email or name.lower().replace(" ", ".") + "@example.com"
        return create_staff(None, name=name, email=email, is_admin=is_admin,
                            is_volunteer=is_volunteer, password=PASSWORD, send_email=False).account
    return make


@pytest.fixture
def volunteer(make_staff):
    return make_staff()


@pytest.fixture
def make_event(db, admin):
    def make(code="F26A", status=Status.PRESALE, windows=("Friday", "Saturday", "Sunday")):
        event = create_event(admin, window_names=list(windows), code=code,
                             name=f"Fall {code}", start_date=datetime.date(2026, 11, 20),
                             end_date=datetime.date(2026, 11, 22))
        event.status = status
        event.save()
        return event
    return make


@pytest.fixture
def event(make_event):
    return make_event()


@pytest.fixture
def make_seller(db):
    def make(event, name="Sam Seller", email=None, accepted=True):
        email = email or name.lower().replace(" ", ".") + "@example.com"
        seller = create_seller(event, name=name, email=email, payment_method="PayPal",
                               send_email=False)
        seller.account.set_password(PASSWORD)
        seller.account.save()
        if accepted:
            seller.rules_accepted_at = datetime.datetime.now(datetime.UTC)
            seller.save()
        return seller
    return make


@pytest.fixture
def seller(event, make_seller):
    return make_seller(event)


@pytest.fixture
def login(client):
    def do(account: Account):
        client.force_login(account, backend="django.contrib.auth.backends.ModelBackend")
        return client
    return do
