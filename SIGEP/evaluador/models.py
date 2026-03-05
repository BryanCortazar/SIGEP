from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from django.db.models.signals import post_save
from django.dispatch import receiver

EVENTO_MODEL = "administrador.Evento"


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# =========================
# VALIDADORES (TOP-LEVEL)
# =========================
def validate_avatar_size(file):
    max_mb = 5
    max_bytes = max_mb * 1024 * 1024
    size = int(getattr(file, "size", 0) or 0)
    if size > max_bytes:
        raise ValidationError(f"La foto excede el tamaño permitido ({max_mb}MB).")


def validate_cv_size(file):
    max_mb = 15
    max_bytes = max_mb * 1024 * 1024
    size = int(getattr(file, "size", 0) or 0)
    if size > max_bytes:
        raise ValidationError(f"El CV excede el tamaño permitido ({max_mb}MB).")


# =========================
# PERFIL (CONFIGURACIÓN)
# =========================
def _avatar_upload_path(instance, filename: str) -> str:
    return f"perfiles/usuario_{instance.usuario_id}/avatar/{filename}"


def _cv_upload_path(instance, filename: str) -> str:
    return f"perfiles/usuario_{instance.usuario_id}/cv/{filename}"


class PerfilUsuario(TimeStampedModel):
    # ✅ related_name único (evita choque con coordinador)
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil_evaluador",
    )

    puesto = models.CharField(max_length=120, blank=True, default="")
    institucion = models.CharField(max_length=180, blank=True, default="")
    telefono = models.CharField(max_length=40, blank=True, default="")
    bio = models.TextField(blank=True, default="")

    avatar = models.ImageField(
        upload_to=_avatar_upload_path,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["jpg", "jpeg", "png", "webp"]),
            validate_avatar_size,
        ],
    )

    cv = models.FileField(
        upload_to=_cv_upload_path,
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(["pdf"]),
            validate_cv_size,
        ],
    )

    class Meta:
        indexes = [models.Index(fields=["usuario"])]

    def __str__(self):
        return f"PerfilEvaluador({self.usuario_id})"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def crear_perfil_usuario(sender, instance, created, **kwargs):
    if created:
        PerfilUsuario.objects.create(usuario=instance)


# =========================
# CRONOGRAMA
# =========================
class ActividadCronograma(TimeStampedModel):
    # ✅ related_name único
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="cronograma_actividades_evaluador",
    )
    titulo = models.CharField(max_length=180)
    inicio = models.TimeField()
    fin = models.TimeField()
    responsable = models.CharField(max_length=180, blank=True, default="")

    class Meta:
        ordering = ["inicio", "fin", "id"]
        indexes = [models.Index(fields=["evento", "inicio", "fin"])]

    def clean(self):
        if self.fin <= self.inicio:
            raise ValidationError("La hora fin debe ser mayor que la hora inicio.")

    def __str__(self):
        return f"{self.titulo} ({self.inicio}-{self.fin})"


# =========================
# INSCRIPCIONES
# =========================
class Inscripcion(TimeStampedModel):
    ROL_ADMINISTRADOR = "ADMINISTRADOR"
    ROL_COORDINADOR = "COORDINADOR"
    ROL_USUARIO = "USUARIO"
    ROL_EVALUADOR = "EVALUADOR"
    ROL_PONENTE = "PONENTE"
    ROL_PARTICIPANTE = "PARTICIPANTE"

    ROLES = (
        (ROL_ADMINISTRADOR, "Administrador"),
        (ROL_COORDINADOR, "Coordinador"),
        (ROL_USUARIO, "Usuario"),
        (ROL_EVALUADOR, "Evaluador"),
        (ROL_PONENTE, "Ponente"),
        (ROL_PARTICIPANTE, "Participante"),
    )

    # ✅ related_name únicos
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones_evaluador",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones_evento_evaluador",
    )
    rol = models.CharField(max_length=20, choices=ROLES)

    class Meta:
        unique_together = ("evento", "usuario")
        indexes = [models.Index(fields=["evento", "rol"])]

    def __str__(self):
        return f"{self.usuario_id} -> {self.evento_id} ({self.rol})"


