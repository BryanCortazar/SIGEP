from __future__ import annotations

from django import forms
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import (
    ActividadCronograma,
    Inscripcion,
    EvaluacionProyecto,
    Rubrica,
    Espacio,
    Reporte,
    PerfilUsuario,
)

User = get_user_model()


def _get_evento_model():
    """
    Resuelve el modelo Evento sin depender de una única app.
    """
    for app_label, model_name in [
        ("administrador", "Evento"),
        ("coordinador", "Evento"),
        ("eventos", "Evento"),
    ]:
        try:
            return apps.get_model(app_label, model_name)
        except Exception:
            continue
    return None


def _get_ponencia_model():
    try:
        return apps.get_model("ponente", "Ponencia")
    except Exception:
        return None



# =========================
# Forms existentes (los mantengo)
# =========================
class InscripcionUsuarioForm(forms.Form):
    user_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    nombres = forms.CharField(max_length=150, required=True)
    apellidos = forms.CharField(max_length=150, required=True)
    correo = forms.EmailField(required=True)
    rol = forms.ChoiceField(choices=Inscripcion.ROLES, required=True)
    activo = forms.BooleanField(required=False, initial=True)
    password = forms.CharField(required=False, min_length=8, widget=forms.PasswordInput())

    def clean_correo(self):
        email = (self.cleaned_data.get("correo") or "").strip().lower()
        if not email:
            raise forms.ValidationError("El correo es obligatorio.")
        return email

    def clean(self):
        cleaned = super().clean()
        user_id = cleaned.get("user_id")
        password = (cleaned.get("password") or "").strip()
        email = (cleaned.get("correo") or "").strip().lower()

        if not user_id and not password:
            user_exists = False
            if email:
                user_exists = User.objects.filter(email__iexact=email).exists()
            if not user_exists:
                self.add_error("password", "La contraseña es obligatoria para registrar un usuario nuevo.")

        if password:
            try:
                validate_password(password)
            except ValidationError as e:
                self.add_error("password", e.messages)

        return cleaned




class EventoGestionForm(forms.Form):
    evento_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    titulo = forms.CharField(max_length=200, required=True)
    descripcion = forms.CharField(required=False, widget=forms.Textarea)
    fecha = forms.DateField(required=True, widget=forms.DateInput(attrs={"type": "date"}))
    lugar = forms.CharField(max_length=150, required=False)
    cupo = forms.IntegerField(required=False, min_value=0, initial=0)
    estado = forms.ChoiceField(choices=[("BORRADOR", "Borrador"), ("PUBLICADO", "Publicado"), ("CERRADO", "Cerrado")], required=False)

    def clean_titulo(self):
        return (self.cleaned_data.get("titulo") or "").strip()

    def clean_descripcion(self):
        return (self.cleaned_data.get("descripcion") or "").strip()

    def clean_lugar(self):
        return (self.cleaned_data.get("lugar") or "").strip()

    def clean_estado(self):
        value = (self.cleaned_data.get("estado") or "BORRADOR").strip().upper()
        if value not in {"BORRADOR", "PUBLICADO", "CERRADO"}:
            raise forms.ValidationError("Estado no válido.")
        return value


# Compatibilidad hacia atrás
EventForm = EventoGestionForm


class ActividadCronogramaForm(forms.ModelForm):
    inicio = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        required=True,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    fin = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        required=True,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )

    class Meta:
        model = ActividadCronograma
        fields = ["titulo", "inicio", "fin", "responsable"]
        widgets = {
            "titulo": forms.TextInput(),
            "responsable": forms.TextInput(),
        }

    def clean_titulo(self):
        titulo = (self.cleaned_data.get("titulo") or "").strip()
        if not titulo:
            raise forms.ValidationError("El título de la actividad es obligatorio.")
        return titulo

    def clean_responsable(self):
        return (self.cleaned_data.get("responsable") or "").strip()

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get("inicio")
        fin = cleaned.get("fin")

        if inicio is None:
            self.add_error("inicio", "La hora de inicio es obligatoria.")
        if fin is None:
            self.add_error("fin", "La hora de fin es obligatoria.")
        if inicio is not None and fin is not None and fin <= inicio:
            self.add_error("fin", "La hora fin debe ser mayor que la hora inicio.")

        return cleaned


class EvaluacionProyectoForm(forms.ModelForm):
    inicio = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        required=True,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    fin = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        required=True,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )

    class Meta:
        model = EvaluacionProyecto
        fields = ["titulo", "ponente", "inicio", "fin", "lugar"]

    def clean_titulo(self):
        titulo = (self.cleaned_data.get("titulo") or "").strip()
        if not titulo:
            raise forms.ValidationError("El título del proyecto/ponencia es obligatorio.")
        return titulo

    def clean_ponente(self):
        return (self.cleaned_data.get("ponente") or "").strip()

    def clean_lugar(self):
        return (self.cleaned_data.get("lugar") or "").strip()

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get("inicio")
        fin = cleaned.get("fin")

        if inicio is None:
            self.add_error("inicio", "La hora de inicio es obligatoria.")
        if fin is None:
            self.add_error("fin", "La hora de fin es obligatoria.")
        if inicio is not None and fin is not None and fin <= inicio:
            self.add_error("fin", "La hora fin debe ser mayor que la hora inicio.")

        return cleaned


