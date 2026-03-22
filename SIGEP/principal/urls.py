from django.urls import path
from . import views

app_name = "principal"

urlpatterns = [
    # Home/landing
    path("", views.dashboard, name="dashboard"),

    # Auth
    path("login/", views.login_view, name="login"),
    path("registrar/", views.registrar_view, name="registrar"),
    path("recuperar/", views.recuperar_cuenta_view, name="recuperar"),
    path("logout/", views.salir, name="logout"),

    # Link para establecer contraseña (desde el correo del admin)
    path("set-password/<uidb64>/<token>/", views.set_password, name="set_password"),
]