import pytest

from apps.accounts.services import generate_admin_id, generate_volunteer_id, initials
from apps.core.ids import ID_ALPHABET, normalize_code
from apps.core.money import format_cents, parse_dollars, percent_of


def test_alphabet_leaves_out_confusable_characters():
    for ch in "01IO":
        assert ch not in ID_ALPHABET


def test_normalize_code_ignores_case_and_spaces():
    assert normalize_code("  f26a-042 ") == "F26A-042"


@pytest.mark.parametrize("name,expected", [
    ("Kim Park", "KP"), ("kim   de la park", "KP"), ("Zoë Ångström", "ZA"), ("Cher", "CC"),
    ("", "XX"), ("Mary-Kate O'Neil", "MN"),
])
def test_initials(name, expected):
    assert initials(name) == expected


@pytest.mark.django_db
def test_volunteer_id_format_and_digits():
    vid = generate_volunteer_id("Kim Park")
    assert vid.startswith("VOL-KP")
    assert all(d in "23456789" for d in vid[-2:])


@pytest.mark.django_db
def test_volunteer_id_draws_another_pair_when_taken(make_staff):
    first = make_staff("Kim Park").staff.volunteer_id
    for _ in range(20):
        assert generate_volunteer_id("Kim Park") != first


@pytest.mark.django_db
def test_admin_id_format():
    admin_id = generate_admin_id()
    assert admin_id.startswith("ADM-") and len(admin_id) == 10
    assert all(c in ID_ALPHABET for c in admin_id[4:])


def test_format_cents():
    assert format_cents(0) == "$0.00"
    assert format_cents(10200) == "$102.00"
    assert format_cents(-325) == "-$3.25"
    assert format_cents(123456) == "$1,234.56"


def test_parse_dollars():
    assert parse_dollars("2") == 200
    assert parse_dollars("$2.50") == 250
    assert parse_dollars("0.25") == 25
    with pytest.raises(ValueError):
        parse_dollars("2.505")
    with pytest.raises(ValueError):
        parse_dollars("two")


def test_percent_rounds_half_up():
    assert percent_of(1500, 10) == 150
    assert percent_of(5, 10) == 1  # 0.5 cents rounds up
    assert percent_of(4, 10) == 0
    assert percent_of(1250, 7) == 88  # 87.5 rounds up
