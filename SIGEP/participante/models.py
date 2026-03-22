from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

EVENTO_MODEL = "administrador.Evento"
EVALUACION_PROYECTO_MODEL = "evaluador.EvaluacionProyecto"


def _validate_file_size(file, max_mb: int, label: str):
    size = int(getattr(file, "size", 0) or 0)
    max_bytes = max_mb * 1024 * 1024
    if size > max_bytes:
        raise ValidationError(f"{label} excede el tamaño permitido ({max_mb} MB).")


def validate_presentacion_size(file):
    _validate_file_size(file, 25, "La presentación del proyecto")


def validate_informe_size(file):
    _validate_file_size(file, 15, "El informe del proyecto")


def validate_cv_size(file):
    _validate_file_size(file, 10, "El CV del participante")


def upload_presentacion(instance, filename: str) -> str:
    return f"participante/presentaciones/evento_{instance.evento_id}/user_{instance.participante_id}/{filename}"


def upload_informe(instance, filename: str) -> str:
    return f"participante/informes/evento_{instance.evento_id}/user_{instance.participante_id}/{filename}"


def upload_avatar(instance, filename: str) -> str:
    return f"participante/perfil/user_{instance.usuario_id}/{filename}"


def upload_cv(instance, filename: str) -> str:
    return f"participante/cv/user_{instance.usuario_id}/{filename}"


def generar_folio_constancia() -> str:
    return f"SIGEP-CP-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PerfilParticipante(TimeStampedModel):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_participante",
    )
    telefono = models.CharField(max_length=20, blank=True, default="")
    institucion = models.CharField(max_length=180, blank=True, default="")
    biografia = models.TextField(blank=True, default="")
    avatar = models.ImageField(upload_to=upload_avatar, blank=True, null=True)
    cv = models.FileField(
        upload_to=upload_cv,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf", "doc", "docx"]), validate_cv_size],
    )

    class Meta:
        verbose_name = "Perfil de participante"
        verbose_name_plural = "Perfiles de participantes"

    def __str__(self) -> str:
        return f"Perfil participante: {self.usuario}"


