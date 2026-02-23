from django.urls import path
from . import views

app_name = "coordinador"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("eventos/", views.eventos, name="eventos"),
    path("cronograma/", views.cronograma, name="cronograma"),
    path("inscripciones/", views.inscripciones, name="inscripciones"),
    path("evaluadores/", views.asignacion_evaluadores, name="evaluadores"),
    path("rubricas/", views.rubricas, name="rubricas"),
    path("espacios/", views.asignacion_espacios, name="espacios"),
    path("reportes/", views.reportes, name="reportes"),
    path("configuracion/", views.configuracion, name="configuracion"),
]