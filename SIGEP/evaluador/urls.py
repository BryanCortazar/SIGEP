from django.urls import path
from . import views

app_name = "evaluador"

urlpatterns = [
    # Home del módulo
    path("", views.panel, name="dashboard"),     # mantiene name="dashboard" si ya lo usas en templates
    path("panel/", views.panel, name="panel"),

    # Opción 2
    path("proyectos/", views.proyectos_asignados, name="proyectos"),

    # Opción 3 (tu ruta exige proyecto_id)
    path("formulario/<int:proyecto_id>/", views.formulario, name="formulario"),

    # Opción 4
    path("horario/", views.mi_horario, name="horario"),

    # Historial
    path("historial/", views.historial, name="historial"),

    # Configuración
    path("configuracion/", views.configuracion, name="configuracion"),
]