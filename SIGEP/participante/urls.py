from django.urls import path

from . import views

app_name = "participante"

urlpatterns = [
    path("", views.panel_participante, name="panel_participante"),
    path("panel/", views.panel_participante, name="panel"),
    path("evento/elegir/", views.elegir_evento, name="elegir_evento"),
    path("evento/<int:pk>/eliminar/", views.eliminar_proyecto, name="eliminar_proyecto"),
    path("programa/", views.programa, name="programa"),
    path("pase/", views.mi_pase, name="mi_pase"),
    path("constancia/", views.constancia, name="constancia"),
    path("configuracion/", views.configuracion, name="configuracion"),
]
