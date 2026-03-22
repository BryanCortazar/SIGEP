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
        raise ValidationError(f"{label} excede el tamaño permitido ({max_mb} MB).")


def validate_pdf_size(file):
    validate_file_size(file, 10, "El archivo PDF")


def validate_resumen_size(file):
    validate_file_size(file, 10, "El archivo de resumen")


def validate_presentacion_size(file):
    validate_file_size(file, 25, "La presentación")


def upload_presentacion(instance, filename: str) -> str:
    return f"ponente/presentaciones/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


def upload_resumen(instance, filename: str) -> str:
    return f"ponente/resumenes/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


def upload_cv(instance, filename: str) -> str:
    return f"ponente/cv/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


def upload_resena(instance, filename: str) -> str:
    return f"ponente/resenas/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


def upload_diapositivas(instance, filename: str) -> str:
    return f"ponente/diapositivas/evento_{instance.evento_id}/user_{instance.ponente_id}/{filename}"


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

    DOC_PENDIENTE = "PENDIENTE"
    DOC_EN_REVISION = "EN_REVISION"
    DOC_VALIDADO = "VALIDADO"

    ESTADOS_DOCUMENTO = (
        (DOC_PENDIENTE, "Pendiente"),
        (DOC_EN_REVISION, "En revisión"),
        (DOC_VALIDADO, "Validado"),
    )

    PROG_PENDIENTE = "PENDIENTE"
    PROG_CONFIRMADO = "CONFIRMADO"
    PROG_REPROGRAMADO = "REPROGRAMADO"
    PROG_CANCELADO = "CANCELADO"

    ESTADOS_PROGRAMACION = (
        (PROG_PENDIENTE, "Pendiente"),
        (PROG_CONFIRMADO, "Confirmado"),
        (PROG_REPROGRAMADO, "Reprogramado"),
        (PROG_CANCELADO, "Cancelado"),
    )

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="ponencias",
    )

    ponente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ponencias",
    )

    evaluacion_proyecto = models.OneToOneField(
        "evaluador.EvaluacionProyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ponencia_origen",
    )

    titulo = models.CharField(max_length=240)
    tipo = models.CharField(max_length=120, blank=True, default="")
    area_tematica = models.CharField(max_length=160, blank=True, default="")
    resumen = models.TextField(blank=True, default="")
    autores = models.TextField(blank=True, default="")

    archivo_resumen = models.FileField(
        upload_to=upload_resumen,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf"]),
            validate_resumen_size,
        ],
    )

    presentacion = models.FileField(
        upload_to=upload_presentacion,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf", "ppt", "pptx"]),
            validate_presentacion_size,
        ],
    )

    cv_documento = models.FileField(
        upload_to=upload_cv,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf"]),
            validate_pdf_size,
        ],
    )
    cv_estado = models.CharField(
        max_length=20,
        choices=ESTADOS_DOCUMENTO,
        default=DOC_PENDIENTE,
    )

    resena_biografica = models.FileField(
        upload_to=upload_resena,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf"]),
            validate_pdf_size,
        ],
    )
    resena_estado = models.CharField(
        max_length=20,
        choices=ESTADOS_DOCUMENTO,
        default=DOC_PENDIENTE,
    )

    diapositivas_presentacion = models.FileField(
        upload_to=upload_diapositivas,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf", "ppt", "pptx"]),
            validate_presentacion_size,
        ],
    )
    diapositivas_estado = models.CharField(
        max_length=20,
        choices=ESTADOS_DOCUMENTO,
        default=DOC_PENDIENTE,
    )

    requerimientos_tecnicos = models.TextField(blank=True, default="")

    fecha_programada = models.DateField(null=True, blank=True)
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    espacio_asignado = models.CharField(max_length=180, blank=True, default="")
    estado_programacion = models.CharField(
        max_length=20,
        choices=ESTADOS_PROGRAMACION,
        default=PROG_PENDIENTE,
    )

    # Constancias
    constancia_habilitada = models.BooleanField(default=False)
    folio_constancia = models.CharField(max_length=50, blank=True, default="")
    constancia_generada_en = models.DateTimeField(null=True, blank=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default=ESTADO_REGISTRADA,
    )

    class Meta:
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "ponente"]),
            models.Index(fields=["estado"]),
            models.Index(fields=["fecha_programada"]),
            models.Index(fields=["estado_programacion"]),
            models.Index(fields=["constancia_habilitada"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["evento", "ponente"],
                name="uq_ponencia_evento_ponente",
            )
        ]

    def puede_editar(self) -> bool:
        evento_publicado = getattr(self.evento, "estado", "") == "PUBLICADO"
        return evento_publicado and self.estado in {self.ESTADO_REGISTRADA, self.ESTADO_EN_REVISION}

    def porcentaje_documentacion(self) -> int:
        total = 4
        completos = 0

        if self.cv_documento:
            completos += 1
        if self.resena_biografica:
            completos += 1
        if self.diapositivas_presentacion:
            completos += 1
        if (self.requerimientos_tecnicos or "").strip():
            completos += 1

        return int((completos / total) * 100)

    def tiene_horario_asignado(self) -> bool:
        return bool(self.fecha_programada and self.hora_inicio and self.hora_fin and self.espacio_asignado)

    def rango_horario(self) -> str:
        if self.hora_inicio and self.hora_fin:
            return f"{self.hora_inicio.strftime('%H:%M')} - {self.hora_fin.strftime('%H:%M')}"
        return "Sin horario"

    def nombre_evento(self) -> str:
        if hasattr(self.evento, "nombre") and self.evento.nombre:
            return self.evento.nombre
        if hasattr(self.evento, "titulo") and self.evento.titulo:
            return self.evento.titulo
        return "Evento"

    def participacion_finalizada(self) -> bool:
        hoy = timezone.localdate()
        ahora = timezone.localtime()

        if not self.fecha_programada:
            return False

        if self.estado_programacion == self.PROG_CANCELADO:
            return False

        if self.fecha_programada < hoy:
            return True

        if self.fecha_programada == hoy and self.hora_fin:
            return ahora.time() >= self.hora_fin

        return False

    def puede_generar_constancia(self) -> bool:
        base_valida = (
            self.ponente_id is not None
            and self.evento_id is not None
            and self.fecha_programada is not None
            and self.estado_programacion != self.PROG_CANCELADO
            and self.estado != self.ESTADO_RECHAZADA
        )
        return base_valida and (self.constancia_habilitada or self.participacion_finalizada())

    def __str__(self):
        return f"Ponencia #{self.pk} - {self.titulo}"