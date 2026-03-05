from django.urls import path
from . import views

app_name = "ponente"

urlpatterns = [
    path("", views.panel, name="dashboard"),
    path("panel/", views.panel, name="panel"),

    # ✅ nueva opción
    path("inscripcion/", views.inscripcion, name="inscripcion"),

    path("participacion/", views.gestionar_participacion, name="participacion"),
    path("horario/", views.mi_horario, name="horario"),
    path("resultados/", views.mis_resultados, name="resultados"),
    path("historial/", views.historial, name="historial"),
    path("constancia/", views.generar_constancia, name="constancia"),
    path("configuracion/", views.configuracion, name="configuracion"),
]