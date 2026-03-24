from django.urls import path
from . import views

app_name = "evaluador"

urlpatterns = [
    path("", views.panel, name="dashboard"),
    path("panel/", views.panel, name="panel"),
    path("proyectos/", views.proyectos_asignados, name="proyectos"),
    path("formulario/<int:proyecto_id>/", views.formulario, name="formulario"),
    path("horario/", views.mi_horario, name="horario"),
    path("historial/", views.historial, name="historial"),
    path("configuracion/", views.configuracion, name="configuracion"),
]