# =========================
# EVALUADORES
# =========================
class EvaluacionProyecto(TimeStampedModel):
    # ✅ related_name único
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="evaluacion_proyectos_evaluador",
    )
    titulo = models.CharField(max_length=220)
    ponente = models.CharField(max_length=180, blank=True, default="")
    inicio = models.TimeField()
    fin = models.TimeField()
    lugar = models.CharField(max_length=180, blank=True, default="")

    class Meta:
        ordering = ["inicio", "fin", "id"]
        indexes = [models.Index(fields=["evento", "inicio", "fin"])]

    def clean(self):
        if self.fin <= self.inicio:
            raise ValidationError("La hora fin debe ser mayor que la hora inicio.")

    def __str__(self):
        return self.titulo


class EvaluacionAsignacion(TimeStampedModel):
    proyecto = models.ForeignKey(EvaluacionProyecto, on_delete=models.CASCADE, related_name="asignaciones")

    # ✅ related_name único
    evaluador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="evaluaciones_asignadas_evaluador",
    )

    class Meta:
        unique_together = ("proyecto", "evaluador")
        indexes = [models.Index(fields=["evaluador"])]

    def clean(self):
        p = self.proyecto
        if not p or not p.pk:
            return

        conflicto = (
            EvaluacionAsignacion.objects.select_related("proyecto")
            .filter(evaluador=self.evaluador, proyecto__evento=p.evento)
            .exclude(pk=self.pk)
            .filter(proyecto__inicio__lt=p.fin, proyecto__fin__gt=p.inicio)
            .exists()
        )
        if conflicto:
            raise ValidationError("Este evaluador ya tiene una evaluación asignada en un horario traslapado.")

    def __str__(self):
        return f"{self.evaluador_id} -> {self.proyecto_id}"


class EvaluacionEntrega(TimeStampedModel):
    ESTADO_BORRADOR = "BORRADOR"
    ESTADO_ENVIADA = "ENVIADA"
    ESTADOS = (
        (ESTADO_BORRADOR, "Borrador"),
        (ESTADO_ENVIADA, "Enviada"),
    )

    asignacion = models.OneToOneField(
        "EvaluacionAsignacion",
        on_delete=models.CASCADE,
        related_name="entrega",
    )

    calificacion = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    observaciones_generales = models.TextField(blank=True, default="")
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_BORRADOR)
    fecha_envio = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha_envio", "-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["estado"]),
            models.Index(fields=["fecha_envio"]),
        ]

    def __str__(self):
        return f"Entrega({self.asignacion_id}) - {self.estado}"


# =========================
# RÚBRICAS
# =========================
def rubrica_upload_path(instance, filename: str) -> str:
    return f"rubricas/evento_{instance.rubrica.evento_id}/{filename}"


class Rubrica(TimeStampedModel):
    ESTADO_BORRADOR = "BORRADOR"
    ESTADO_ACTIVA = "ACTIVA"
    ESTADOS = ((ESTADO_BORRADOR, "Borrador"), (ESTADO_ACTIVA, "Activa"))

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="rubricas_evaluador",
    )
    proyecto = models.ForeignKey("EvaluacionProyecto", on_delete=models.SET_NULL, null=True, blank=True, related_name="rubricas")
    titulo = models.CharField(max_length=220)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_BORRADOR)

    class Meta:
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["evento", "proyecto"]),
        ]

    def __str__(self):
        return f"{self.titulo} ({self.evento_id})"


class RubricaCriterio(TimeStampedModel):
    rubrica = models.ForeignKey(Rubrica, on_delete=models.CASCADE, related_name="criterios")
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True, default="")
    puntaje_max = models.PositiveIntegerField(default=1)
    orden = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["orden", "id"]
        indexes = [models.Index(fields=["rubrica", "orden"])]

    def clean(self):
        if self.puntaje_max <= 0:
            raise ValidationError("El puntaje máximo debe ser mayor que 0.")


class RubricaAdjunto(TimeStampedModel):
    rubrica = models.ForeignKey(Rubrica, on_delete=models.CASCADE, related_name="adjuntos")
    archivo = models.FileField(upload_to=rubrica_upload_path)
    nombre_original = models.CharField(max_length=255, blank=True, default="")
    tamano = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-id"]

    def save(self, *args, **kwargs):
        try:
            self.tamano = int(getattr(self.archivo, "size", 0) or 0)
        except Exception:
            self.tamano = 0
        super().save(*args, **kwargs)


