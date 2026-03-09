from __future__ import annotations

import os

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Ponencia

User = get_user_model()


def validate_file_size(file, max_mb: int, label: str):
    max_bytes = max_mb * 1024 * 1024
    size = int(getattr(file, "size", 0) or 0)
    if size > max_bytes:
        raise ValidationError(f"{label} excede el tamaño permitido de {max_mb} MB.")


def validate_avatar_file(file):
    ext = os.path.splitext(getattr(file, "name", ""))[1].lower()
    allowed = [".jpg", ".jpeg", ".png", ".webp"]
    if ext not in allowed:
        raise ValidationError("La foto debe estar en formato JPG, JPEG, PNG o WebP.")
    validate_file_size(file, 5, "La foto de perfil")


def validate_cv_file(file):
    ext = os.path.splitext(getattr(file, "name", ""))[1].lower()
    if ext != ".pdf":
        raise ValidationError("El CV debe estar en formato PDF.")
    validate_file_size(file, 15, "El CV")


class SeleccionEventoForm(forms.Form):
    evento_id = forms.IntegerField(required=True)


class PonenciaForm(forms.ModelForm):
    class Meta:
        model = Ponencia
        fields = [
            "titulo",
            "tipo",
            "area_tematica",
            "resumen",
            "autores",
            "archivo_resumen",
            "presentacion",
        ]
        widgets = {
            "titulo": forms.TextInput(attrs={
                "class": "w-full h-11 rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "placeholder": "Título de la ponencia"
            }),
            "tipo": forms.TextInput(attrs={
                "class": "w-full h-11 rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "placeholder": "Ej. Ponencia magistral, panel, taller..."
            }),
            "area_tematica": forms.TextInput(attrs={
                "class": "w-full h-11 rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "placeholder": "Área temática"
            }),
            "resumen": forms.Textarea(attrs={
                "class": "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "rows": 5,
                "placeholder": "Resumen de la ponencia"
            }),
            "autores": forms.Textarea(attrs={
                "class": "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "rows": 3,
                "placeholder": "Autores de la ponencia"
            }),
            "archivo_resumen": forms.ClearableFileInput(attrs={
                "class": "w-full text-sm",
                "accept": ".pdf"
            }),
            "presentacion": forms.ClearableFileInput(attrs={
                "class": "w-full text-sm",
                "accept": ".pdf,.ppt,.pptx"
            }),
        }

    def clean_titulo(self):
        titulo = (self.cleaned_data.get("titulo") or "").strip()
        if len(titulo) < 5:
            raise ValidationError("El título es demasiado corto.")
        return titulo

    def clean_tipo(self):
        return (self.cleaned_data.get("tipo") or "").strip()

    def clean_area_tematica(self):
        return (self.cleaned_data.get("area_tematica") or "").strip()

    def clean_resumen(self):
        resumen = (self.cleaned_data.get("resumen") or "").strip()
        if len(resumen) > 3000:
            raise ValidationError("El resumen excede el límite permitido de 3000 caracteres.")
        return resumen

    def clean_autores(self):
        return (self.cleaned_data.get("autores") or "").strip()


class GestionParticipacionForm(forms.ModelForm):
    class Meta:
        model = Ponencia
        fields = [
            "cv_documento",
            "resena_biografica",
            "diapositivas_presentacion",
            "requerimientos_tecnicos",
        ]
        widgets = {
            "cv_documento": forms.ClearableFileInput(attrs={
                "class": "w-full text-sm",
                "accept": ".pdf"
            }),
            "resena_biografica": forms.ClearableFileInput(attrs={
                "class": "w-full text-sm",
                "accept": ".pdf"
            }),
            "diapositivas_presentacion": forms.ClearableFileInput(attrs={
                "class": "w-full text-sm",
                "accept": ".pdf,.ppt,.pptx"
            }),
            "requerimientos_tecnicos": forms.Textarea(attrs={
                "class": "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary text-sm",
                "rows": 5,
                "placeholder": "Ejemplo: micrófono inalámbrico, proyector HDMI, audio, internet, etc."
            }),
        }

    def clean_requerimientos_tecnicos(self):
        texto = (self.cleaned_data.get("requerimientos_tecnicos") or "").strip()
        if len(texto) > 1500:
            raise ValidationError("Los requerimientos técnicos exceden el límite permitido de 1500 caracteres.")
        return texto


