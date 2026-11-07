"""The shared badge-and-PIN step for admin overrides on a volunteer's screen.

The admin scans their badge (Admin ID) and types their PIN. It doesn't sign
them in. The action is logged under that admin. See SPEC section 2.
"""

from django import forms
from django.db import transaction
from django.utils import timezone

from .audit import audit
from .ids import normalize_code


class OverrideError(Exception):
    pass


def verify_override(admin_id, pin, *, action, note="", note_required=True,
                    requested_by=None, event=None, target=None):
    """Check a badge and PIN and log the override. Returns the admin's StaffProfile.

    Wrong PINs are counted in their own transaction, so call this before starting
    the transaction for the overridden action. Otherwise a rollback would undo
    the count.
    """
    from apps.accounts.models import PIN_LOCK_AFTER, StaffProfile

    note = (note or "").strip()
    if note_required and not note:
        raise OverrideError("A note is required.")

    error = None
    with transaction.atomic():
        profile = (
            StaffProfile.objects.select_for_update()
            .select_related("account")
            .filter(admin_id=normalize_code(admin_id), is_admin=True, account__is_active=True)
            .first()
        )
        if profile is None:
            error = "That badge isn't recognized."
        elif profile.badge_locked:
            error = "That badge is locked. Another admin can unlock or reissue it."
        elif not profile.pin_hash:
            error = "That admin hasn't set a PIN yet."
        elif not profile.check_pin(pin or ""):
            profile.failed_pin_count += 1
            locked = profile.failed_pin_count >= PIN_LOCK_AFTER
            if locked:
                profile.badge_locked_at = timezone.now()
            profile.save(update_fields=["failed_pin_count", "badge_locked_at"])
            audit(profile.account, "badge.pin_failed", event=event, target=profile,
                  attempted_for=action, locked=locked)
            error = "Wrong PIN. The badge is now locked." if locked else "Wrong PIN."
        elif profile.failed_pin_count:
            profile.failed_pin_count = 0
            profile.save(update_fields=["failed_pin_count"])
    # Raised outside the block so the failed-PIN count is kept.
    if error:
        raise OverrideError(error)

    audit(profile.account, f"override.{action}", event=event, target=target, note=note,
          requested_by=requested_by.pk if requested_by else None)
    return profile


class OverrideForm(forms.Form):
    """Badge, PIN and note fields. Use with `core/override_fields.html`."""

    admin_badge = forms.CharField(label="Scan admin badge", max_length=12,
                                  widget=forms.TextInput(attrs={"autocomplete": "off"}))
    admin_pin = forms.CharField(label="PIN", max_length=4, widget=forms.PasswordInput(
        attrs={"inputmode": "numeric", "autocomplete": "off"}))
    admin_note = forms.CharField(label="Note", max_length=500, required=False,
                                 widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, action, note_required=True, requested_by=None, event=None,
                 target=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.override_args = dict(action=action, note_required=note_required,
                                  requested_by=requested_by, event=event, target=target)
        self.fields["admin_note"].required = note_required
        self.admin = None

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        try:
            self.admin = verify_override(data["admin_badge"], data["admin_pin"],
                                         note=data.get("admin_note", ""), **self.override_args)
        except OverrideError as e:
            raise forms.ValidationError(str(e)) from e
        return data
