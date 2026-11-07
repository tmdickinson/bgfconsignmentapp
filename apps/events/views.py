from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.forms import SellerForm
from apps.accounts.services import AccountError, create_seller, parse_seller_id
from apps.core.audit import audit
from apps.core.permissions import admin_required, event_view, staff_required

from . import google_forms, services
from .form_import import APP_FIELDS, extract, import_csv
from .forms import (
    CsvImportForm,
    EventForm,
    MappingForm,
    NewEventForm,
    ReviewForm,
    ScheduleForm,
    SellerWaiverForm,
    WaiverForm,
    window_formset,
)
from .models import Event, FeeWaiver, FormResponse, GoogleConnection, Status, WaiverCategory

SETTING_FIELDS = [f for f in EventForm.base_fields]


def _admin_event(view):
    return admin_required(event_view(view))


@staff_required
def event_list(request):
    if request.user.is_admin:
        events = Event.objects.all()
    else:
        events = Event.objects.filter(staff_access__staff=request.user.staff,
                                      staff_access__revoked_at__isnull=True).exclude(
            status="postsale").distinct()
    return render(request, "events/event_list.html", {"events": events})


@admin_required
def event_new(request):
    form = NewEventForm(request.POST or None, initial={
        "listing_fee_type": "sheet", "sheet_fee_cents": 200, "items_per_sheet": 20,
        "flat_fee_cents": 0, "commission_percent": 10, "unsold_fee_cents": 25,
        "rules_text": Event._meta.get_field("rules_text").default,
    })
    if request.method == "POST" and form.is_valid():
        data = dict(form.cleaned_data)
        event = services.create_event(request.user, window_names=data.pop("window_names"), **data)
        messages.success(request, f"Event {event.code} created in Setup.")
        return redirect("events:detail", event_pk=event.pk)
    return render(request, "events/event_form.html", {"form": form})


@event_view
def event_detail(request, event):
    context = {"event": event, "windows": event.windows.all(),
               "schedule": event.schedule.all(), "seller_count": event.sellers.count()}
    if request.user.is_admin:
        context.update(
            reminders=services.due_reminders(event),
            review_count=event.form_responses.filter(needs_review=True,
                                                     reviewed_at__isnull=True).count(),
            access=event.staff_access.filter(revoked_at__isnull=True).select_related("staff"),
        )
    return render(request, "events/event_detail.html", context)


@_admin_event
def event_edit(request, event):
    if event.is_closed:
        messages.error(request, "The event is closed.")
        return redirect("events:detail", event_pk=event.pk)
    locked = services.locked_fields(event)
    initial = {f: getattr(event, f) for f in SETTING_FIELDS}
    form = EventForm(request.POST or None, initial=initial, locked=locked)
    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["code"]
        if code != event.code and Event.objects.filter(code=code).exclude(pk=event.pk).exists():
            form.add_error("code", "That Event ID is already used.")
        else:
            try:
                services.update_event(request.user, event, form.cleaned_data)
            except services.EventError as e:
                form.add_error(None, str(e))
            else:
                messages.success(request, "Settings saved.")
                return redirect("events:detail", event_pk=event.pk)
    return render(request, "events/event_form.html", {"form": form, "event": event})


@_admin_event
def event_windows(request, event):
    locked = services.windows_locked(event)
    FormSet = window_formset(locked)
    initial = [{"name": w.name, "scheduled_start": w.scheduled_start,
                "scheduled_end": w.scheduled_end} for w in event.windows.all()]
    formset = FormSet(request.POST or None, initial=initial, prefix="w")
    if locked:
        for f in formset.forms:
            f.fields["name"].disabled = True
    if request.method == "POST" and formset.is_valid():
        rows = [f.cleaned_data for f in formset.forms
                if f.cleaned_data and not (formset.can_delete and f.cleaned_data.get("DELETE"))]
        try:
            services.update_windows(request.user, event, rows)
        except services.EventError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, "Price windows saved.")
            return redirect("events:detail", event_pk=event.pk)
    return render(request, "events/windows.html",
                  {"event": event, "formset": formset, "locked": locked})


@_admin_event
def event_schedule(request, event):
    form = ScheduleForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        times = {k: v for k, v in form.cleaned_data.items() if not form.fields[k].disabled}
        services.update_schedule(request.user, event, times)
        messages.success(request, "Reminder times saved.")
        return redirect("events:detail", event_pk=event.pk)
    return render(request, "events/schedule.html", {"event": event, "form": form})


