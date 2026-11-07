import time

from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.tokens import default_token_generator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.http import require_POST

from apps.core.audit import audit
from apps.core.permissions import admin_required, seller_view, staff_required
from apps.events.models import Event

from . import services, throttle
from .badges import badge_pdf
from .forms import (
    ChangePasswordForm,
    ForgotPasswordForm,
    LinkSellerForm,
    LoginForm,
    PinForm,
    SellerForm,
    SetPasswordForm,
    StaffForm,
)
from .models import Account, Seller, StaffProfile

BACKEND = "django.contrib.auth.backends.ModelBackend"
CHOICE_SECONDS = 300


def _next_url(request):
    url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(url, {request.get_host()}, require_https=request.is_secure()):
        return url
    return None


def _sign_in(request, account):
    login(request, account, backend=BACKEND)
    audit(account, "account.login", target=account)


@login_not_required
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"]
        if throttle.is_blocked(request, identifier):
            form.add_error(None, "Too many failed attempts. Wait 15 minutes and try again.")
        else:
            password = form.cleaned_data["password"]
            matches = [a for a in services.find_accounts(identifier) if a.check_password(password)]
            if not matches:
                throttle.record_failure(request, identifier)
                time.sleep(0.5)
                form.add_error(None, "That sign-in and password don't match an active account.")
            else:
                throttle.clear_failures(identifier)
                next_url = _next_url(request)
                if len(matches) == 1:
                    _sign_in(request, matches[0])
                    return redirect(next_url or "home")
                request.session["login_choices"] = {
                    "accounts": [a.pk for a in matches], "at": time.time(), "next": next_url,
                }
                return redirect("accounts:login_choose")
    return render(request, "accounts/login.html", {"form": form, "next": _next_url(request)})


@login_not_required
def login_choose(request):
    """When one email and password match more than one account, ask which to open."""
    pending = request.session.get("login_choices")
    if not pending or time.time() - pending["at"] > CHOICE_SECONDS:
        request.session.pop("login_choices", None)
        return redirect("accounts:login")
    accounts = [a for a in Account.objects.filter(pk__in=pending["accounts"], is_active=True)
                .select_related("staff_profile", "seller_record__event")]
    if request.method == "POST":
        chosen = next((a for a in accounts if str(a.pk) == request.POST.get("account")), None)
        if chosen:
            next_url = pending.get("next")
            request.session.pop("login_choices", None)
            _sign_in(request, chosen)
            return redirect(next_url or "home")
    return render(request, "accounts/login_choose.html", {"accounts": accounts})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@login_not_required
def forgot_password(request):
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"]
        if not throttle.is_blocked(request, "reset:" + identifier):
            throttle.record_failure(request, "reset:" + identifier)  # Limits reset emails too.
            services.send_reset_emails(services.find_accounts(identifier))
        # The same answer whether or not an account exists.
        return render(request, "accounts/forgot_sent.html")
    return render(request, "accounts/forgot.html", {"form": form})


@login_not_required
def set_password(request, uidb64, token):
    try:
        account = Account.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)), is_active=True)
    except (Account.DoesNotExist, ValueError, TypeError, OverflowError):
        account = None
    if account is None or not default_token_generator.check_token(account, token):
        return render(request, "accounts/set_password_invalid.html", status=400)
    form = SetPasswordForm(request.POST or None, account=account)
    if request.method == "POST" and form.is_valid():
        account.set_password(form.cleaned_data["password1"])
        account.save(update_fields=["password"])
        audit(account, "account.password_set", target=account)
        if request.user.is_authenticated:
            logout(request)
        _sign_in(request, account)
        messages.success(request, "Your password is set.")
        return redirect("home")
    return render(request, "accounts/set_password.html", {"form": form, "account": account})


