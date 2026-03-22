from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

# ============================================================
# IMPORTANTE:
# Tu Evento vive en "administrador.Evento" (según tu sistema).
# Si cambia, SOLO ajustas esta constante.
# ============================================================
EVENTO_MODEL = "administrador.Evento"


class TimeStampedModel(models.Model):
    creado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ============================================================
# VALIDADORES (TOP-LEVEL) - IMPORTANTES PARA MIGRACIONES
# ============================================================
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


# ============================================================
# PERFIL (Configuración)
# ============================================================
def _avatar_upload_path(instance, filename: str) -> str:
    return f"perfiles/usuario_{instance.usuario_id}/avatar/{filename}"


def _cv_upload_path(instance, filename: str) -> str:
    return f"perfiles/usuario_{instance.usuario_id}/cv/{filename}"


class PerfilUsuario(TimeStampedModel):
    """
    Perfil extendido para el usuario.
    No tocamos el modelo User: lo extendemos con OneToOne.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="perfil",
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
        return f"Perfil({self.usuario_id})"


# ============================================================
# CRONOGRAMA
# ============================================================
class ActividadCronograma(TimeStampedModel):
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="cronograma_actividades",
    )
    titulo = models.CharField(max_length=180)
    inicio = models.TimeField()
    fin = models.TimeField()
    responsable = models.CharField(max_length=180, blank=True, default="")

    class Meta:
        ordering = ["inicio", "fin", "id"]
        indexes = [models.Index(fields=["evento", "inicio", "fin"])]

    def clean(self):
        super().clean()

        if self.inicio is None or self.fin is None:
            return

        if self.fin <= self.inicio:
            raise ValidationError({
                "fin": "La hora fin debe ser mayor que la hora inicio."
            })

    def __str__(self):
        return f"{self.titulo} ({self.inicio}-{self.fin})"


# ============================================================
# INSCRIPCIONES
# ============================================================
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

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inscripciones_evento",
    )
    rol = models.CharField(max_length=20, choices=ROLES)

    class Meta:
        unique_together = ("evento", "usuario")
        indexes = [models.Index(fields=["evento", "rol"])]

    def __str__(self):
        return f"{self.usuario_id} -> {self.evento_id} ({self.rol})"


# ============================================================
# EVALUADORES / PROYECTOS EVALUABLES
# ============================================================
class EvaluacionProyecto(TimeStampedModel):
    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="evaluacion_proyectos",
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
        super().clean()

        if self.inicio is None or self.fin is None:
            return

        if self.fin <= self.inicio:
            raise ValidationError({
                "fin": "La hora fin debe ser mayor que la hora inicio."
            })

    def __str__(self):
        return self.titulo


class EvaluacionAsignacion(TimeStampedModel):
    proyecto = models.ForeignKey(
        EvaluacionProyecto,
        on_delete=models.CASCADE,
        related_name="asignaciones",
    )
    evaluador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="evaluaciones_asignadas",
    )

    class Meta:
        unique_together = ("proyecto", "evaluador")
        indexes = [models.Index(fields=["evaluador"])]

    def clean(self):
        super().clean()

        if not self.proyecto_id or not self.evaluador_id:
            return

        p = self.proyecto
        if not p or p.inicio is None or p.fin is None or p.evento_id is None:
            return

        conflicto = (
            EvaluacionAsignacion.objects.select_related("proyecto")
            .filter(evaluador=self.evaluador, proyecto__evento=p.evento)
            .exclude(pk=self.pk)
            .filter(proyecto__inicio__lt=p.fin, proyecto__fin__gt=p.inicio)
            .exists()
        )
        if conflicto:
            raise ValidationError(
                "Este evaluador ya tiene una evaluación asignada en un horario traslapado."
            )

    def __str__(self):
        return f"{self.evaluador_id} -> {self.proyecto_id}"


# ============================================================
# RÚBRICAS
# ============================================================
def rubrica_upload_path(instance, filename: str) -> str:
    return f"rubricas/evento_{instance.rubrica.evento_id}/{filename}"


class Rubrica(TimeStampedModel):
    ESTADO_BORRADOR = "BORRADOR"
    ESTADO_ACTIVA = "ACTIVA"
    ESTADOS = ((ESTADO_BORRADOR, "Borrador"), (ESTADO_ACTIVA, "Activa"))

    evento = models.ForeignKey(
        EVENTO_MODEL,
        on_delete=models.CASCADE,
        related_name="rubricas",
    )
    proyecto = models.ForeignKey(
        "EvaluacionProyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rubricas",
    )
    ponencia = models.ForeignKey(
        "ponente.Ponencia",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rubricas",
    )
    titulo = models.CharField(max_length=220)
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default=ESTADO_BORRADOR,
    )

    class Meta:
        ordering = ["-actualizado_en", "-id"]
        indexes = [
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["evento", "proyecto"]),
            models.Index(fields=["evento", "ponencia"]),
        ]

    def clean(self):
        super().clean()

        if not self.proyecto_id and not self.ponencia_id:
            raise ValidationError("La rúbrica debe asociarse a un proyecto o a una ponencia.")

        if self.proyecto_id and self.proyecto and self.proyecto.evento_id != self.evento_id:
            raise ValidationError("El proyecto seleccionado no pertenece al evento actual.")

        if self.ponencia_id and self.ponencia and self.ponencia.evento_id != self.evento_id:
            raise ValidationError("La ponencia seleccionada no pertenece al evento actual.")

        # Se permite conservar el puente evaluable en `proyecto` aunque el objetivo visible sea una ponencia.
        # Esto mejora la comunicación con el módulo evaluador, que consume EvaluacionProyecto.
        dup = Rubrica.objects.filter(evento=self.evento).exclude(pk=self.pk)
        if self.proyecto_id and dup.filter(proyecto_id=self.proyecto_id).exists():
            raise ValidationError("Ya existe una rúbrica para el proyecto/registro evaluable seleccionado.")
        if self.ponencia_id and dup.filter(ponencia_id=self.ponencia_id).exists():
            raise ValidationError("Ya existe una rúbrica para la ponencia seleccionada.")

    @property
    def puntaje_maximo(self) -> int:
        return sum(int(c.puntaje_max or 0) for c in self.criterios.all())

    @property
    def tipo_objetivo(self) -> str:
        return "PONENCIA" if self.ponencia_id else "PROYECTO"

    @property
    def objetivo_titulo(self) -> str:
        if self.ponencia_id and self.ponencia:
            return self.ponencia.titulo
        if self.proyecto_id and self.proyecto:
            return self.proyecto.titulo
        return "Sin asignar"

    def __str__(self):
        return f"{self.titulo} ({self.evento_id})"


class RubricaCriterio(TimeStampedModel):
    rubrica = models.ForeignKey(
        Rubrica,
        on_delete=models.CASCADE,
        related_name="criterios",
    )
    titulo = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True, default="")
    puntaje_max = models.PositiveIntegerField(default=1)
    orden = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["orden", "id"]
        indexes = [models.Index(fields=["rubrica", "orden"])]

    def clean(self):
        super().clean()

        if self.puntaje_max is None:
            return

        if self.puntaje_max <= 0:
            raise ValidationError({
                "puntaje_max": "El puntaje máximo debe ser mayor que 0."
            })


class RubricaAdjunto(TimeStampedModel):
    rubrica = models.ForeignKey(
        Rubrica,
        on_delete=models.CASCADE,
        related_name="adjuntos",
    )
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


# ============================================================
# ESPACIOS
# ============================================================
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
        related_name="espacios",
    )
    proyecto = models.ForeignKey(
        "EvaluacionProyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="espacios",
    )
    ponencia = models.ForeignKey(
        "ponente.Ponencia",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="espacios_asignados",
    )

    nombre = models.CharField(max_length=180)
    tipo = models.CharField(max_length=20, choices=TIPOS, default=TIPO_SALA)
    capacidad = models.PositiveIntegerField(default=0)
    ubicacion = models.CharField(max_length=220, blank=True, default="")
    inicio = models.TimeField(null=True, blank=True)
    fin = models.TimeField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=ESTADOS,
        default=ESTADO_DISPONIBLE,
    )
    tags = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["inicio", "fin", "nombre", "id"]
        indexes = [
            models.Index(fields=["evento", "tipo"]),
            models.Index(fields=["evento", "estado"]),
            models.Index(fields=["evento", "nombre"]),
            models.Index(fields=["evento", "inicio", "fin"]),
            models.Index(fields=["evento", "proyecto"]),
            models.Index(fields=["evento", "ponencia"]),
        ]

    def clean(self):
        super().clean()

        tiene_proyecto = self.proyecto_id is not None
        tiene_ponencia = self.ponencia_id is not None
        if tiene_proyecto == tiene_ponencia:
            raise ValidationError("La asignación de espacio debe vincularse a un proyecto o a una ponencia, pero no a ambos.")

        if self.proyecto_id and self.proyecto and self.proyecto.evento_id != self.evento_id:
            raise ValidationError("El proyecto seleccionado no pertenece al evento actual.")

        if self.ponencia_id and self.ponencia and self.ponencia.evento_id != self.evento_id:
            raise ValidationError("La ponencia seleccionada no pertenece al evento actual.")

        if self.inicio is None or self.fin is None:
            raise ValidationError("Debes indicar la hora de inicio y la hora fin del uso del área.")

        if self.fin <= self.inicio:
            raise ValidationError({"fin": "La hora fin debe ser mayor que la hora inicio."})

        nombre_normalizado = (self.nombre or "").strip()
        if nombre_normalizado:
            conflicto_area = (
                Espacio.objects.filter(evento=self.evento, nombre__iexact=nombre_normalizado)
                .exclude(pk=self.pk)
                .filter(inicio__lt=self.fin, fin__gt=self.inicio)
                .exists()
            )
            if conflicto_area:
                raise ValidationError(
                    "El área seleccionada ya está asignada a otro proyecto o ponencia en un horario traslapado."
                )

        dup = Espacio.objects.filter(evento=self.evento).exclude(pk=self.pk)
        if self.proyecto_id and dup.filter(proyecto_id=self.proyecto_id).exists():
            raise ValidationError("El proyecto seleccionado ya tiene un espacio asignado. Edita el registro existente si necesitas cambiarlo.")
        if self.ponencia_id and dup.filter(ponencia_id=self.ponencia_id).exists():
            raise ValidationError("La ponencia seleccionada ya tiene un espacio asignado. Edita el registro existente si necesitas cambiarlo.")

    @property
    def tipo_objetivo(self) -> str:
        return "PONENCIA" if self.ponencia_id else "PROYECTO"

    @property
    def objetivo_titulo(self) -> str:
        if self.ponencia_id and self.ponencia:
            return str(getattr(self.ponencia, "titulo", "Ponencia") or "Ponencia")
        if self.proyecto_id and self.proyecto:
            return str(getattr(self.proyecto, "titulo", "Proyecto") or "Proyecto")
        return "Sin objetivo"

    @property
    def objetivo_responsable(self) -> str:
        if self.ponencia_id and self.ponencia:
            user = getattr(self.ponencia, "ponente", None)
            if user is not None:
                try:
                    full_name = user.get_full_name()
                except Exception:
                    full_name = ""
                return (full_name or getattr(user, "email", "") or getattr(user, "username", "") or "Sin responsable").strip()
            return str(getattr(self.ponencia, "autor", "") or getattr(self.ponencia, "responsable", "") or "Sin responsable")
        if self.proyecto_id and self.proyecto:
            return str(getattr(self.proyecto, "ponente", "") or "Sin responsable")
        return "Sin responsable"

    def __str__(self):
        return f"{self.nombre} · {self.objetivo_titulo}"


# ============================================================
# REPORTES
# ============================================================
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
        related_name="reportes",
    )
    proyecto = models.ForeignKey(
        "EvaluacionProyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reportes",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reportes_creados",
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

    def __str__(self):
        return self.nombre