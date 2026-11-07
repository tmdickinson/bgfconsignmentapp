"""Displayed identifiers. See SPEC section 3."""

import secrets

# Leaves out 0, 1, I and O so IDs are easy to read and type.
ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
DIGITS = "23456789"


def random_chars(length, alphabet=ID_ALPHABET):
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_code(value):
    """Scanned or typed codes ignore case and surrounding spaces."""
    return (value or "").strip().upper()