def home(request):
    user = request.user
    if user.seller:
        return redirect("accounts:seller_home", seller_pk=user.seller.pk)
    if not user.is_staff_member:
        logout(request)
        return redirect("accounts:login")
    from apps.events.services import due_reminders

    if user.is_admin:
        events = Event.objects.exclude(status="postsale")
    else:
        events = Event.objects.filter(staff_access__staff=user.staff,
                                      staff_access__revoked_at__isnull=True).exclude(
            status="postsale").distinct()
    from apps.consignment.services.alerts import open_alert_count

    reminders = [(e, r) for e in events for r in due_reminders(e)] if user.is_admin else []
    alerts = [(e, n) for e in events if (n := open_alert_count(e))]
    linked = Seller.objects.filter(linked_staff=user.staff).select_related("event")
    return render(request, "accounts/staff_home.html", {
        "events": events, "reminders": reminders, "alerts": alerts,
        "linked": [s for s in linked if not s.login_expired or user.is_admin],
    })


@staff_required
def my_account(request):
    account = request.user
    profile = account.staff
    pin_form = PinForm(account=account, prefix="pin")
    link_form = LinkSellerForm(prefix="link")
    if request.method == "POST" and "pin-pin" in request.POST and profile.is_admin:
        pin_form = PinForm(request.POST, account=account, prefix="pin")
        if pin_form.is_valid():
            services.set_pin(account, pin_form.cleaned_data["pin"])
            messages.success(request, "Your PIN is set.")
            return redirect("accounts:my_account")
    elif request.method == "POST" and "link-seller_id" in request.POST:
        link_form = LinkSellerForm(request.POST, prefix="link")
        if link_form.is_valid():
            key = "link:" + link_form.cleaned_data["seller_id"]
            if throttle.is_blocked(request, key):
                link_form.add_error(None, "Too many failed attempts. Wait 15 minutes.")
            else:
                try:
                    seller = services.link_seller(account, link_form.cleaned_data["seller_id"],
                                                  link_form.cleaned_data["password"])
                except services.LinkError as e:
                    throttle.record_failure(request, key)
                    link_form.add_error(None, str(e))
                else:
                    messages.success(request, f"Linked to seller {seller.seller_id}.")
                    return redirect("accounts:my_account")
    linked = Seller.objects.filter(linked_staff=profile).select_related("event")
    return render(request, "accounts/my_account.html", {
        "profile": profile, "pin_form": pin_form, "link_form": link_form,
        "linked": [s for s in linked if not s.login_expired or account.is_admin],
        "active_events": Event.objects.exclude(status="postsale").exists(),
    })