class ProyectoParticipante(TimeStampedModel):
    CAT_TECNOLOGIA = "TECNOLOGIA"
    CAT_INNOVACION = "INNOVACION"
    CAT_INVESTIGACION = "INVESTIGACION"
    CAT_SOCIAL = "SOCIAL"
    CAT_EMPRESARIAL = "EMPRESARIAL"

    CATEGORIAS = (
        (CAT_TECNOLOGIA, "Tecnología"),
        (CAT_INNOVACION, "Innovación"),
        (CAT_INVESTIGACION, "Investigación"),
        (CAT_SOCIAL, "Impacto social"),
        (CAT_EMPRESARIAL, "Emprendimiento"),
    )

    ESTADO_REGISTRADO = "REGISTRADO"
    ESTADO_EN_REVISION = "EN_REVISION"
    ESTADO_ACEPTADO = "ACEPTADO"
    ESTADO_RECHAZADO = "RECHAZADO"

    ESTADOS = (
        (ESTADO_REGISTRADO, "Registrado"),
        (ESTADO_EN_REVISION, "En revisión"),
        (ESTADO_ACEPTADO, "Aceptado"),
        (ESTADO_RECHAZADO, "Rechazado"),
    )

    PROG_PENDIENTE = "PENDIENTE"
    PROG_PROGRAMADO = "PROGRAMADO"
    PROG_FINALIZADO = "FINALIZADO"
    PROG_CANCELADO = "CANCELADO"

    ESTADOS_PROGRAMACION = (
        (PROG_PENDIENTE, "Pendiente"),
        (PROG_PROGRAMADO, "Programado"),
        (PROG_FINALIZADO, "Finalizado"),
        (PROG_CANCELADO, "Cancelado"),
    )

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="proyectos_participantes",
    )
    participante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proyectos_participante",
    )
    evaluacion_proyecto = models.OneToOneField(
        EVALUACION_PROYECTO_MODEL,
        on_delete=models.SET_NULL,
        related_name="proyecto_real",
        blank=True,
        null=True,
    )

    nombre_participante = models.CharField(max_length=180)
    correo = models.EmailField()
    telefono = models.CharField(max_length=20)
    institucion_empresa = models.CharField(max_length=200)
    nombre_proyecto = models.CharField(max_length=220)
    categoria = models.CharField(max_length=20, choices=CATEGORIAS)
    numero_integrantes = models.PositiveIntegerField(default=1)
    resumen = models.TextField(blank=True, default="")
    presentacion = models.FileField(
        upload_to=upload_presentacion,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf", "ppt", "pptx"]), validate_presentacion_size],
    )
    informe = models.FileField(
        upload_to=upload_informe,
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf", "doc", "docx"]), validate_informe_size],
    )
    requerimientos_tecnicos = models.TextField(blank=True, default="")

    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_REGISTRADO)
    fecha_programada = models.DateField(blank=True, null=True)
    hora_inicio = models.TimeField(blank=True, null=True)
    hora_fin = models.TimeField(blank=True, null=True)
    espacio_asignado = models.CharField(max_length=180, blank=True, default="")
    estado_programacion = models.CharField(max_length=20, choices=ESTADOS_PROGRAMACION, default=PROG_PENDIENTE)

    class Meta:
        verbose_name = "Proyecto participante"
        verbose_name_plural = "Proyectos participantes"
        ordering = ["-creado_en", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["evento", "participante"],
                name="unique_proyecto_por_evento_y_participante",
            )
        ]
        indexes = [
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["participante", "evento"]),
        ]

    def clean(self):
        super().clean()
        if self.numero_integrantes < 1:
            raise ValidationError({"numero_integrantes": "Debe haber al menos un integrante."})

        if self.hora_inicio and self.hora_fin and self.hora_fin <= self.hora_inicio:
            raise ValidationError({"hora_fin": "La hora fin debe ser mayor que la hora inicio."})

    def __str__(self) -> str:
        return f"{self.nombre_proyecto} - {self.evento_id}"

    def nombre_evento(self) -> str:
        return getattr(self.evento, "titulo", None) or getattr(self.evento, "nombre", "Evento")

    def tiene_programacion(self) -> bool:
        return bool(self.fecha_programada and self.hora_inicio and self.hora_fin)

    def porcentaje_documentacion(self) -> int:
        total = 3
        completos = sum([
            bool(self.resumen.strip()),
            bool(self.presentacion),
            bool(self.informe),
        ])
        return int((completos / total) * 100)

    def puede_editar(self) -> bool:
        return self.estado in {self.ESTADO_REGISTRADO, self.ESTADO_EN_REVISION}


class PaseAccesoParticipante(TimeStampedModel):
    proyecto = models.OneToOneField(
        ProyectoParticipante,
        on_delete=models.CASCADE,
        related_name="pase_acceso",
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    activo = models.BooleanField(default=True)
    total_escaneos = models.PositiveIntegerField(default=0)
    ultimo_escaneo = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Pase de acceso de participante"
        verbose_name_plural = "Pases de acceso de participantes"
        ordering = ["-actualizado_en", "-id"]

    def __str__(self) -> str:
        return f"Pase QR · {self.proyecto.nombre_proyecto}"

    def url_validacion(self) -> str:
        return reverse("participante:validar_pase_qr", kwargs={"token": str(self.token)})

    def registrar_escaneo(self) -> None:
        self.total_escaneos += 1
        self.ultimo_escaneo = timezone.now()
        self.save(update_fields=["total_escaneos", "ultimo_escaneo", "actualizado_en"])

    def regenerar_token(self) -> None:
        self.token = uuid.uuid4()
        self.save(update_fields=["token", "actualizado_en"])


class ConstanciaParticipante(TimeStampedModel):
    proyecto = models.OneToOneField(
        ProyectoParticipante,
        on_delete=models.CASCADE,
        related_name="constancia_generada",
    )
    folio = models.CharField(max_length=40, unique=True, editable=False, db_index=True, default=generar_folio_constancia)
    emitida_en = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Constancia de participante"
        verbose_name_plural = "Constancias de participantes"
        ordering = ["-emitida_en", "-id"]

    def __str__(self) -> str:
        return f"Constancia {self.folio} · {self.proyecto.nombre_proyecto}"