class RubricaForm(forms.ModelForm):
    TARGET_PROYECTO = "PROYECTO"
    TARGET_PONENCIA = "PONENCIA"
    TARGET_CHOICES = (
        (TARGET_PROYECTO, "Proyecto"),
        (TARGET_PONENCIA, "Ponencia"),
    )

    target_type = forms.ChoiceField(
        choices=TARGET_CHOICES,
        required=True,
        initial=TARGET_PROYECTO,
        label="Tipo de registro",
    )

    class Meta:
        model = Rubrica
        fields = ["titulo", "estado", "proyecto", "ponencia"]

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        ponencias_qs = kwargs.pop("ponencias_qs", None)
        super().__init__(*args, **kwargs)

        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs
        self.fields["proyecto"].required = False
        self.fields["proyecto"].empty_label = "Selecciona un proyecto"
        self.fields["proyecto"].label = "Proyecto"

        if ponencias_qs is not None:
            self.fields["ponencia"].queryset = ponencias_qs
        else:
            Ponencia = _get_ponencia_model()
            self.fields["ponencia"].queryset = Ponencia.objects.none() if Ponencia else []
        self.fields["ponencia"].required = False
        self.fields["ponencia"].empty_label = "Selecciona una ponencia"
        self.fields["ponencia"].label = "Ponencia"

        target_initial = self.TARGET_PONENCIA if getattr(self.instance, "ponencia_id", None) else self.TARGET_PROYECTO
        self.fields["target_type"].initial = target_initial

        common_class = "w-full h-11 rounded-lg border-slate-300 dark:bg-slate-800 dark:border-slate-700"
        text_class = "w-full h-11 rounded-lg border-slate-300 dark:bg-slate-800 dark:border-slate-700 px-3"
        self.fields["titulo"].widget.attrs.update({"class": text_class, "placeholder": "Ej. Rúbrica de evaluación técnica"})
        self.fields["estado"].widget.attrs.update({"class": common_class})
        self.fields["target_type"].widget.attrs.update({"class": common_class})
        self.fields["proyecto"].widget.attrs.update({"class": common_class})
        self.fields["ponencia"].widget.attrs.update({"class": common_class})

    def clean_titulo(self):
        titulo = (self.cleaned_data.get("titulo") or "").strip()
        if not titulo:
            raise forms.ValidationError("El nombre de la rúbrica es obligatorio.")
        return titulo

    def clean(self):
        cleaned = super().clean()
        target_type = (cleaned.get("target_type") or self.TARGET_PROYECTO).strip().upper()
        proyecto = cleaned.get("proyecto")
        ponencia = cleaned.get("ponencia")

        if target_type == self.TARGET_PROYECTO:
            cleaned["ponencia"] = None
            if not proyecto:
                self.add_error("proyecto", "Debes seleccionar el proyecto al que se asignará la rúbrica.")
        elif target_type == self.TARGET_PONENCIA:
            cleaned["proyecto"] = None
            if not ponencia:
                self.add_error("ponencia", "Debes seleccionar la ponencia a la que se asignará la rúbrica.")
        else:
            self.add_error("target_type", "Selecciona un tipo de registro válido.")

        return cleaned