@_admin_event
def event_advance(request, event):
    target = event.next_status
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            messages.error(request, "Tick the box to confirm.")
        else:
            try:
                services.advance_status(request.user, event, request.POST.get("expected"))
            except services.EventError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, f"{event.code} is now in {Status(target).label}.")
                return redirect("events:detail", event_pk=event.pk)
    return render(request, "events/advance.html", {
        "event": event, "target": Status(target) if target else None,
        "blockers": services.status_blockers(event),
    })


@_admin_event
def event_waivers(request, event):
    def current(category):
        return FeeWaiver.objects.filter(event=event, category=category, seller=None,
                                        revoked_at__isnull=True).first()

    category_forms = {}
    for category in (WaiverCategory.VOLUNTEER, WaiverCategory.ADMIN):
        w = current(category)
        category_forms[category] = WaiverForm(
            request.POST if request.POST.get("category") == category else None,
            prefix=category, initial={"commission": w and w.waive_commission,
                                      "listing": w and w.waive_listing,
                                      "unsold": w and w.waive_unsold})
    seller_form = SellerWaiverForm(
        request.POST if request.POST.get("category") == "seller" else None,
        event=event, prefix="seller")
    if request.method == "POST":
        category = request.POST.get("category")
        form = seller_form if category == "seller" else category_forms.get(category)
        if form is None:
            return HttpResponseBadRequest()
        if form.is_valid():
            d = form.cleaned_data
            try:
                services.set_waiver(request.user, event, category=category,
                                    seller=d.get("seller"), commission=d["commission"],
                                    listing=d["listing"], unsold=d["unsold"],
                                    note=d.get("note", ""))
            except services.EventError as e:
                messages.error(request, str(e))
            else:
                messages.success(request, "Waiver saved.")
                return redirect("events:waivers", event_pk=event.pk)
    seller_waivers = FeeWaiver.objects.filter(
        event=event, category=WaiverCategory.SELLER, revoked_at__isnull=True
    ).select_related("seller__event", "set_by")
    history = FeeWaiver.objects.filter(event=event).select_related("seller__event", "set_by")
    return render(request, "events/waivers.html", {
        "event": event, "category_forms": category_forms, "seller_form": seller_form,
        "seller_waivers": seller_waivers, "history": history,
        "current": {c: current(c) for c in category_forms},
    })


@event_view
def event_sellers(request, event):
    q = request.GET.get("q", "").strip()
    sellers = event.sellers.select_related("account", "linked_staff", "event")
    if q:
        filters = Q(name__icontains=q) | Q(email__icontains=q)
        parsed = parse_seller_id(q)
        if parsed and parsed[0] == event.code:
            filters |= Q(number=parsed[1])
        elif q.isdigit():
            filters |= Q(number=int(q))
        sellers = sellers.filter(filters)
    return render(request, "events/sellers.html", {"event": event, "sellers": sellers, "q": q})


@_admin_event
def event_seller_new(request, event):
    response = None
    if pk := request.GET.get("response") or request.POST.get("response"):
        response = get_object_or_404(FormResponse, pk=pk, event=event, seller__isnull=True)
    initial = extract(event, response.payload) if response else {}
    form = SellerForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            seller = create_seller(event, actor=request.user, **form.cleaned_data)
        except AccountError as e:
            form.add_error(None, str(e))
        else:
            if response:
                response.seller = seller
                response.reviewed_by, response.reviewed_at = request.user, timezone.now()
                response.review_note = "Seller created by an admin"
                response.save()
            messages.success(request, f"Seller {seller.seller_id} created and emailed a "
                                      "set-password link.")
            return redirect("events:sellers", event_pk=event.pk)
    return render(request, "events/seller_new.html",
                  {"event": event, "form": form, "response": response})


@_admin_event
def staff_sellers(request, event):
    sellers = event.sellers.filter(linked_staff__isnull=False).select_related(
        "linked_staff", "event")
    return render(request, "events/staff_sellers.html", {"event": event, "sellers": sellers})


