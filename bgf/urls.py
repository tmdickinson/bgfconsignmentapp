from django.urls import include, path

from apps.accounts.views import home

urlpatterns = [
    path("", home, name="home"),
    path("", include("apps.accounts.urls")),
    path("events/", include("apps.events.urls")),
]
