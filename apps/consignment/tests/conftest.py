import pytest

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
