from django.urls import path
from . import views

app_name = "administrador"

urlpatterns = [

    path("", views.dashboard, name="dashboard"),

    # crear usuario (modal)
    path("usuarios/crear/", views.crear_usuario, name="crear_usuario"),

    path("usuarios/", views.usuarios, name="usuarios"),
    path("roles/", views.roles, name="roles"),
    path("eventos/", views.eventos, name="eventos"),

    path("auditoria/", views.auditoria, name="auditoria"),
    path("auditoria/export/", views.auditoria_export_csv, name="auditoria_export"),

    path("reporte/csv/", views.reporte_csv, name="reporte_csv"),

    path("configuracion/", views.configuracion, name="configuracion"),
    path("perfil/actualizar/", views.perfil_actualizar, name="perfil_actualizar"),
    path("perfil/password/", views.perfil_cambiar_password, name="perfil_password"),

    path("salir/", views.salir, name="salir"),
    path("perfil/password/", views.perfil_cambiar_password, name="perfil_cambiar_password"),
]
