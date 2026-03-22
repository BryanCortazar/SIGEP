from __future__ import annotations

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import models
from django.dispatch import receiver


class Evento(models.Model):
    ESTADOS = (
        ("BORRADOR", "Borrador"),
        ("PUBLICADO", "Publicado"),
        ("CERRADO", "Cerrado"),
    )

    titulo = models.CharField(max_length=200)
    descripcion = models.TextField()
    fecha = models.DateField()
    lugar = models.CharField(max_length=150)
    cupo = models.PositiveIntegerField(default=0)
    estado = models.CharField(max_length=15, choices=ESTADOS, default="BORRADOR")

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eventos_creados",
    )

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "Evento"
        verbose_name_plural = "Eventos"

    def __str__(self) -> str:
        return f"{self.titulo}"


class ConfiguracionSistema(models.Model):
    clave = models.CharField(max_length=100, unique=True)
    valor = models.CharField(max_length=255)

    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="config_actualizada",
    )

    class Meta:
        verbose_name = "Configuración del sistema"
        verbose_name_plural = "Configuraciones del sistema"

    def __str__(self) -> str:
        return self.clave


class AuditoriaLog(models.Model):
    class Resultado(models.TextChoices):
        EXITOSO = "EXITOSO", "Exitoso"
        FALLIDO = "FALLIDO", "Fallido"
        ADVERTENCIA = "ADVERTENCIA", "Advertencia"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs_auditoria",
    )
    accion = models.CharField(max_length=255)
    modulo = models.CharField(max_length=80, blank=True, db_index=True)
    accion_tipo = models.CharField(max_length=80, blank=True, db_index=True)
    entidad = models.CharField(max_length=120, blank=True)
    objeto_id = models.CharField(max_length=60, blank=True)
    resultado = models.CharField(
        max_length=20,
        choices=Resultado.choices,
        default=Resultado.EXITOSO,
        db_index=True,
    )
    detalles = models.JSONField(default=dict, blank=True)
    fecha = models.DateTimeField(auto_now_add=True)

    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Auditoría"
        verbose_name_plural = "Auditoría"
        indexes = [
            models.Index(fields=["-fecha"]),
            models.Index(fields=["modulo", "accion_tipo"]),
            models.Index(fields=["resultado", "fecha"]),
        ]

    def __str__(self) -> str:
        return f"{self.fecha} - {self.accion}"


# =========================
# Señales globales de autenticación
# =========================
def _client_ip_from_request(request) -> str | None:
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def audit_user_logged_in(sender, request, user, **kwargs):
    try:
        AuditoriaLog.objects.create(
            usuario=user,
            accion="AUTENTICACION | LOGIN_EXITOSO",
            modulo="AUTENTICACION",
            accion_tipo="LOGIN",
            entidad="Sesion",
            resultado=AuditoriaLog.Resultado.EXITOSO,
            ip_origen=_client_ip_from_request(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2000] if request else "",
            detalles={"path": getattr(request, "path", "") if request else ""},
        )
    except Exception:
        pass


@receiver(user_logged_out)
def audit_user_logged_out(sender, request, user, **kwargs):
    try:
        AuditoriaLog.objects.create(
            usuario=user if getattr(user, "is_authenticated", False) else None,
            accion="AUTENTICACION | LOGOUT",
            modulo="AUTENTICACION",
            accion_tipo="LOGOUT",
            entidad="Sesion",
            resultado=AuditoriaLog.Resultado.EXITOSO,
            ip_origen=_client_ip_from_request(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2000] if request else "",
            detalles={"path": getattr(request, "path", "") if request else ""},
        )
    except Exception:
        pass


@receiver(user_login_failed)
def audit_user_login_failed(sender, credentials, request, **kwargs):
    identidad = ""
    if isinstance(credentials, dict):
        identidad = credentials.get("username") or credentials.get("email") or ""
    try:
        AuditoriaLog.objects.create(
            usuario=None,
            accion=f"AUTENTICACION | LOGIN_FALLIDO | usuario={identidad}",
            modulo="AUTENTICACION",
            accion_tipo="LOGIN_FALLIDO",
            entidad="Sesion",
            resultado=AuditoriaLog.Resultado.FALLIDO,
            ip_origen=_client_ip_from_request(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2000] if request else "",
            detalles={"identidad": identidad, "path": getattr(request, "path", "") if request else ""},
        )
    except Exception:
        pass