from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/choose/", views.login_choose, name="login_choose"),
    path("logout/", views.logout_view, name="logout"),
    path("password/forgot/", views.forgot_password, name="forgot_password"),
    path("password/set/<uidb64>/<token>/", views.set_password, name="set_password"),
    path("account/", views.my_account, name="my_account"),
    path("account/password/", views.change_password, name="change_password"),
    path("seller/<int:seller_pk>/", views.seller_home, name="seller_home"),
    path("seller/<int:seller_pk>/rules/", views.seller_rules, name="seller_rules"),
    path("staff/", views.staff_list, name="staff_list"),
    path("staff/new/", views.staff_new, name="staff_new"),
    path("staff/<int:pk>/", views.staff_edit, name="staff_edit"),
    path("staff/<int:pk>/badge.pdf", views.staff_badge, name="staff_badge"),
    path("staff/<int:pk>/<slug:action>/", views.staff_action, name="staff_action"),
    path("sellers/<int:pk>/", views.seller_edit, name="seller_edit"),
]