class PonenteCuentaForm(forms.Form):
    nombres = forms.CharField(max_length=150, required=True)
    apellidos = forms.CharField(max_length=150, required=True)
    correo = forms.EmailField(required=True)

    password_actual = forms.CharField(required=False)
    password_nueva1 = forms.CharField(required=False)
    password_nueva2 = forms.CharField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        self.password_changed = False
        super().__init__(*args, **kwargs)

        if user and not self.is_bound:
            self.fields["nombres"].initial = user.first_name
            self.fields["apellidos"].initial = user.last_name
            self.fields["correo"].initial = user.email

    def clean_correo(self):
        correo = (self.cleaned_data.get("correo") or "").strip().lower()
        if User.objects.exclude(pk=self.user.pk).filter(email__iexact=correo).exists():
            raise ValidationError("Ya existe una cuenta registrada con este correo.")
        return correo

    def clean(self):
        cleaned_data = super().clean()

        password_actual = cleaned_data.get("password_actual") or ""
        password_nueva1 = cleaned_data.get("password_nueva1") or ""
        password_nueva2 = cleaned_data.get("password_nueva2") or ""

        quiere_cambiar_password = any([password_actual, password_nueva1, password_nueva2])

        if quiere_cambiar_password:
            if not password_actual:
                self.add_error("password_actual", "Debes capturar tu contraseña actual.")
            elif not self.user.check_password(password_actual):
                self.add_error("password_actual", "La contraseña actual no es correcta.")

            if not password_nueva1:
                self.add_error("password_nueva1", "Debes capturar la nueva contraseña.")

            if not password_nueva2:
                self.add_error("password_nueva2", "Debes confirmar la nueva contraseña.")

            if password_nueva1 and password_nueva2 and password_nueva1 != password_nueva2:
                self.add_error("password_nueva2", "La confirmación de contraseña no coincide.")

            if password_nueva1 and len(password_nueva1) < 8:
                self.add_error("password_nueva1", "La nueva contraseña debe tener al menos 8 caracteres.")

        return cleaned_data

    def save(self):
        user = self.user
        user.first_name = (self.cleaned_data.get("nombres") or "").strip()
        user.last_name = (self.cleaned_data.get("apellidos") or "").strip()
        user.email = (self.cleaned_data.get("correo") or "").strip().lower()

        password_nueva1 = (self.cleaned_data.get("password_nueva1") or "").strip()
        if password_nueva1:
            user.set_password(password_nueva1)
            self.password_changed = True

        user.save()
        return user


class PonentePerfilForm(forms.Form):
    institucion = forms.CharField(max_length=180, required=False)
    especialidad = forms.CharField(max_length=180, required=False)
    telefono = forms.CharField(max_length=30, required=False)
    bio = forms.CharField(required=False, widget=forms.Textarea)
    avatar = forms.FileField(required=False)
    cv = forms.FileField(required=False)

    def __init__(self, *args, user=None, profile=None, **kwargs):
        self.user = user
        self.profile = profile
        super().__init__(*args, **kwargs)

        if profile and not self.is_bound:
            self.fields["institucion"].initial = getattr(profile, "institucion", "")
            self.fields["especialidad"].initial = getattr(profile, "especialidad", "")
            self.fields["telefono"].initial = getattr(profile, "telefono", "")
            self.fields["bio"].initial = getattr(profile, "bio", "")

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar:
            validate_avatar_file(avatar)
        return avatar

    def clean_cv(self):
        cv = self.cleaned_data.get("cv")
        if cv:
            validate_cv_file(cv)
        return cv

    def save(self):
        profile = self.profile
        user = self.user

        simple_fields = ["institucion", "especialidad", "telefono", "bio"]
        file_fields = ["avatar", "cv"]

        profile_changed = False
        user_changed = False

        for field in simple_fields:
            value = (self.cleaned_data.get(field) or "").strip()
            if profile is not None and hasattr(profile, field):
                setattr(profile, field, value)
                profile_changed = True
            elif hasattr(user, field):
                setattr(user, field, value)
                user_changed = True

        for field in file_fields:
            value = self.cleaned_data.get(field)
            if value:
                if profile is not None and hasattr(profile, field):
                    setattr(profile, field, value)
                    profile_changed = True
                elif hasattr(user, field):
                    setattr(user, field, value)
                    user_changed = True

        if profile is not None and hasattr(profile, "save") and profile_changed:
            profile.save()

        if user_changed:
            user.save()

        return profile