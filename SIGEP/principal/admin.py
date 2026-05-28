from django.contrib import admin

# Register your models here.
from django.contrib.auth.admin import UserAdmin

from .models import Usuario, SolicitudRecuperacionCuenta


# ============================================================
# USUARIOS DEL SISTEMA
# ============================================================
@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = (
        "id",
        "username",
        "email",
        "nombre_completo_admin",
        "rol",
        "telefono",
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined",
        "actualizado_en",
    )

    list_filter = (
        "rol",
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
        "date_joined",
        "actualizado_en",
    )

    search_fields = (
        "username",
        "email",
        "nombres",
        "apellido_paterno",
        "apellido_materno",
        "first_name",
        "last_name",
        "telefono",
    )

    ordering = (
        "username",
    )

    readonly_fields = (
        "last_login",
        "date_joined",
        "actualizado_en",
        "nombre_completo_admin",
    )

    filter_horizontal = (
        "groups",
        "user_permissions",
    )

    fieldsets = (
        ("Credenciales de acceso", {
            "fields": (
                "username",
                "password",
            )
        }),
        ("Información personal", {
            "fields": (
                "nombres",
                "apellido_paterno",
                "apellido_materno",
                "nombre_completo_admin",
                "email",
                "telefono",
                "foto",
            )
        }),
        ("Rol operativo SIGEP", {
            "fields": (
                "rol",
            )
        }),
        ("Permisos de Django", {
            "fields": (
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        }),
        ("Fechas importantes", {
            "fields": (
                "last_login",
                "date_joined",
                "actualizado_en",
            )
        }),
    )

    add_fieldsets = (
        ("Crear nuevo usuario", {
            "classes": (
                "wide",
            ),
            "fields": (
                "username",
                "email",
                "nombres",
                "apellido_paterno",
                "apellido_materno",
                "telefono",
                "rol",
                "foto",
                "password1",
                "password2",
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            ),
        }),
    )

    @admin.display(description="Nombre completo")
    def nombre_completo_admin(self, obj):
        return obj.get_full_name()


# ============================================================
# SOLICITUDES DE RECUPERACIÓN DE CUENTA
# ============================================================
@admin.register(SolicitudRecuperacionCuenta)
class SolicitudRecuperacionCuentaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "usuario",
        "activo",
        "esta_expirada_admin",
        "creado_en",
        "expira_en",
        "usado_en",
        "ip_origen",
    )

    list_filter = (
        "activo",
        "creado_en",
        "expira_en",
        "usado_en",
    )

    search_fields = (
        "usuario__username",
        "usuario__email",
        "usuario__nombres",
        "usuario__apellido_paterno",
        "usuario__apellido_materno",
        "token",
        "ip_origen",
        "user_agent",
    )

    readonly_fields = (
        "id",
        "token",
        "creado_en",
        "usado_en",
        "esta_expirada_admin",
    )

    raw_id_fields = (
        "usuario",
    )

    ordering = (
        "-creado_en",
    )

    date_hierarchy = "creado_en"

    actions = (
        "invalidar_solicitudes",
        "reactivar_solicitudes",
    )

    fieldsets = (
        ("Usuario asociado", {
            "fields": (
                "usuario",
            )
        }),
        ("Datos de recuperación", {
            "fields": (
                "id",
                "token",
                "activo",
                "creado_en",
                "expira_en",
                "usado_en",
                "esta_expirada_admin",
            )
        }),
        ("Información técnica", {
            "fields": (
                "ip_origen",
                "user_agent",
            )
        }),
    )

    @admin.display(description="Expirada", boolean=True)
    def esta_expirada_admin(self, obj):
        return obj.esta_expirada()

    @admin.action(description="Invalidar solicitudes seleccionadas")
    def invalidar_solicitudes(self, request, queryset):
        for solicitud in queryset:
            solicitud.invalidar()

    @admin.action(description="Reactivar solicitudes seleccionadas")
    def reactivar_solicitudes(self, request, queryset):
        queryset.update(
            activo=True,
            usado_en=None,
        )