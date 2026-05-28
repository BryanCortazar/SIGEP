from django.contrib import admin

from django.contrib import admin

from .models import (
    PerfilUsuario,
    ActividadCronograma,
    Inscripcion,
    EvaluacionProyecto,
    EvaluacionAsignacion,
    Rubrica,
    RubricaCriterio,
    RubricaAdjunto,
    Espacio,
    Reporte,
)


# ============================================================
# PERFIL DE USUARIO
# ============================================================
@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "usuario",
        "puesto",
        "institucion",
        "telefono",
        "creado_en",
        "actualizado_en",
    )

    search_fields = (
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "usuario__email",
        "puesto",
        "institucion",
        "telefono",
    )

    list_filter = (
        "institucion",
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
    )

    fieldsets = (
        ("Usuario asociado", {
            "fields": (
                "usuario",
            )
        }),
        ("Información del perfil", {
            "fields": (
                "puesto",
                "institucion",
                "telefono",
                "bio",
            )
        }),
        ("Archivos del perfil", {
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
# CRONOGRAMA
# ============================================================
@admin.register(ActividadCronograma)
class ActividadCronogramaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "titulo",
        "inicio",
        "fin",
        "responsable",
        "creado_en",
    )

    search_fields = (
        "titulo",
        "responsable",
        "evento__titulo",
    )

    list_filter = (
        "evento",
        "inicio",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "evento",
    )

    ordering = (
        "evento",
        "inicio",
        "fin",
        "id",
    )

    fieldsets = (
        ("Evento", {
            "fields": (
                "evento",
            )
        }),
        ("Datos de la actividad", {
            "fields": (
                "titulo",
                "inicio",
                "fin",
                "responsable",
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
# INSCRIPCIONES
# ============================================================
@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "usuario",
        "rol",
        "creado_en",
        "actualizado_en",
    )

    search_fields = (
        "evento__titulo",
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "usuario__email",
        "rol",
    )

    list_filter = (
        "rol",
        "evento",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "evento",
        "usuario",
    )

    ordering = (
        "evento",
        "rol",
        "usuario",
    )

    fieldsets = (
        ("Evento y usuario", {
            "fields": (
                "evento",
                "usuario",
            )
        }),
        ("Rol dentro del evento", {
            "fields": (
                "rol",
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
# EVALUACIONES / PROYECTOS EVALUABLES
# ============================================================
class EvaluacionAsignacionInline(admin.TabularInline):
    model = EvaluacionAsignacion
    extra = 1
    raw_id_fields = (
        "evaluador",
    )

    fields = (
        "evaluador",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )


@admin.register(EvaluacionProyecto)
class EvaluacionProyectoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "titulo",
        "ponente",
        "inicio",
        "fin",
        "lugar",
        "creado_en",
    )

    search_fields = (
        "titulo",
        "ponente",
        "lugar",
        "evento__titulo",
    )

    list_filter = (
        "evento",
        "inicio",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "evento",
    )

    ordering = (
        "evento",
        "inicio",
        "fin",
        "id",
    )

    inlines = (
        EvaluacionAsignacionInline,
    )

    fieldsets = (
        ("Evento", {
            "fields": (
                "evento",
            )
        }),
        ("Proyecto evaluable", {
            "fields": (
                "titulo",
                "ponente",
                "inicio",
                "fin",
                "lugar",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )


@admin.register(EvaluacionAsignacion)
class EvaluacionAsignacionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "proyecto",
        "evaluador",
        "evento_del_proyecto",
        "inicio_del_proyecto",
        "fin_del_proyecto",
        "creado_en",
    )

    search_fields = (
        "proyecto__titulo",
        "proyecto__ponente",
        "proyecto__evento__titulo",
        "evaluador__username",
        "evaluador__first_name",
        "evaluador__last_name",
        "evaluador__email",
    )

    list_filter = (
        "proyecto__evento",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "proyecto",
        "evaluador",
    )

    ordering = (
        "proyecto__evento",
        "proyecto__inicio",
        "evaluador",
    )

    def evento_del_proyecto(self, obj):
        return obj.proyecto.evento if obj.proyecto_id else "-"

    evento_del_proyecto.short_description = "Evento"

    def inicio_del_proyecto(self, obj):
        return obj.proyecto.inicio if obj.proyecto_id else "-"

    inicio_del_proyecto.short_description = "Inicio"

    def fin_del_proyecto(self, obj):
        return obj.proyecto.fin if obj.proyecto_id else "-"

    fin_del_proyecto.short_description = "Fin"


# ============================================================
# RÚBRICAS
# ============================================================
class RubricaCriterioInline(admin.TabularInline):
    model = RubricaCriterio
    extra = 1

    fields = (
        "orden",
        "titulo",
        "descripcion",
        "puntaje_max",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    ordering = (
        "orden",
        "id",
    )


class RubricaAdjuntoInline(admin.TabularInline):
    model = RubricaAdjunto
    extra = 1

    fields = (
        "archivo",
        "nombre_original",
        "tamano",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "tamano",
        "creado_en",
        "actualizado_en",
    )


@admin.register(Rubrica)
class RubricaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "titulo",
        "estado",
        "tipo_objetivo",
        "objetivo_titulo",
        "puntaje_total",
        "actualizado_en",
    )

    search_fields = (
        "titulo",
        "evento__titulo",
        "proyecto__titulo",
        "ponencia__titulo",
    )

    list_filter = (
        "estado",
        "evento",
        "creado_en",
        "actualizado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
        "puntaje_total",
        "tipo_objetivo",
        "objetivo_titulo",
    )

    raw_id_fields = (
        "evento",
        "proyecto",
        "ponencia",
    )

    ordering = (
        "-actualizado_en",
        "-id",
    )

    inlines = (
        RubricaCriterioInline,
        RubricaAdjuntoInline,
    )

    actions = (
        "activar_rubricas",
        "regresar_a_borrador",
    )

    fieldsets = (
        ("Evento", {
            "fields": (
                "evento",
            )
        }),
        ("Información de la rúbrica", {
            "fields": (
                "titulo",
                "estado",
            )
        }),
        ("Objetivo evaluable", {
            "fields": (
                "proyecto",
                "ponencia",
                "tipo_objetivo",
                "objetivo_titulo",
            )
        }),
        ("Resumen de evaluación", {
            "fields": (
                "puntaje_total",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )

    def puntaje_total(self, obj):
        return obj.puntaje_maximo

    puntaje_total.short_description = "Puntaje máximo"

    @admin.action(description="Activar rúbricas seleccionadas")
    def activar_rubricas(self, request, queryset):
        queryset.update(estado="ACTIVA")

    @admin.action(description="Regresar rúbricas seleccionadas a borrador")
    def regresar_a_borrador(self, request, queryset):
        queryset.update(estado="BORRADOR")


@admin.register(RubricaCriterio)
class RubricaCriterioAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rubrica",
        "orden",
        "titulo",
        "puntaje_max",
        "creado_en",
    )

    search_fields = (
        "titulo",
        "descripcion",
        "rubrica__titulo",
        "rubrica__evento__titulo",
    )

    list_filter = (
        "rubrica__evento",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "rubrica",
    )

    ordering = (
        "rubrica",
        "orden",
        "id",
    )


@admin.register(RubricaAdjunto)
class RubricaAdjuntoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rubrica",
        "nombre_original",
        "tamano",
        "creado_en",
    )

    search_fields = (
        "nombre_original",
        "rubrica__titulo",
        "rubrica__evento__titulo",
    )

    list_filter = (
        "rubrica__evento",
        "creado_en",
    )

    readonly_fields = (
        "tamano",
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "rubrica",
    )

    ordering = (
        "-id",
    )


# ============================================================
# ESPACIOS
# ============================================================
@admin.register(Espacio)
class EspacioAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "nombre",
        "tipo",
        "estado",
        "capacidad",
        "inicio",
        "fin",
        "tipo_objetivo",
        "objetivo_titulo",
        "actualizado_en",
    )

    search_fields = (
        "nombre",
        "ubicacion",
        "tags",
        "evento__titulo",
        "proyecto__titulo",
        "ponencia__titulo",
    )

    list_filter = (
        "evento",
        "tipo",
        "estado",
        "inicio",
        "creado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
        "tipo_objetivo",
        "objetivo_titulo",
        "objetivo_responsable",
    )

    raw_id_fields = (
        "evento",
        "proyecto",
        "ponencia",
    )

    ordering = (
        "evento",
        "inicio",
        "fin",
        "nombre",
        "id",
    )

    actions = (
        "marcar_disponible",
        "marcar_ocupado",
        "marcar_mantenimiento",
    )

    fieldsets = (
        ("Evento", {
            "fields": (
                "evento",
            )
        }),
        ("Datos del espacio", {
            "fields": (
                "nombre",
                "tipo",
                "capacidad",
                "ubicacion",
                "tags",
                "estado",
            )
        }),
        ("Horario de uso", {
            "fields": (
                "inicio",
                "fin",
            )
        }),
        ("Asignación", {
            "fields": (
                "proyecto",
                "ponencia",
                "tipo_objetivo",
                "objetivo_titulo",
                "objetivo_responsable",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )

    @admin.action(description="Marcar espacios seleccionados como disponibles")
    def marcar_disponible(self, request, queryset):
        queryset.update(estado="DISPONIBLE")

    @admin.action(description="Marcar espacios seleccionados como ocupados")
    def marcar_ocupado(self, request, queryset):
        queryset.update(estado="OCUPADO")

    @admin.action(description="Marcar espacios seleccionados en mantenimiento")
    def marcar_mantenimiento(self, request, queryset):
        queryset.update(estado="MANTENIMIENTO")


# ============================================================
# REPORTES
# ============================================================
@admin.register(Reporte)
class ReporteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "evento",
        "nombre",
        "categoria",
        "formato",
        "modo",
        "estado",
        "creado_por",
        "generado_en",
    )

    search_fields = (
        "nombre",
        "nombre_original",
        "evento__titulo",
        "proyecto__titulo",
        "creado_por__username",
        "creado_por__first_name",
        "creado_por__last_name",
        "creado_por__email",
    )

    list_filter = (
        "evento",
        "categoria",
        "formato",
        "modo",
        "estado",
        "generado_en",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "evento",
        "proyecto",
        "creado_por",
    )

    ordering = (
        "-generado_en",
        "-id",
    )

    date_hierarchy = "generado_en"

    actions = (
        "marcar_archivado",
        "marcar_listo",
    )

    fieldsets = (
        ("Evento", {
            "fields": (
                "evento",
                "proyecto",
            )
        }),
        ("Información del reporte", {
            "fields": (
                "nombre",
                "nombre_original",
                "categoria",
                "formato",
                "modo",
                "estado",
            )
        }),
        ("Archivo generado", {
            "fields": (
                "archivo",
                "generado_en",
            )
        }),
        ("Responsable", {
            "fields": (
                "creado_por",
            )
        }),
        ("Fechas de control", {
            "fields": (
                "creado_en",
                "actualizado_en",
            )
        }),
    )

    @admin.action(description="Archivar reportes seleccionados")
    def marcar_archivado(self, request, queryset):
        queryset.update(estado="ARCHIVADO")

    @admin.action(description="Marcar reportes seleccionados como listos")
    def marcar_listo(self, request, queryset):
        queryset.update(estado="LISTO")