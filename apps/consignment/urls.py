from django.urls import path

from . import checkin_views, views

app_name = "consignment"

urlpatterns = [
    path("seller/<int:seller_pk>/games/new/", views.listing_new, name="listing_new"),
    path("seller/<int:seller_pk>/games/<int:pk>/", views.listing_detail, name="listing_detail"),
    path("seller/<int:seller_pk>/games/<int:pk>/edit/", views.listing_edit, name="listing_edit"),
    path("seller/<int:seller_pk>/games/<int:pk>/deactivate/", views.listing_deactivate,
         name="listing_deactivate"),
    path("seller/<int:seller_pk>/games/<int:pk>/reactivate/", views.listing_reactivate,
         name="listing_reactivate"),
    path("seller/<int:seller_pk>/slips/generate/", views.slips_generate, name="slips_generate"),
    path("seller/<int:seller_pk>/slips/print/", views.slips_print, name="slips_print"),
    path("seller/<int:seller_pk>/check-in/", views.seller_checkin_report,
         name="seller_checkin_report"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("events/<int:event_pk>/items/", views.event_items, name="event_items"),
    path("events/<int:event_pk>/sample-slips.pdf", views.sample_slips_pdf, name="sample_slips"),
    path("events/<int:event_pk>/check-in/", checkin_views.checkin_sellers, name="checkin_sellers"),
    path("events/<int:event_pk>/check-in/<int:seller_pk>/", checkin_views.checkin_screen,
         name="checkin_screen"),
    path("events/<int:event_pk>/check-in/<int:seller_pk>/report/", checkin_views.checkin_report,
         name="checkin_report"),
]
