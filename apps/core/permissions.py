"""Server-side role checks. Every view other than sign-in uses one of these.

Sign-in itself is required everywhere by LoginRequiredMiddleware.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect


def admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff_member:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def can_access_event(account, event):
    """Admins reach every event. Volunteers reach open events they were granted.

    After an event closes, volunteers see it only through a linked seller account.
    """
    if account.is_admin:
        return True
    if not account.is_volunteer or event.is_closed:
        return False
    return account.staff.event_access.filter(event=event, revoked_at__isnull=True).exists()


def event_view(view):
    """Staff view of one event. Loads `event_pk` into `event` and checks access."""
    from apps.events.models import Event

    @wraps(view)
    def wrapper(request, event_pk, *args, **kwargs):
        if not request.user.is_staff_member:
            raise PermissionDenied
        event = get_object_or_404(Event, pk=event_pk)
        if not can_access_event(request.user, event):
            raise PermissionDenied
        return view(request, event, *args, **kwargs)

    return wrapper


def can_open_seller(account, seller):
    """Sellers see only their own account. Staff see the seller account they linked.

    Admins reach sellers through admin screens, not by opening the account.
    """
    if seller.account_id == account.pk:
        return seller.account.is_active and not seller.login_expired
    if account.staff and seller.linked_staff_id == account.staff.pk:
        return not seller.login_expired or account.is_admin
    return False


def seller_view(view=None, *, rules_page=False):
    """A seller-facing page. Loads `seller_pk` into `seller` and checks ownership.

    Until the seller accepts the rules, every seller page except the rules page
    sends them there.
    """
    from apps.accounts.models import Seller

    if view is None:
        return lambda v: seller_view(v, rules_page=rules_page)

    @wraps(view)
    def wrapper(request, seller_pk, *args, **kwargs):
        seller = get_object_or_404(Seller.objects.select_related("event", "account"), pk=seller_pk)
        if not can_open_seller(request.user, seller):
            raise PermissionDenied
        if seller.rules_accepted_at is None and not rules_page:
            return redirect("accounts:seller_rules", seller_pk=seller.pk)
        return view(request, seller, *args, **kwargs)

    return wrapper
