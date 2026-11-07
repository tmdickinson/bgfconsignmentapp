import pytest

from apps.accounts.services import set_event_access
from apps.consignment.models import Register
from apps.consignment.services import checkin as csvc
from apps.consignment.services import checkout as osvc
from apps.consignment.services import listings as lsvc
from apps.consignment.services import listings as svc

COMPLETE = {"name": "Wingspan", "publisher": "Stonemaier", "description": "All components.",
            "condition": "good", "condition_note": "", "is_bundle": False, "bundle_count": None,
            "bgg_id": None, "slip_size": "large", "donate_if_unsold": False}


@pytest.fixture
def prices(event):
    def make(*dollars):
        windows = list(event.windows.all())
        dollars = dollars or (30, 20, 10)
        return {w.pk: d for w, d in zip(windows, dollars, strict=True)}
    return make


@pytest.fixture
def make_listing(seller, prices):
    def make(owner=None, **details):
        owner = owner or seller
        return svc.create_listing(owner, owner.account, {**COMPLETE, **details},
                                  prices() if owner == seller else {
                                      w.pk: 5 for w in owner.event.windows.all()})
    return make


def form_data(event, **overrides):
    data = {k: ("" if v is None else v) for k, v in COMPLETE.items() if v is not False}
    for i, w in enumerate(event.windows.all()):
        data[f"price_{w.pk}"] = str(30 - 10 * i)
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


@pytest.fixture
def shop(seller, make_listing, admin, volunteer, make_staff):
    """Four checked-in games, the event in Sale with Friday active, two registers."""
    event = seller.event
    games = [make_listing(name=n) for n in ("Wingspan", "Azul", "Catan", "Root")]
    lsvc.generate_slips(seller, seller.account)
    event.status = "checkin"
    event.save()
    report = csvc.get_report(seller, admin)
    for g in games:
        g.refresh_from_db()
        csvc.accept(report, admin, g.current_slip)
    event.status = "sale"
    event.save()
    osvc.set_active_window(admin, event, event.windows.first())
    event.refresh_from_db()
    other = make_staff("Kim Park")
    set_event_access(admin, volunteer.staff, [event])
    set_event_access(admin, other.staff, [event])
    r1 = Register.objects.create(event=event, name="Register 1")
    r2 = Register.objects.create(event=event, name="Register 2")
    for g in games:
        g.refresh_from_db()
    return type("Shop", (), {"games": games, "event": event, "r1": r1, "r2": r2,
                             "vol": volunteer, "other": other, "admin": admin})


def code(game):
    return game.accepted_slip.item_id


def status(game):
    game.refresh_from_db()
    return game.status


def cart(shop, who=None, register=None):
    return osvc.start_regular(who or shop.vol, shop.event, register or shop.r1)
