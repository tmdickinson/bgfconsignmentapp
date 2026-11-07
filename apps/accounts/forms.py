from django import forms
from django.contrib.auth.password_validation import validate_password

from apps.events.models import Event


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Seller ID, Volunteer ID or email", max_length=254,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class ForgotPasswordForm(forms.Form):
    identifier = forms.CharField(label="Seller ID, Volunteer ID or email", max_length=254)


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(strip=False, label="New password", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}),
        help_text="At least 10 characters, and not a common password.")
    password2 = forms.CharField(strip=False, label="New password again", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, account=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = account

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("The passwords don't match.")
        if p1:
            validate_password(p1, self.account)
        return data


class ChangePasswordForm(SetPasswordForm):
    current = forms.CharField(strip=False, label="Current password", widget=forms.PasswordInput)
    field_order = ["current", "password1", "password2"]

    def clean_current(self):
        if not self.account.check_password(self.cleaned_data["current"]):
            raise forms.ValidationError("That isn't your current password.")
        return self.cleaned_data["current"]


class PinForm(forms.Form):
    current = forms.CharField(strip=False, label="Your account password", widget=forms.PasswordInput)
    pin = forms.RegexField(regex=r"^\d{4}$", label="New 4-digit PIN", max_length=4,
                           error_messages={"invalid": "Use exactly 4 digits."},
                           widget=forms.PasswordInput(attrs={"inputmode": "numeric"}))
    pin2 = forms.CharField(label="PIN again", max_length=4,
                           widget=forms.PasswordInput(attrs={"inputmode": "numeric"}))

    def __init__(self, *args, account=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.account = account

    def clean_current(self):
        if not self.account.check_password(self.cleaned_data["current"]):
            raise forms.ValidationError("That isn't your password.")
        return self.cleaned_data["current"]

    def clean(self):
        data = super().clean()
        if data.get("pin") and data.get("pin") != data.get("pin2"):
            raise forms.ValidationError("The PINs don't match.")
        return data


class LinkSellerForm(forms.Form):
    seller_id = forms.CharField(label="Seller ID", max_length=12)
    password = forms.CharField(strip=False, label="That seller account's password",
                               widget=forms.PasswordInput)


class StaffForm(forms.Form):
    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    is_admin = forms.BooleanField(label="Admin", required=False)
    is_volunteer = forms.BooleanField(label="Volunteer", required=False)
    events = forms.ModelMultipleChoiceField(
        queryset=Event.objects.all(), required=False,
        widget=forms.CheckboxSelectMultiple, label="Event access (volunteers)",
        help_text="Admins can always reach every event.",
    )

    def clean(self):
        data = super().clean()
        if not data.get("is_admin") and not data.get("is_volunteer"):
            raise forms.ValidationError("Choose at least one role.")
        return data


class SellerForm(forms.Form):
    name = forms.CharField(max_length=120)
    email = forms.EmailField()
    payment_method = forms.CharField(label="Preferred payment method", max_length=200,
                                     required=False)
