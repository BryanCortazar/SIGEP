from django.urls import path
from . import views

app_name = "coordinador"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("evento/crear/", views.evento_crear, name="evento_crear"),
    path("evento/seleccionar/", views.evento_seleccionar, name="evento_seleccionar"),

    path("gestion/", views.gestion_evento, name="gestion_evento"),

    path("cronograma/", views.cronograma, name="cronograma"),
    path("cronograma/guardar/", views.cronograma_guardar, name="cronograma_guardar"),
    path("cronograma/<int:pk>/eliminar/", views.cronograma_eliminar, name="cronograma_eliminar"),

    path("inscripciones/", views.inscripciones, name="inscripciones"),
    path("inscripciones/guardar/", views.inscripcion_guardar, name="inscripcion_guardar"),
    path("inscripciones/<int:user_id>/eliminar/", views.inscripcion_eliminar, name="inscripcion_eliminar"),
    path("inscripciones/exportar/csv/", views.inscripciones_exportar_csv, name="inscripciones_exportar_csv"),

    path("evaluadores/", views.evaluadores, name="evaluadores"),
    path("evaluadores/proyecto/guardar/", views.eval_proyecto_guardar, name="eval_proyecto_guardar"),
    path("evaluadores/proyecto/<int:pk>/eliminar/", views.eval_proyecto_eliminar, name="eval_proyecto_eliminar"),
    path("evaluadores/gestionar/guardar/", views.eval_gestionar_guardar, name="eval_gestionar_guardar"),

    path("espacios/", views.espacios, name="espacios"),
    path("espacios/guardar/", views.espacio_guardar, name="espacio_guardar"),
    path("espacios/<int:pk>/eliminar/", views.espacio_eliminar, name="espacio_eliminar"),

    path("reportes/", views.reportes, name="reportes"),
    path("reportes/generar/", views.reporte_generar, name="reporte_generar"),
    path("reportes/<int:pk>/descargar/", views.reporte_descargar, name="reporte_descargar"),
    path("reportes/<int:pk>/editar/", views.reporte_editar, name="reporte_editar"),
    path("reportes/<int:pk>/eliminar/", views.reporte_eliminar, name="reporte_eliminar"),

    path("configuracion/", views.configuracion, name="configuracion"),

    path("rubricas/", views.rubricas, name="rubricas"),
    path("rubricas/guardar/", views.rubrica_guardar, name="rubrica_guardar"),
    path("rubricas/<int:pk>/eliminar/", views.rubrica_eliminar, name="rubrica_eliminar"),
]