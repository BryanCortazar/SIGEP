from django.contrib import admin

from django.contrib import admin
from .models import Evento, ConfiguracionSistema, AuditoriaLog


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "titulo",
        "fecha",
        "lugar",
        "cupo",
        "estado",
        "creado_por",
        "creado_en",
        "actualizado_en",
    )

    list_filter = (
        "estado",
        "fecha",
        "creado_en",
    )

    search_fields = (
        "titulo",
        "descripcion",
        "lugar",
        "creado_por__username",
        "creado_por__first_name",
        "creado_por__last_name",
        "creado_por__email",
    )

    readonly_fields = (
        "creado_en",
        "actualizado_en",
    )

    raw_id_fields = (
        "creado_por",
    )

    ordering = (
        "-creado_en",
    )

    date_hierarchy = "fecha"

    fieldsets = (
        ("Información general del evento", {
            "fields": (
                "titulo",
                "descripcion",
                "fecha",
                "lugar",
                "cupo",
                "estado",
            )
        }),
        ("Responsable de creación", {
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

    actions = (
        "publicar_eventos",
        "cerrar_eventos",
        "regresar_a_borrador",
    )

    @admin.action(description="Publicar eventos seleccionados")
    def publicar_eventos(self, request, queryset):
        queryset.update(estado="PUBLICADO")

    @admin.action(description="Cerrar eventos seleccionados")
    def cerrar_eventos(self, request, queryset):
        queryset.update(estado="CERRADO")

    @admin.action(description="Regresar eventos seleccionados a borrador")
    def regresar_a_borrador(self, request, queryset):
        queryset.update(estado="BORRADOR")


@admin.register(ConfiguracionSistema)
class ConfiguracionSistemaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "clave",
        "valor",
        "actualizado_por",
        "actualizado_en",
    )

    search_fields = (
        "clave",
        "valor",
        "actualizado_por__username",
        "actualizado_por__email",
    )

    list_filter = (
        "actualizado_en",
    )

    readonly_fields = (
        "actualizado_en",
    )

    raw_id_fields = (
        "actualizado_por",
    )

    ordering = (
        "clave",
    )

    fieldsets = (
        ("Parámetros de configuración", {
            "fields": (
                "clave",
                "valor",
            )
        }),
        ("Control de actualización", {
            "fields": (
                "actualizado_por",
                "actualizado_en",
            )
        }),
    )


@admin.register(AuditoriaLog)
class AuditoriaLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "fecha",
        "usuario",
        "modulo",
        "accion_tipo",
        "entidad",
        "objeto_id",
        "resultado",
        "ip_origen",
    )

    list_filter = (
        "resultado",
        "modulo",
        "accion_tipo",
        "fecha",
    )

    search_fields = (
        "accion",
        "modulo",
        "accion_tipo",
        "entidad",
        "objeto_id",
        "usuario__username",
        "usuario__first_name",
        "usuario__last_name",
        "usuario__email",
        "ip_origen",
        "user_agent",
    )

    readonly_fields = (
        "usuario",
        "accion",
        "modulo",
        "accion_tipo",
        "entidad",
        "objeto_id",
        "resultado",
        "detalles",
        "fecha",
        "ip_origen",
        "user_agent",
    )

    raw_id_fields = (
        "usuario",
    )

    ordering = (
        "-fecha",
    )

    date_hierarchy = "fecha"

    fieldsets = (
        ("Información de auditoría", {
            "fields": (
                "usuario",
                "accion",
                "modulo",
                "accion_tipo",
                "entidad",
                "objeto_id",
                "resultado",
                "fecha",
            )
        }),
        ("Detalles técnicos", {
            "fields": (
                "ip_origen",
                "user_agent",
                "detalles",
            )
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
