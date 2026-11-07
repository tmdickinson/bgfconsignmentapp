from django import forms

from apps.accounts.models import Seller
from apps.core.ids import normalize_code
from apps.core.money import format_cents, parse_dollars

from .form_import import APP_FIELDS, mapping_for
from .models import Event, ListingFeeType, Status, event_code_validator

DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class DollarField(forms.CharField):
    """Typed in dollars, cleaned to integer cents."""

    def prepare_value(self, value):
        if isinstance(value, int):
            return format_cents(value).replace("$", "")
        return value

    def clean(self, value):
        if isinstance(value, int):  # A disabled field returns its initial value, already cents.
            return value
        value = super().clean(value)
        try:
            cents = parse_dollars(value)
        except ValueError as e:
            raise forms.ValidationError("Enter an amount such as 2.00.") from e
        if cents < 0:
            raise forms.ValidationError("The amount can't be negative.")
        return cents


class EventForm(forms.Form):
    code = forms.CharField(label="Event ID", max_length=4,
                           help_text="Four letters or digits, such as F26A. Changeable during Setup only.")
    name = forms.CharField(max_length=120)
    start_date = forms.DateField(widget=DATE)
    end_date = forms.DateField(widget=DATE)
    form_url = forms.URLField(label="Google Form link", required=False, assume_scheme="https",
                              help_text="The form's edit link, docs.google.com/forms/d/…/edit")
    rules_text = forms.CharField(label="Selling rules", widget=forms.Textarea(attrs={"rows": 8}),
                                 help_text="Shown to sellers with the fees. They must agree to continue.")
    listing_fee_type = forms.ChoiceField(label="Listing fee", choices=ListingFeeType.choices,
                                         widget=forms.RadioSelect)
    sheet_fee_cents = DollarField(label="Fee per sheet ($)")
    items_per_sheet = forms.IntegerField(label="Items per sheet", min_value=1)
    flat_fee_cents = DollarField(label="Flat fee per seller ($)")
    commission_percent = forms.IntegerField(label="Commission (%)", min_value=0, max_value=100)
    unsold_fee_cents = DollarField(label="Unsold item fee ($)")
    charge_unsold_on_donated = forms.BooleanField(
        label="Charge unsold item fees for donated items", required=False)
    charge_negative_balances = forms.BooleanField(
        label="Charge sellers for negative balances", required=False)

    def __init__(self, *args, locked=(), **kwargs):
        super().__init__(*args, **kwargs)
        for name in locked:
            self.fields[name].disabled = True
            self.fields[name].help_text = (
                "Locked since the first seller signed up." if name != "code"
                else "Locked after Setup.")

    def clean_code(self):
        code = normalize_code(self.cleaned_data["code"])
        event_code_validator(code)
        return code

    def clean(self):
        data = super().clean()
        if data.get("start_date") and data.get("end_date") and data["end_date"] < data["start_date"]:
            self.add_error("end_date", "The end date is before the start.")
        return data


class NewEventForm(EventForm):
    window_names = forms.CharField(
        label="Price windows", widget=forms.Textarea(attrs={"rows": 3}),
        help_text="One name per line, in order, such as Friday, Saturday, Sunday. "
                  "The number and names lock once any item is added.")

    def clean_code(self):
        code = super().clean_code()
        if Event.objects.filter(code=code).exists():
            raise forms.ValidationError("That Event ID is already used.")
        return code

    def clean_window_names(self):
        names = [n.strip() for n in self.cleaned_data["window_names"].splitlines() if n.strip()]
        if not names:
            raise forms.ValidationError("Add at least one window.")
        if len(set(n.lower() for n in names)) != len(names):
            raise forms.ValidationError("Window names must be different.")
        return names


class WindowForm(forms.Form):
    name = forms.CharField(max_length=60)
    scheduled_start = forms.DateTimeField(required=False, widget=DATETIME)
    scheduled_end = forms.DateTimeField(required=False, widget=DATETIME)

    def clean(self):
        data = super().clean()
        start, end = data.get("scheduled_start"), data.get("scheduled_end")
        if start and end and end <= start:
            self.add_error("scheduled_end", "The end is before the start.")
        return data


class BaseWindowFormSet(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        names = [f.cleaned_data["name"].strip().lower() for f in self.forms
                 if f.cleaned_data and not self._should_delete_form(f)]
        if len(names) != len(set(names)):
            raise forms.ValidationError("Window names must be different.")
        if not names:
            raise forms.ValidationError("An event needs at least one price window.")


def window_formset(locked):
    return forms.formset_factory(WindowForm, formset=BaseWindowFormSet,
                                 extra=0 if locked else 1, can_delete=not locked)


class ScheduleForm(forms.Form):
    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        for row in event.schedule.all():
            self.fields[row.status] = forms.DateTimeField(
                label=f"Move to {Status(row.status).label}", required=False, widget=DATETIME,
                initial=row.scheduled_at, disabled=row.moved_at is not None,
                help_text=f"Done {row.moved_at:%b %d, %Y %I:%M %p}" if row.moved_at else "",
            )


class WaiverForm(forms.Form):
    commission = forms.BooleanField(label="Commission", required=False)
    listing = forms.BooleanField(label="Listing fee", required=False)
    unsold = forms.BooleanField(label="Unsold item fee", required=False)


class SellerWaiverForm(WaiverForm):
    seller = forms.ModelChoiceField(queryset=Seller.objects.none())
    note = forms.CharField(max_length=200, required=False)
    field_order = ["seller", "commission", "listing", "unsold", "note"]

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["seller"].queryset = event.sellers.select_related("event")


class MappingForm(forms.Form):
    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        mapping = mapping_for(event)
        for field, label in APP_FIELDS:
            self.fields[field] = forms.CharField(
                label=label, max_length=200, initial=mapping[field],
                help_text="The form question or CSV column heading, matched without regard to case.")


class CsvImportForm(forms.Form):
    file = forms.FileField(label="CSV file", help_text="The first row must hold the column headings.")


class ReviewForm(forms.Form):
    note = forms.CharField(max_length=200, required=False)
