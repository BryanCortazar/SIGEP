# principal/models.py
from __future__ import annotations

import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Usuario(AbstractUser):
    """
    Modelo de usuario central de SIGEP (entorno real).
    - Autenticación y control del rol base para redirección por módulo.
    - Email único (recuperación y comunicación confiable).
    - Extensible para relaciones futuras sin romper el sistema.
    """
    foto = models.ImageField(upload_to="perfiles/", null=True, blank=True)

    class Rol(models.TextChoices):
        ADMINISTRADOR = "ADMIN", "Administrador"
        COORDINADOR = "COOR", "Coordinador"
        EVALUADOR = "EVAL", "Evaluador/Jurado"
        PONENTE = "PON", "Ponente"
        PARTICIPANTE = "PART", "Participante"

    rol = models.CharField(
        max_length=5,
        choices=Rol.choices,
        default=Rol.PARTICIPANTE,
        db_index=True,
        help_text="Rol base operativo (redirección por módulo y control general)."
    )

    # AbstractUser trae email, pero no es único; aquí lo hacemos único (producción)
    email = models.EmailField("correo electrónico", unique=True)

    telefono = models.CharField("teléfono", max_length=20, blank=True, null=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.username} ({self.get_rol_display()})"


class SolicitudRecuperacionCuenta(models.Model):
    """
    Flujo real de recuperación de cuenta.
    - Token único UUID
    - Expira
    - Se invalida al usarse
    - Guarda trazabilidad (IP/UA) para auditoría/seguridad
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    usuario = models.ForeignKey(
        "principal.Usuario",
        on_delete=models.CASCADE,
        related_name="recuperaciones"
    )

    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    creado_en = models.DateTimeField(default=timezone.now, db_index=True)
    usado_en = models.DateTimeField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    # Seguridad / auditoría
    ip_origen = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True, null=True)

    # Expiración (por ejemplo 30 minutos)
    expira_en = models.DateTimeField()

    class Meta:
        ordering = ["-creado_en"]
        indexes = [
            models.Index(fields=["token"]),
            models.Index(fields=["usuario", "activo"]),
        ]

    def esta_expirada(self) -> bool:
        return timezone.now() >= self.expira_en

    def invalidar(self) -> None:
        self.activo = False
        if not self.usado_en:
            self.usado_en = timezone.now()
        self.save(update_fields=["activo", "usado_en"])

    def __str__(self) -> str:
        return f"Recuperación {self.usuario.username} - {self.creado_en:%Y-%m-%d %H:%M}"