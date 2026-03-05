from __future__ import annotations

from django import forms
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
        password = cleaned.get("password") or ""
        if not user_id and not password:
            self.add_error("password", "La contraseña es obligatoria para registrar al usuario.")
        return cleaned


class ActividadCronogramaForm(forms.ModelForm):
    class Meta:
        model = ActividadCronograma
        fields = ["titulo", "inicio", "fin", "responsable"]


class EvaluacionProyectoForm(forms.ModelForm):
    class Meta:
        model = EvaluacionProyecto
        fields = ["titulo", "ponente", "inicio", "fin", "lugar"]


class RubricaForm(forms.ModelForm):
    class Meta:
        model = Rubrica
        fields = ["titulo", "estado", "proyecto"]

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].required = False
        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs


class EspacioForm(forms.ModelForm):
    class Meta:
        model = Espacio
        fields = ["nombre", "tipo", "capacidad", "ubicacion", "estado", "proyecto", "tags"]

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        super().__init__(*args, **kwargs)
        self.fields["proyecto"].required = False
        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs


class ReporteGenerarForm(forms.Form):
    nombre = forms.CharField(required=False, max_length=220)
    categoria = forms.ChoiceField(choices=Reporte.CATEGORIAS)
    formato = forms.ChoiceField(choices=Reporte.FORMATOS)
    modo = forms.ChoiceField(choices=Reporte.MODOS)
    proyecto = forms.ModelChoiceField(queryset=EvaluacionProyecto.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        proyectos_qs = kwargs.pop("proyectos_qs", None)
        super().__init__(*args, **kwargs)
        if proyectos_qs is not None:
            self.fields["proyecto"].queryset = proyectos_qs


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