def change_password(request):
    form = ChangePasswordForm(request.POST or None, account=request.user)
    if request.method == "POST" and form.is_valid():
        request.user.set_password(form.cleaned_data["password1"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        audit(request.user, "account.password_changed", target=request.user)
        messages.success(request, "Your password has been changed.")
        return redirect("home")
    return render(request, "accounts/change_password.html", {"form": form})


# ---------- Seller pages ----------


@seller_view
def seller_home(request, seller):
    from apps.consignment.views import seller_listings_context

    return render(request, "accounts/seller_home.html", {
        "seller": seller, "event": seller.event, **seller_listings_context(seller),
        "new_slips_url": request.session.pop("new_slips_url", None)})


@seller_view(rules_page=True)
def seller_rules(request, seller):
    if request.method == "POST":
        if request.POST.get("agree"):
            services.accept_rules(seller, request.user)
            return redirect("accounts:seller_home", seller_pk=seller.pk)
        messages.error(request, "Tick the box to agree to the rules.")
    return render(request, "accounts/seller_rules.html", {"seller": seller, "event": seller.event})



# ---------- Staff management (admins) ----------


@admin_required
def staff_list(request):
    profiles = StaffProfile.objects.select_related("account").order_by(
        "-account__is_active", "name")
    return render(request, "accounts/staff_list.html", {"profiles": profiles})


@admin_required
def staff_new(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            profile = services.create_staff(request.user, name=d["name"], email=d["email"],
                                            is_admin=d["is_admin"], is_volunteer=d["is_volunteer"])
        except services.AccountError as e:
            form.add_error(None, str(e))
        else:
            services.set_event_access(request.user, profile, d["events"])
            messages.success(request, f"{profile.name} was added and emailed a set-password link.")
            return redirect("accounts:staff_edit", pk=profile.pk)
    return render(request, "accounts/staff_form.html", {"form": form})


@admin_required
def staff_edit(request, pk):
    profile = get_object_or_404(StaffProfile.objects.select_related("account"), pk=pk)
    current = Event.objects.filter(staff_access__staff=profile,
                                   staff_access__revoked_at__isnull=True)
    initial = {"name": profile.name, "email": profile.account.email, "is_admin": profile.is_admin,
               "is_volunteer": profile.is_volunteer, "events": list(current)}
    form = StaffForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.update_staff(request.user, profile, name=d["name"], email=d["email"],
                                  is_admin=d["is_admin"], is_volunteer=d["is_volunteer"])
        except services.AccountError as e:
            form.add_error(None, str(e))
        else:
            services.set_event_access(request.user, profile, d["events"])
            messages.success(request, "Saved.")
            return redirect("accounts:staff_edit", pk=profile.pk)
    linked = Seller.objects.filter(linked_staff=profile).select_related("event")
    return render(request, "accounts/staff_form.html",
                  {"form": form, "profile": profile, "linked": linked})


@admin_required
@require_POST
def staff_action(request, pk, action):
    profile = get_object_or_404(StaffProfile.objects.select_related("account"), pk=pk)
    try:
        if action == "deactivate":
            services.set_account_active(request.user, profile.account, False)
        elif action == "activate":
            services.set_account_active(request.user, profile.account, True)
        elif action == "send-link":
            services.send_set_password_email(profile.account, new=False)
            audit(request.user, "account.link_sent", target=profile.account)
            messages.success(request, f"A set-password link was emailed to {profile.account.email}.")
        elif action == "reissue-badge" and profile.is_admin:
            services.reissue_badge(request.user, profile)
            messages.success(request, f"New badge {profile.admin_id}. The old one no longer works.")
        elif action == "unlock-badge" and profile.is_admin:
            services.unlock_badge(request.user, profile)
            messages.success(request, "The badge is unlocked.")
        else:
            return HttpResponse(status=400)
    except services.AccountError as e:
        messages.error(request, str(e))
    return redirect("accounts:staff_edit", pk=profile.pk)


@admin_required
def staff_badge(request, pk):
    profile = get_object_or_404(StaffProfile, pk=pk, is_admin=True)
    response = HttpResponse(badge_pdf(profile), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="badge-{profile.admin_id}.pdf"'
    return response


# ---------- Seller management (admins) ----------


@admin_required
def seller_edit(request, pk):
    seller = get_object_or_404(Seller.objects.select_related("event", "account", "linked_staff"),
                               pk=pk)
    form = SellerForm(request.POST or None, initial={
        "name": seller.name, "email": seller.email, "payment_method": seller.payment_method})
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("deactivate", "activate"):
            services.set_account_active(request.user, seller.account, action == "activate")
            return redirect("accounts:seller_edit", pk=seller.pk)
        if action == "send-link":
            services.send_set_password_email(seller.account, new=False)
            audit(request.user, "account.link_sent", event=seller.event, target=seller.account)
            messages.success(request, f"A set-password link was emailed to {seller.email}.")
            return redirect("accounts:seller_edit", pk=seller.pk)
        if form.is_valid():
            try:
                services.update_seller(request.user, seller, **form.cleaned_data)
            except services.AccountError as e:
                form.add_error(None, str(e))
            else:
                messages.success(request, "Saved.")
                return redirect("accounts:seller_edit", pk=seller.pk)
    return render(request, "accounts/seller_edit.html", {
        "form": form, "seller": seller, "event": seller.event,
        "listings": seller.listings.select_related("current_slip")})
