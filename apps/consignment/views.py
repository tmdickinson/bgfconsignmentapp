from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.permissions import admin_required, can_access_event, event_view, seller_view

from .forms import ListingForm, listing_initial
from .models import ItemStatus, Listing, SlipVersion
from .services import listings as svc
from .slips import SlipData, sample_slips, slips_pdf


def seller_listings_context(seller):
    """What the seller's home page shows: active and deactivated games, and slip state."""
    all_listings = list(seller.listings.select_related("current_slip")
                        .prefetch_related("prices__window"))
    active = [lst for lst in all_listings if lst.is_active]
    for listing in active:
        listing.problems = svc.problems(listing)
    return {
        "active": active,
        "deactivated": [lst for lst in all_listings if not lst.is_active],
        "needs_slip": [lst for lst in active if not lst.has_valid_slip],
        "incomplete": [lst for lst in active if lst.problems],
        "can_edit": seller.event.status == "presale",
        "can_print": svc.can_print(seller),
    }


def _listing(seller, pk):
    # Filtering by seller is what keeps sellers out of each other's listings.
    return get_object_or_404(Listing.objects.select_related("current_slip"), pk=pk, seller=seller)


def _home(seller):
    return redirect("accounts:seller_home", seller_pk=seller.pk)


@seller_view
def listing_new(request, seller):
    if seller.event.status != "presale":
        messages.error(request, "Listings can only be changed during Presale.")
        return _home(seller)
    form = ListingForm(request.POST or None, windows=seller.event.windows.all())
    if request.method == "POST" and form.is_valid():
        listing = svc.create_listing(seller, request.user, form.details(), form.prices())
        messages.success(request, f"Saved {listing.name or 'your game'}.")
        if "add_another" in request.POST:
            return redirect("consignment:listing_new", seller_pk=seller.pk)
        return _home(seller)
    return render(request, "consignment/listing_form.html",
                  {"seller": seller, "form": form, "mode": "new"})


@seller_view
def listing_detail(request, seller, pk):
    listing = _listing(seller, pk)
    return render(request, "consignment/listing_detail.html", {
        "seller": seller, "listing": listing, **_history(listing),
        "problems": svc.problems(listing) if listing.is_active else [],
        "editable": svc.is_editable(listing), "presale": seller.event.status == "presale",
    })


def _history(listing):
    return {
        "prices": listing.prices.select_related("window"),
        "slips": listing.slips.prefetch_related("prices__window"),
        "changes": listing.status_changes.select_related("changed_by__staff_profile",
                                                         "changed_by__seller_record"),
    }


@seller_view
def listing_edit(request, seller, pk):
    """Edit a listing without a slip, or make changes while reactivating."""
    listing = _listing(seller, pk)
    reactivating = listing.status == ItemStatus.DEACTIVATED
    if seller.event.status != "presale" or not (reactivating or svc.is_editable(listing)):
        messages.error(request, "This game can't be edited now.")
        return redirect("consignment:listing_detail", seller_pk=seller.pk, pk=listing.pk)
    form = ListingForm(request.POST or None, initial=listing_initial(listing),
                       windows=seller.event.windows.all())
    if request.method == "POST" and form.is_valid():
        try:
            if reactivating:
                had_slip = listing.has_valid_slip
                svc.reactivate(listing, request.user, form.details(), form.prices())
                listing.refresh_from_db()
                if had_slip and not listing.has_valid_slip:
                    messages.warning(request, "Reactivated with changes. The old slip no longer "
                                              "works. Generate a new one.")
                else:
                    messages.success(request, "Reactivated.")
            else:
                svc.update_listing(listing, request.user, form.details(), form.prices())
                messages.success(request, "Saved.")
        except svc.ListingError as e:
            messages.error(request, str(e))
        return _home(seller)
    return render(request, "consignment/listing_form.html", {
        "seller": seller, "form": form, "listing": listing,
        "mode": "reactivate" if reactivating else "edit"})


@seller_view
@require_POST
def listing_deactivate(request, seller, pk):
    listing = _listing(seller, pk)
    try:
        svc.deactivate(listing, request.user)
    except svc.ListingError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"Deactivated {listing.name or 'the game'}.")
    return _home(seller)