# =========================
# ESPACIOS
# =========================
class Espacio(TimeStampedModel):
    TIPO_AUDITORIO = "AUDITORIO"
    TIPO_SALA = "SALA"
    TIPO_LAB = "LABORATORIO"
    TIPO_OTRO = "OTRO"
    TIPOS = (
        (TIPO_AUDITORIO, "Auditorio"),
        (TIPO_SALA, "Sala"),
        (TIPO_LAB, "Laboratorio"),
        (TIPO_OTRO, "Otro"),
    )

    ESTADO_DISPONIBLE = "DISPONIBLE"
    ESTADO_OCUPADO = "OCUPADO"
    ESTADO_MANTENIMIENTO = "MANTENIMIENTO"
    ESTADOS = (
        (ESTADO_DISPONIBLE, "Disponible"),
        (ESTADO_OCUPADO, "Ocupado"),
        (ESTADO_MANTENIMIENTO, "Mantenimiento"),
    )

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="espacios_evaluador",
    )
    proyecto = models.ForeignKey("EvaluacionProyecto", on_delete=models.SET_NULL, null=True, blank=True, related_name="espacios")

    nombre = models.CharField(max_length=180)
    tipo = models.CharField(max_length=20, choices=TIPOS, default=TIPO_SALA)
    capacidad = models.PositiveIntegerField(default=0)
    ubicacion = models.CharField(max_length=220, blank=True, default="")
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_DISPONIBLE)
    tags = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "tipo"]),
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["evento", "nombre"]),
        ]


# =========================
# REPORTES
# =========================
def reporte_upload_path(instance, filename: str) -> str:
    return f"reportes/evento_{instance.evento_id}/{filename}"


class Reporte(TimeStampedModel):
    CATEG_TODOS = "TODOS"
    CATEG_INSCRIPCIONES = "INSCRIPCIONES"
    CATEG_EVALUACIONES = "EVALUACIONES"
    CATEG_ASISTENCIA = "ASISTENCIA"
    CATEG_GENERAL = "GENERAL"
    CATEGORIAS = (
        (CATEG_TODOS, "Todos"),
        (CATEG_INSCRIPCIONES, "Inscripciones"),
        (CATEG_EVALUACIONES, "Evaluaciones"),
        (CATEG_ASISTENCIA, "Asistencia"),
        (CATEG_GENERAL, "General"),
    )

    FORMATO_PDF = "PDF"
    FORMATO_XLSX = "XLSX"
    FORMATOS = ((FORMATO_PDF, "PDF"), (FORMATO_XLSX, "Excel (XLSX)"))

    MODO_TIEMPO_REAL = "TIEMPO_REAL"
    MODO_HISTORIAL = "HISTORIAL"
    MODOS = ((MODO_TIEMPO_REAL, "Tiempo real"), (MODO_HISTORIAL, "Historial"))

    ESTADO_LISTO = "LISTO"
    ESTADO_ARCHIVADO = "ARCHIVADO"
    ESTADOS = ((ESTADO_LISTO, "Listo"), (ESTADO_ARCHIVADO, "Archivado"))

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="reportes_evaluador",
    )
    proyecto = models.ForeignKey("EvaluacionProyecto", on_delete=models.SET_NULL, null=True, blank=True, related_name="reportes")

    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reportes_creados_evaluador",
    )

    nombre = models.CharField(max_length=220)
    nombre_original = models.CharField(max_length=255, blank=True, default="")
    categoria = models.CharField(max_length=20, choices=CATEGORIAS, default=CATEG_GENERAL)
    formato = models.CharField(max_length=10, choices=FORMATOS, default=FORMATO_PDF)
    modo = models.CharField(max_length=20, choices=MODOS, default=MODO_TIEMPO_REAL)
    estado = models.CharField(max_length=20, choices=ESTADOS, default=ESTADO_LISTO)

    generado_en = models.DateTimeField(default=timezone.now)
    archivo = models.FileField(upload_to=reporte_upload_path, null=True, blank=True)

    class Meta:
        ordering = ["-generado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "categoria"]),
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["evento", "generado_en"]),
        ]