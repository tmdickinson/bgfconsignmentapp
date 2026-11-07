from django.conf import settings


def branding(request):
    return {"brand": settings.BRAND}