@_admin_event
def event_import(request, event):
    mapping_form = MappingForm(request.POST if "save_mapping" in request.POST else None,
                               event=event, prefix="map")
    csv_form = CsvImportForm(request.POST if "import_csv" in request.POST else None,
                             request.FILES or None, prefix="csv")
    if request.method == "POST":
        if "save_mapping" in request.POST and mapping_form.is_valid():
            event.field_mapping = {f: mapping_form.cleaned_data[f] for f, _ in APP_FIELDS}
            event.save(update_fields=["field_mapping"])
            audit(request.user, "event.mapping_updated", event=event, target=event,
                  mapping=event.field_mapping)
            messages.success(request, "Field mapping saved.")
            return redirect("events:import", event_pk=event.pk)
        if "import_csv" in request.POST and csv_form.is_valid():
            try:
                summary = import_csv(event, csv_form.cleaned_data["file"], request.user)
            except ValueError as e:
                csv_form.add_error("file", str(e))
            else:
                messages.success(request, _summary_text(summary))
                return redirect("events:import", event_pk=event.pk)
    return render(request, "events/import.html", {
        "event": event, "mapping_form": mapping_form, "csv_form": csv_form,
        "google_configured": google_forms.is_configured(),
        "connection": GoogleConnection.objects.filter(event=event).first(),
        "recent": event.form_responses.select_related("seller__event")[:25],
        "review_count": event.form_responses.filter(needs_review=True,
                                                    reviewed_at__isnull=True).count(),
    })


def _summary_text(summary):
    parts = [f"{summary.get('created', 0)} accounts created"]
    for key, label in (("duplicate", "duplicates"), ("outside_presale", "outside Presale"),
                       ("incomplete", "incomplete"), ("already_processed", "already processed")):
        if summary.get(key):
            parts.append(f"{summary[key]} {label}")
    return "Import finished: " + ", ".join(parts) + "."


@_admin_event
@require_POST
def google_sync(request, event):
    try:
        summary = google_forms.sync_event(event, request.user)
    except google_forms.GoogleError as e:
        messages.error(request, f"Sync failed: {e}")
    else:
        messages.success(request, _summary_text(summary))
    return redirect("events:import", event_pk=event.pk)


@_admin_event
@require_POST
def google_connect(request, event):
    try:
        google_forms.form_id_from_url(event.form_url)
    except google_forms.GoogleError as e:
        messages.error(request, f"{e} Set the Google Form link in the event settings.")
        return redirect("events:import", event_pk=event.pk)
    state = google_forms.new_state()
    request.session["google_oauth"] = {"state": state, "event": event.pk}
    return redirect(google_forms.authorization_url(state))


@admin_required
def google_callback(request):
    pending = request.session.pop("google_oauth", None)
    if not pending or request.GET.get("state") != pending["state"]:
        return HttpResponseBadRequest("This authorization link has expired. Start again.")
    event = get_object_or_404(Event, pk=pending["event"])
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(request, "Google authorization was canceled.")
        return redirect("events:import", event_pk=event.pk)
    try:
        token = google_forms.exchange_code(request.GET["code"])
    except google_forms.GoogleError as e:
        messages.error(request, str(e))
    else:
        GoogleConnection.objects.update_or_create(event=event, defaults={
            "refresh_token": token, "authorized_by": request.user,
            "authorized_at": timezone.now(), "last_error": ""})
        audit(request.user, "event.google_connected", event=event, target=event)
        messages.success(request, "Connected to the Google Form. Use Sync now to fetch responses.")
    return redirect("events:import", event_pk=event.pk)


@_admin_event
def review_list(request, event):
    if request.method == "POST":
        response = get_object_or_404(FormResponse, pk=request.POST.get("response"), event=event)
        form = ReviewForm(request.POST)
        if form.is_valid() and response.reviewed_at is None:
            response.reviewed_by, response.reviewed_at = request.user, timezone.now()
            response.review_note = form.cleaned_data["note"]
            response.save(update_fields=["reviewed_by", "reviewed_at", "review_note"])
            audit(request.user, "import.reviewed", event=event, target=response,
                  note=response.review_note)
        return redirect("events:review", event_pk=event.pk)
    show_done = request.GET.get("done") == "1"
    responses = event.form_responses.filter(needs_review=True,
                                            reviewed_at__isnull=not show_done)
    rows = [(r, extract(event, r.payload)) for r in responses.select_related("reviewed_by")]
    return render(request, "events/review.html",
                  {"event": event, "rows": rows, "show_done": show_done})
