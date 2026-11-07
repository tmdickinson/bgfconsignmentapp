"""Slow down password guessing by refusing attempts after repeated failures."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import LoginFailure


def client_ip(request):
    # Behind the host's proxy the client address is the first forwarded one.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")


def _recent(**filters):
    since = timezone.now() - timedelta(minutes=settings.LOGIN_FAILURE_WINDOW_MINUTES)
    return LoginFailure.objects.filter(created_at__gte=since, **filters).count()


def is_blocked(request, identifier):
    return (_recent(identifier=identifier.strip().lower()) >= settings.LOGIN_FAILURE_LIMIT
            or _recent(ip_address=client_ip(request)) >= settings.LOGIN_FAILURE_IP_LIMIT)


def record_failure(request, identifier):
    LoginFailure.objects.create(identifier=identifier.strip().lower(), ip_address=client_ip(request))


def clear_failures(identifier):
    LoginFailure.objects.filter(identifier=identifier.strip().lower()).delete()