@seller_view
def listing_reactivate(request, seller, pk):
    listing = _listing(seller, pk)
    if listing.status != ItemStatus.DEACTIVATED or seller.event.status != "presale":
        return redirect("consignment:listing_detail", seller_pk=seller.pk, pk=listing.pk)
    if request.method == "POST":
        try:
            svc.reactivate(listing, request.user)
        except svc.ListingError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, "Reactivated with no changes."
                             + (f" Slip {listing.current_slip.item_id} still works."
                                if listing.has_valid_slip else ""))
        return _home(seller)
    return render(request, "consignment/reactivate.html", {"seller": seller, "listing": listing})


@seller_view
@require_POST
def slips_generate(request, seller):
    try:
        created = svc.generate_slips(seller, request.user)
    except svc.ListingError as e:
        messages.error(request, str(e))
        return _home(seller)
    if not created:
        messages.info(request, "Every active game already has a valid slip.")
        return _home(seller)
    url = reverse("consignment:slips_print", kwargs={"seller_pk": seller.pk})
    query = "&".join(f"slip={s.pk}" for s in created)
    request.session["new_slips_url"] = f"{url}?{query}"
    messages.success(request, f"Generated {len(created)} slip{'s' if len(created) != 1 else ''}.")
    return _home(seller)


@seller_view
def slips_print(request, seller):
    if not svc.can_print(seller):
        messages.error(request, "Slips can be printed through the end of Check-in.")
        return _home(seller)
    ids = [int(i) for i in request.GET.getlist("slip") if i.isdigit()]
    slips = [s for s in SlipVersion.objects.filter(pk__in=ids, listing__seller=seller)
             .select_related("listing__seller__event", "listing__current_slip")
             .prefetch_related("prices__window") if s.is_valid]
    if not slips:
        messages.error(request, "Tick at least one game with a valid slip to print.")
        return _home(seller)
    return _pdf([SlipData.from_slip(s) for s in slips], f"slips-{seller.seller_id}.pdf")


def _pdf(slips, filename):
    response = HttpResponse(slips_pdf(slips), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


# ---------- Staff views ----------


def item_detail(request, pk):
    """A listing and its full history, for staff with access to its event."""
    listing = get_object_or_404(Listing.objects.select_related("seller__event", "current_slip"),
                                pk=pk)
    if not (request.user.is_staff_member and can_access_event(request.user, listing.seller.event)):
        raise PermissionDenied
    return render(request, "consignment/item_detail.html", {
        "listing": listing, "seller": listing.seller, "event": listing.seller.event,
        **_history(listing)})


@admin_required
@event_view
def sample_slips_pdf(request, event):
    return _pdf(sample_slips(event), f"sample-slips-{event.code}.pdf")


@event_view
def event_items(request, event):
    """Browse and search an event's games, grouped by seller."""
    from apps.accounts.services import parse_seller_id

    from .services.checkin import parse_item_id

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    listings = (Listing.objects.filter(seller__event=event)
                .select_related("seller__event", "accepted_slip", "current_slip")
                .order_by("seller__number", "created_at"))
    if status in ItemStatus.values:
        listings = listings.filter(status=status)
    if q:
        if parsed := parse_item_id(event, q):
            listings = listings.filter(slips__item_id=parsed[1])
        elif (seller := parse_seller_id(q)) and seller[0] == event.code:
            listings = listings.filter(seller__number=seller[1])
        else:
            listings = listings.filter(Q(name__icontains=q) | Q(publisher__icontains=q)
                                       | Q(seller__name__icontains=q))
    limit = 300
    listings = list(listings.distinct()[:limit + 1])
    return render(request, "consignment/items.html", {
        "event": event, "listings": listings[:limit], "more": len(listings) > limit, "q": q,
        "status": status, "statuses": ItemStatus.choices})


@seller_view
def seller_checkin_report(request, seller):
    from .services.checkin import summary

    data = summary(seller)
    if not (data["report"] and data["report"].completed_at):
        messages.info(request, "Your check-in report appears here after check-in.")
        return _home(seller)
    return render(request, "consignment/seller_checkin_report.html", {"seller": seller, **data})
