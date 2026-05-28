from django.contrib import admin

#admin.site.register(ponente)
from django.contrib import admin

from .models import PerfilPonente, Ponencia


# ============================================================
# PERFIL DE PONENTE
# ============================================================
@admin.register(PerfilPonente)
class PerfilPonenteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "usuario",
        "institucion",
        "especialidad",
        "telefono",
        "creado_en",
        "actualizado_en",
    )

    search_fields = (
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "usuario__email",
        "institucion",
        "especialidad",
        "telefono",
    )

    list_filter = (
        "institucion",
        "especialidad",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "usuario",
    )

    ordering = (
        "-actualizado_en",
        "-id",
    )

    fieldsets = (
        ("Usuario asociado", {
            "fields": (
                "usuario",
            )
        }),
        ("Información profesional", {
            "fields": (
                "institucion",
                "especialidad",
                "telefono",
                "bio",
            )
        }),
        ("Documentos del perfil", {
            "fields": (
                "avatar",
                "cv",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )


# ============================================================
# PONENCIAS
# ============================================================
@admin.register(Ponencia)
class PonenciaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "titulo",
        "evento",
        "ponente",
        "estado",
        "cv_estado",
        "resena_estado",
        "diapositivas_estado",
        "estado_programacion",
        "fecha_programada",
        "horario_admin",
        "espacio_asignado",
        "constancia_habilitada",
        "actualizado_en",
    )

    search_fields = (
        "titulo",
        "tipo",
        "area_tematica",
        "resumen",
        "autores",
        "ponente__username",
        "ponente__first_name",
        "ponente__last_name",
        "ponente__email",
        "evento__titulo",
        "espacio_asignado",
        "folio_constancia",
    )

    list_filter = (
        "evento",
        "estado",
        "cv_estado",
        "resena_estado",
        "diapositivas_estado",
        "estado_programacion",
        "constancia_habilitada",
        "fecha_programada",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
        "porcentaje_documentacion_admin",
        "horario_admin",
        "nombre_evento_admin",
        "participacion_finalizada_admin",
        "puede_generar_constancia_admin",
    )

    raw_id_fields = (
        "evento",
        "ponente",
        "evaluacion_proyecto",
    )

    ordering = (
        "-actualizado_en",
        "-id",
    )

    date_hierarchy = "fecha_programada"

    actions = (
        "marcar_en_revision",
        "marcar_aceptada",
        "marcar_rechazada",
        "validar_cv",
        "validar_resena",
        "validar_diapositivas",
        "confirmar_programacion",
        "cancelar_programacion",
        "habilitar_constancia",
        "deshabilitar_constancia",
    )

    fieldsets = (
        ("Evento y ponente", {
            "fields": (
                "evento",
                "ponente",
                "evaluacion_proyecto",
            )
        }),
        ("Información general de la ponencia", {
            "fields": (
                "titulo",
                "tipo",
                "area_tematica",
                "resumen",
                "autores",
                "estado",
            )
        }),
        ("Archivos principales", {
            "fields": (
                "archivo_resumen",
                "presentacion",
            )
        }),
        ("Documentación complementaria", {
            "fields": (
                "cv_documento",
                "cv_estado",
                "resena_biografica",
                "resena_estado",
                "diapositivas_presentacion",
                "diapositivas_estado",
                "requerimientos_tecnicos",
                "porcentaje_documentacion_admin",
            )
        }),
        ("Programación de la ponencia", {
            "fields": (
                "fecha_programada",
                "hora_inicio",
                "hora_fin",
                "horario_admin",
                "espacio_asignado",
                "estado_programacion",
            )
        }),
        ("Constancia", {
            "fields": (
                "constancia_habilitada",
                "folio_constancia",
                "constancia_generada_en",
                "participacion_finalizada_admin",
                "puede_generar_constancia_admin",
            )
        }),
        ("Información calculada", {
            "fields": (
                "nombre_evento_admin",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )

    @admin.display(description="Horario")
    def horario_admin(self, obj):
        return obj.rango_horario()

    @admin.display(description="Evento")
    def nombre_evento_admin(self, obj):
        return obj.nombre_evento()

    @admin.display(description="Documentación (%)")
    def porcentaje_documentacion_admin(self, obj):
        return f"{obj.porcentaje_documentacion()}%"

    @admin.display(description="Participación finalizada", boolean=True)
    def participacion_finalizada_admin(self, obj):
        return obj.participacion_finalizada()

    @admin.display(description="Puede generar constancia", boolean=True)
    def puede_generar_constancia_admin(self, obj):
        return obj.puede_generar_constancia()

    @admin.action(description="Marcar ponencias seleccionadas como en revisión")
    def marcar_en_revision(self, request, queryset):
        queryset.update(estado=Ponencia.ESTADO_EN_REVISION)

    @admin.action(description="Marcar ponencias seleccionadas como aceptadas")
    def marcar_aceptada(self, request, queryset):
        queryset.update(estado=Ponencia.ESTADO_ACEPTADA)

    @admin.action(description="Marcar ponencias seleccionadas como rechazadas")
    def marcar_rechazada(self, request, queryset):
        queryset.update(estado=Ponencia.ESTADO_RECHAZADA)

    @admin.action(description="Validar CV de las ponencias seleccionadas")
    def validar_cv(self, request, queryset):
        queryset.update(cv_estado=Ponencia.DOC_VALIDADO)

    @admin.action(description="Validar reseña biográfica de las ponencias seleccionadas")
    def validar_resena(self, request, queryset):
        queryset.update(resena_estado=Ponencia.DOC_VALIDADO)

    @admin.action(description="Validar diapositivas de las ponencias seleccionadas")
    def validar_diapositivas(self, request, queryset):
        queryset.update(diapositivas_estado=Ponencia.DOC_VALIDADO)

    @admin.action(description="Confirmar programación de las ponencias seleccionadas")
    def confirmar_programacion(self, request, queryset):
        queryset.update(estado_programacion=Ponencia.PROG_CONFIRMADO)

    @admin.action(description="Cancelar programación de las ponencias seleccionadas")
    def cancelar_programacion(self, request, queryset):
        queryset.update(estado_programacion=Ponencia.PROG_CANCELADO)

    @admin.action(description="Habilitar constancia para las ponencias seleccionadas")
    def habilitar_constancia(self, request, queryset):
        queryset.update(constancia_habilitada=True)

    @admin.action(description="Deshabilitar constancia para las ponencias seleccionadas")
    def deshabilitar_constancia(self, request, queryset):
        queryset.update(constancia_habilitada=False)