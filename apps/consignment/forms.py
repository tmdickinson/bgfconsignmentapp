from django import forms

from .models import (
    CONDITION_NOTE_MAX,
    DESCRIPTION_MAX,
    NAME_MAX,
    PUBLISHER_MAX,
    Condition,
    SlipSize,
)
from .services.listings import DETAIL_FIELDS


class ListingForm(forms.Form):
    """A listing can be saved incomplete. Completeness is checked when slips are generated."""

    name = forms.CharField(max_length=NAME_MAX, required=False, label="Game name")
    publisher = forms.CharField(max_length=PUBLISHER_MAX, required=False)
    description = forms.CharField(max_length=DESCRIPTION_MAX, required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}),
                                  help_text=f"Up to {DESCRIPTION_MAX} characters.")
    condition = forms.ChoiceField(choices=Condition.choices, required=False,
                                  widget=forms.RadioSelect)
    condition_note = forms.CharField(max_length=CONDITION_NOTE_MAX, required=False,
                                     label="Condition details",
                                     help_text="Required for Fair / Incomplete. Say what's missing or worn.")
    is_bundle = forms.BooleanField(required=False, label="This is a bundle of several items")
    bundle_count = forms.IntegerField(required=False, min_value=2, max_value=99,
                                      label="Number of items in the bundle")
    slip_size = forms.ChoiceField(choices=SlipSize.choices, initial=SlipSize.LARGE,
                                  widget=forms.RadioSelect)
    donate_if_unsold = forms.BooleanField(required=False, label="Donate if not sold")
    bgg_id = forms.IntegerField(required=False, min_value=1, max_value=9_999_999,
                                label="BoardGameGeek ID", help_text="Optional. Never printed.")

    def __init__(self, *args, windows, **kwargs):
        super().__init__(*args, **kwargs)
        self.windows = list(windows)
        for window in self.windows:
            self.fields[f"price_{window.pk}"] = forms.IntegerField(
                label=f"{window.name} price ($)", required=False, min_value=1,
                max_value=10_000, widget=forms.NumberInput(attrs={"step": 1, "min": 1}),
                error_messages={"invalid": "Use whole dollars, such as 15."},
            )

    @property
    def price_fields(self):
        return [self[f"price_{w.pk}"] for w in self.windows]

    @property
    def detail_fields(self):
        return [self[name] for name in self.fields if not name.startswith("price_")]

    def details(self):
        return {name: self.cleaned_data[name] for name in DETAIL_FIELDS}

    def prices(self):
        return {w.pk: self.cleaned_data[f"price_{w.pk}"] for w in self.windows}


def listing_initial(listing):
    initial = {name: getattr(listing, name) for name in DETAIL_FIELDS}
    for price in listing.prices.all():
        initial[f"price_{price.window_id}"] = price.dollars
    return initial