class EspacioForm(forms.ModelForm):
    target_type = forms.ChoiceField(
        choices=(("PONENCIA", "Ponencia"), ("PROYECTO", "Proyecto")),
        required=True,
        initial="PONENCIA",
    )
    ponencia = forms.ModelChoiceField(queryset=None, required=False)
    inicio = forms.TimeField(
        input_formats=["%H:%M", "%H:%M:%S"],
        required=True,
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    duracion_minutos = forms.IntegerField(min_value=5, max_value=480, required=True, initial=30)

    class Meta:
        model = Espacio
        fields = [
            "nombre",
            "tipo",
            "capacidad",
            "ubicacion",
            "estado",
            "proyecto",
            "ponencia",
            "inicio",
            "tags",
        ]

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        ponencias_qs = kwargs.pop("ponencias_qs", None)
        super().__init__(*args, **kwargs)

        self.fields["proyecto"].required = False
        self.fields["ponencia"].required = False

        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs
        if ponencias_qs is not None:
            self.fields["ponencia"].queryset = ponencias_qs
        else:
            Ponencia = _get_ponencia_model()
            self.fields["ponencia"].queryset = Ponencia.objects.none() if Ponencia else []

        if self.instance and getattr(self.instance, "pk", None):
            self.fields["target_type"].initial = "PONENCIA" if getattr(self.instance, "ponencia_id", None) else "PROYECTO"
            if getattr(self.instance, "inicio", None) and getattr(self.instance, "fin", None):
                inicio = self.instance.inicio
                fin = self.instance.fin
                delta = (fin.hour * 60 + fin.minute) - (inicio.hour * 60 + inicio.minute)
                self.fields["duracion_minutos"].initial = max(delta, 5)

    def clean_nombre(self):
        return (self.cleaned_data.get("nombre") or "").strip()

    def clean_ubicacion(self):
        return (self.cleaned_data.get("ubicacion") or "").strip()

    def clean_tags(self):
        return (self.cleaned_data.get("tags") or "").strip()

    def clean(self):
        from datetime import date, datetime, timedelta

        cleaned = super().clean()
        target_type = (cleaned.get("target_type") or "PONENCIA").strip().upper()
        proyecto = cleaned.get("proyecto")
        ponencia = cleaned.get("ponencia")
        inicio = cleaned.get("inicio")
        duracion = cleaned.get("duracion_minutos")

        if target_type == "PONENCIA":
            cleaned["proyecto"] = None
            if not ponencia:
                self.add_error("ponencia", "Selecciona la ponencia a la que se asignará el espacio.")
        elif target_type == "PROYECTO":
            cleaned["ponencia"] = None
            if not proyecto:
                self.add_error("proyecto", "Selecciona el proyecto al que se asignará el espacio.")
        else:
            self.add_error("target_type", "Selecciona un tipo de registro válido.")

        if inicio and duracion:
            base = datetime.combine(date.today(), inicio)
            fin_dt = base + timedelta(minutes=int(duracion))
            if fin_dt.date() != base.date():
                self.add_error("duracion_minutos", "La duración no puede rebasar el mismo día de programación.")
            else:
                cleaned["fin_calculado"] = fin_dt.time()

        return cleaned


class ReporteGenerarForm(forms.Form):
    nombre = forms.CharField(required=False, max_length=220)
    categoria = forms.ChoiceField(
        choices=[
            (Reporte.CATEG_INSCRIPCIONES, "Inscripciones"),
            (Reporte.CATEG_EVALUACIONES, "Evaluaciones"),
            (Reporte.CATEG_ASISTENCIA, "Asistencia"),
            (Reporte.CATEG_GENERAL, "General"),
        ]
    )
    formato = forms.ChoiceField(choices=Reporte.FORMATOS)
    modo = forms.ChoiceField(choices=Reporte.MODOS)
    proyecto = forms.ModelChoiceField(
        queryset=EvaluacionProyecto.objects.none(),
        required=False,
        label="Filtrar registro programado",
        empty_label="Todo el evento",
    )

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        super().__init__(*args, **kwargs)
        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs

        self.fields["nombre"].widget.attrs.update({
            "placeholder": "Ej. Resumen ejecutivo del evento",
        })

    def clean_nombre(self):
        return (self.cleaned_data.get("nombre") or "").strip()

    def clean(self):
        cleaned = super().clean()
        categoria = (cleaned.get("categoria") or "").strip().upper()
        if categoria != Reporte.CATEG_EVALUACIONES:
            cleaned["proyecto"] = None
        return cleaned


class ReporteEditarForm(forms.ModelForm):
    class Meta:
        model = Reporte
        fields = ["nombre", "estado"]


# =========================
# ✅ NUEVO: CONFIGURACIÓN
# =========================
class ConfigCuentaForm(forms.Form):
    nombres = forms.CharField(max_length=150, required=True)
    apellidos = forms.CharField(max_length=150, required=True)
    correo = forms.EmailField(required=True)

    # Password change (opcional)
    password_actual = forms.CharField(required=False, widget=forms.PasswordInput())
    password_nueva = forms.CharField(required=False, widget=forms.PasswordInput())
    password_confirmacion = forms.CharField(required=False, widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        self.user: User = kwargs.pop("user")
        super().__init__(*args, **kwargs)

    def clean_correo(self):
        email = (self.cleaned_data.get("correo") or "").strip().lower()
        if not email:
            raise ValidationError("El correo es obligatorio.")
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.user.pk)
        if qs.exists():
            raise ValidationError("Ya existe un usuario con ese correo.")
        return email

    def clean(self):
        cleaned = super().clean()
        actual = (cleaned.get("password_actual") or "").strip()
        nueva = (cleaned.get("password_nueva") or "").strip()
        conf = (cleaned.get("password_confirmacion") or "").strip()

        # Si el usuario intenta cambiar password: exigir actual + nueva + confirmación
        if any([actual, nueva, conf]):
            if not actual:
                self.add_error("password_actual", "Ingresa tu contraseña actual.")
            if not nueva:
                self.add_error("password_nueva", "Ingresa la nueva contraseña.")
            if nueva and conf and nueva != conf:
                self.add_error("password_confirmacion", "Las contraseñas no coinciden.")
            if actual and not self.user.check_password(actual):
                self.add_error("password_actual", "La contraseña actual es incorrecta.")
            if nueva:
                try:
                    validate_password(nueva, user=self.user)
                except ValidationError as e:
                    self.add_error("password_nueva", e.messages)

        return cleaned


class ConfigPerfilForm(forms.ModelForm):
    class Meta:
        model = PerfilUsuario
        fields = ["puesto", "institucion", "telefono", "bio", "avatar", "cv"]