from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("new/", views.event_new, name="new"),
    path("google/callback/", views.google_callback, name="google_callback"),
    path("<int:event_pk>/", views.event_detail, name="detail"),
    path("<int:event_pk>/settings/", views.event_edit, name="edit"),
    path("<int:event_pk>/windows/", views.event_windows, name="windows"),
    path("<int:event_pk>/schedule/", views.event_schedule, name="schedule"),
    path("<int:event_pk>/status/", views.event_advance, name="advance"),
    path("<int:event_pk>/waivers/", views.event_waivers, name="waivers"),
    path("<int:event_pk>/sellers/", views.event_sellers, name="sellers"),
    path("<int:event_pk>/sellers/new/", views.event_seller_new, name="seller_new"),
    path("<int:event_pk>/staff-sellers/", views.staff_sellers, name="staff_sellers"),
    path("<int:event_pk>/import/", views.event_import, name="import"),
    path("<int:event_pk>/import/review/", views.review_list, name="review"),
    path("<int:event_pk>/import/google/connect/", views.google_connect, name="google_connect"),
    path("<int:event_pk>/import/google/sync/", views.google_sync, name="google_sync"),
]
