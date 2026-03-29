from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Usuario(AbstractUser):
    """
    Modelo de usuario central de SIGEP.
    Se mantienen los campos estándar de AbstractUser para compatibilidad y se agregan
    campos explícitos para nombres y apellidos separados.
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
        help_text="Rol base operativo (redirección por módulo y control general).",
    )

    # Nuevos campos explícitos para el registro
    nombres = models.CharField("nombres", max_length=120, blank=True, default="")
    apellido_paterno = models.CharField("apellido paterno", max_length=80, blank=True, default="")
    apellido_materno = models.CharField("apellido materno", max_length=80, blank=True, default="")

    # Email único para autenticación/recuperación
    email = models.EmailField("correo electrónico", unique=True)

    telefono = models.CharField("teléfono", max_length=20, blank=True, null=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        """
        Mantiene sincronizados first_name/last_name para no romper compatibilidad
        con código existente que use get_full_name(), admin o librerías.
        """
        self.first_name = (self.nombres or "").strip()
        self.last_name = " ".join(
            part for part in [(self.apellido_paterno or "").strip(), (self.apellido_materno or "").strip()] if part
        ).strip()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        parts = [
            (self.nombres or "").strip(),
            (self.apellido_paterno or "").strip(),
            (self.apellido_materno or "").strip(),
        ]
        full_name = " ".join(part for part in parts if part).strip()
        return full_name or super().get_full_name().strip() or self.username

    @property
    def nombre_completo(self) -> str:
        return self.get_full_name()

    def __str__(self) -> str:
        return f"{self.username} ({self.get_rol_display()})"


class SolicitudRecuperacionCuenta(models.Model):
    """
    Flujo real de recuperación de cuenta.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    usuario = models.ForeignKey(
        "principal.Usuario",
        on_delete=models.CASCADE,
        related_name="recuperaciones",
    )

    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    creado_en = models.DateTimeField(default=timezone.now, db_index=True)
    usado_en = models.DateTimeField(blank=True, null=True)
    activo = models.BooleanField(default=True)

    ip_origen = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True, null=True)

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