from django.urls import path

from . import views

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
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("events/<int:event_pk>/sample-slips.pdf", views.sample_slips_pdf, name="sample_slips"),
]
