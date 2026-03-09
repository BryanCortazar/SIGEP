from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

EVENTO_MODEL = "administrador.Evento"


def validate_file_size(file, max_mb: int, label: str):
    max_bytes = max_mb * 1024 * 1024
    size = int(getattr(file, "size", 0) or 0)
    if size > max_bytes:
        raise ValidationError(f"{label} excede el tamaño permitido de {max_mb} MB.")


def validate_avatar_file(file):
    ext = os.path.splitext(getattr(file, "name", ""))[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise ValidationError("La imagen debe estar en formato JPG, JPEG, PNG o WebP.")
    validate_file_size(file, 5, "La foto de perfil")


def upload_avatar(instance, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower() or ".jpg"
    return f"participante/avatar/user_{instance.user_id}/{uuid.uuid4().hex}{ext}"


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PerfilParticipante(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfilparticipante",
    )
    telefono = models.CharField(max_length=30, blank=True, default="")
    institucion = models.CharField(max_length=180, blank=True, default="")
    carrera = models.CharField(max_length=180, blank=True, default="")
    bio = models.TextField(blank=True, default="")
    avatar = models.FileField(
        upload_to=upload_avatar,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["jpg", "jpeg", "png", "webp"]),
            validate_avatar_file,
        ],
    )

    class Meta:
        verbose_name = "Perfil de participante"
        verbose_name_plural = "Perfiles de participantes"

    def __str__(self):
        return f"Perfil participante - {self.user}"


class InscripcionParticipante(TimeStampedModel):
    ESTADO_PREINSCRITO = "PREINSCRITO"
    ESTADO_CONFIRMADO = "CONFIRMADO"
    ESTADO_CANCELADO = "CANCELADO"
    ESTADO_RECHAZADO = "RECHAZADO"

    ESTADOS_INSCRIPCION = (
        (ESTADO_PREINSCRITO, "Preinscrito"),
        (ESTADO_CONFIRMADO, "Confirmado"),
        (ESTADO_CANCELADO, "Cancelado"),
        (ESTADO_RECHAZADO, "Rechazado"),
    )

    participante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones_participante",
    )
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones_participantes",
    )

    estado_inscripcion = models.CharField(
        max_length=20,
        choices=ESTADOS_INSCRIPCION,
        default=ESTADO_PREINSCRITO,
    )

    folio_inscripcion = models.CharField(max_length=50, blank=True, default="")
    codigo_pase = models.CharField(max_length=80, blank=True, default="")
    tipo_acceso = models.CharField(max_length=100, blank=True, default="Participante")
    pase_generado_en = models.DateTimeField(null=True, blank=True)

    asistencia_confirmada = models.BooleanField(default=False)

    constancia_habilitada = models.BooleanField(default=False)
    folio_constancia = models.CharField(max_length=50, blank=True, default="")
    constancia_generada_en = models.DateTimeField(null=True, blank=True)

    observaciones = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = "Inscripción de participante"
        verbose_name_plural = "Inscripciones de participantes"
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["participante", "evento"]),
            models.Index(fields=["estado_inscripcion"]),
            models.Index(fields=["constancia_habilitada"]),
            models.Index(fields=["asistencia_confirmada"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["participante", "evento"],
                name="uq_inscripcion_participante_evento",
            )
        ]

    def nombre_evento(self) -> str:
        if hasattr(self.evento, "nombre") and self.evento.nombre:
            return self.evento.nombre
        if hasattr(self.evento, "titulo") and self.evento.titulo:
            return self.evento.titulo
        return "Evento"

    def fecha_evento(self):
        if hasattr(self.evento, "fecha") and self.evento.fecha:
            return self.evento.fecha
        if hasattr(self.evento, "fecha_inicio") and self.evento.fecha_inicio:
            return self.evento.fecha_inicio
        return None

    def fecha_fin_evento(self):
        if hasattr(self.evento, "fecha_fin") and self.evento.fecha_fin:
            return self.evento.fecha_fin
        if hasattr(self.evento, "fecha") and self.evento.fecha:
            return self.evento.fecha
        if hasattr(self.evento, "fecha_inicio") and self.evento.fecha_inicio:
            return self.evento.fecha_inicio
        return None

    def lugar_evento(self) -> str:
        if hasattr(self.evento, "lugar") and self.evento.lugar:
            return self.evento.lugar
        if hasattr(self.evento, "ubicacion") and self.evento.ubicacion:
            return self.evento.ubicacion
        if hasattr(self.evento, "sede") and self.evento.sede:
            return self.evento.sede
        return "Por definir"

    def evento_finalizado(self) -> bool:
        fecha_fin = self.fecha_fin_evento()
        if not fecha_fin:
            return False
        return fecha_fin <= timezone.localdate()

    def puede_descargar_pase(self) -> bool:
        return self.estado_inscripcion in {
            self.ESTADO_PREINSCRITO,
            self.ESTADO_CONFIRMADO,
        }

    def puede_descargar_constancia(self) -> bool:
        return (
            self.estado_inscripcion == self.ESTADO_CONFIRMADO
            and self.asistencia_confirmada
            and (self.constancia_habilitada or self.evento_finalizado())
        )

    def generar_folio_inscripcion(self) -> str:
        fecha = timezone.localdate().strftime("%Y%m%d")
        return f"INS-PAR-{fecha}-{self.id:06d}"

    def generar_codigo_pase(self) -> str:
        fecha = timezone.localdate().strftime("%Y%m%d")
        return f"PASE-PAR-{fecha}-{self.id:06d}"

    def generar_folio_constancia(self) -> str:
        fecha = timezone.localdate().strftime("%Y%m%d")
        return f"CONST-PAR-{fecha}-{self.id:06d}"

    def __str__(self):
        return f"{self.participante} - {self.nombre_evento()}"