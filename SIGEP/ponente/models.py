from __future__ import annotations

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
        raise ValidationError(f"{label} excede el tamaño permitido ({max_mb}MB).")


def validate_presentacion_size(file):
    validate_file_size(file, 25, "La presentación")


def validate_resumen_size(file):
    validate_file_size(file, 10, "El resumen")


def upload_presentacion(instance, filename: str) -> str:
    return f"ponente/presentaciones/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


def upload_resumen(instance, filename: str) -> str:
    return f"ponente/resumenes/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Ponencia(TimeStampedModel):
    ESTADO_REGISTRADA = "REGISTRADA"
    ESTADO_EN_REVISION = "EN_REVISION"
    ESTADO_ACEPTADA = "ACEPTADA"
    ESTADO_RECHAZADA = "RECHAZADA"

    ESTADOS = (
        (ESTADO_REGISTRADA, "Registrada"),
        (ESTADO_EN_REVISION, "En revisión"),
        (ESTADO_ACEPTADA, "Aceptada"),
        (ESTADO_RECHAZADA, "Rechazada"),
    )

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="ponencias_ponente",
    )

    ponente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ponencias_creadas_ponente",
    )

    # ✅ vínculo estable hacia el “proyecto evaluable” que crea el Coordinador al aprobar
    evaluacion_proyecto = models.OneToOneField(
        "evaluador.EvaluacionProyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ponencia_origen",
    )

    titulo = models.CharField(max_length=240)
    tipo = models.CharField(max_length=120, blank=True, default="")  # texto libre
    area_tematica = models.CharField(max_length=160, blank=True, default="")
    resumen = models.TextField(blank=True, default="")
    autores = models.TextField(blank=True, default="")

    archivo_resumen = models.FileField(
        upload_to=upload_resumen,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["pdf"]), validate_resumen_size],
    )

    presentacion = models.FileField(
        upload_to=upload_presentacion,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["pdf", "ppt", "pptx"]), validate_presentacion_size],
    )

    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_REGISTRADA)

    class Meta:
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "ponente"]),
            models.Index(fields=["estado"]),
        ]

    def puede_editar(self) -> bool:
        return self.estado in {self.ESTADO_REGISTRADA, self.ESTADO_EN_REVISION}

    def __str__(self):
        return f"Ponencia({self.id}) {self.titulo}"