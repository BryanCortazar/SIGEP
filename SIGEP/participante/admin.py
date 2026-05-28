from django.contrib import admin

# from .models import participante
#admin.site.register(participante)

from .models import PerfilParticipante, ProyectoParticipante


@admin.register(PerfilParticipante)
class PerfilParticipanteAdmin(admin.ModelAdmin):
    list_display = ("usuario", "institucion", "telefono", "actualizado_en")
    search_fields = ("usuario__username", "usuario__email", "institucion", "telefono")
    list_select_related = ("usuario",)


@admin.register(ProyectoParticipante)
class ProyectoParticipanteAdmin(admin.ModelAdmin):
    list_display = (
        "nombre_proyecto",
        "evento",
        "participante",
        "categoria",
        "estado",
        "estado_programacion",
        "creado_en",
    )
    list_filter = ("categoria", "estado", "estado_programacion", "evento")
    search_fields = (
        "nombre_proyecto",
        "nombre_participante",
        "correo",
        "participante__username",
        "participante__email",
    )
    list_select_related = ("evento", "participante", "evaluacion_proyecto")
    raw_id_fields = ("evento", "participante", "evaluacion_proyecto")