from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from administrador.models import Evento
from .models import PerfilParticipante, ProyectoParticipante

User = get_user_model()


class SeleccionEventoForm(forms.Form):
    evento_id = forms.IntegerField(required=True, widget=forms.HiddenInput())


class ProyectoParticipanteForm(forms.ModelForm):
    class Meta:
        model = ProyectoParticipante
        fields = [
            "nombre_participante",
            "correo",
            "telefono",
            "institucion_empresa",
            "nombre_proyecto",
            "categoria",
            "numero_integrantes",
            "resumen",
            "presentacion",
            "informe",
            "requerimientos_tecnicos",
        ]
        widgets = {
            "resumen": forms.Textarea(attrs={"rows": 4}),
            "requerimientos_tecnicos": forms.Textarea(attrs={"rows": 4}),
            "categoria": forms.Select(),
            "numero_integrantes": forms.NumberInput(attrs={"min": 1, "max": 10}),
        }

    def __init__(self, *args, **kwargs):
        self.evento = kwargs.pop("evento", None)
        self.usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            css = getattr(field.widget, "attrs", {})
            css.setdefault("class", "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary")
            field.widget.attrs = css

    def clean_correo(self):
        return (self.cleaned_data.get("correo") or "").strip().lower()

    def clean_telefono(self):
        telefono = "".join(ch for ch in (self.cleaned_data.get("telefono") or "") if ch.isdigit())
        if len(telefono) != 10:
            raise forms.ValidationError("El teléfono debe contener exactamente 10 dígitos.")
        return telefono

    def clean_numero_integrantes(self):
        numero = self.cleaned_data.get("numero_integrantes") or 0
        if numero < 1:
            raise forms.ValidationError("Debe existir al menos un integrante.")
        if numero > 10:
            raise forms.ValidationError("El máximo permitido es 10 integrantes.")
        return numero

    def clean(self):
        cleaned = super().clean()
        evento = self.evento
        usuario = self.usuario

        if evento is None:
            raise forms.ValidationError("No se identificó el evento del registro.")

        if hasattr(evento, "estado") and str(evento.estado).upper() != "PUBLICADO":
            raise forms.ValidationError("Solo puedes registrar proyectos en eventos publicados.")

        if usuario and not self.instance.pk:
            existe = ProyectoParticipante.objects.filter(evento=evento, participante=usuario).exists()
            if existe:
                raise forms.ValidationError("Solo puedes registrar un proyecto por evento.")

        return cleaned


class GestionParticipacionForm(forms.ModelForm):
    class Meta:
        model = ProyectoParticipante
        fields = ["presentacion", "informe", "requerimientos_tecnicos"]
        widgets = {
            "requerimientos_tecnicos": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Ejemplo: proyector HDMI, extensión eléctrica, bocinas, mesa de apoyo, internet estable...",
                }
            ),
        }
        labels = {
            "presentacion": "Presentación del proyecto",
            "informe": "Documentación / informe",
            "requerimientos_tecnicos": "Requerimientos técnicos",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            css = getattr(field.widget, "attrs", {})
            if field_name in {"presentacion", "informe"}:
                css.setdefault(
                    "class",
                    "block w-full text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-blue-700",
                )
            else:
                css.setdefault("class", "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary")
            field.widget.attrs = css


class ParticipanteCuentaForm(forms.Form):
    nombres = forms.CharField(max_length=150, required=True)
    apellidos = forms.CharField(max_length=150, required=True)
    correo = forms.EmailField(required=True)
    password_actual = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    password_nueva = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    password_confirmacion = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            css = getattr(field.widget, "attrs", {})
            css.setdefault("class", "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary")
            field.widget.attrs = css

        if self.user and not self.is_bound:
            self.fields["nombres"].initial = getattr(self.user, "first_name", "")
            self.fields["apellidos"].initial = getattr(self.user, "last_name", "")
            self.fields["correo"].initial = getattr(self.user, "email", "")

    def clean_nombres(self):
        return (self.cleaned_data.get("nombres") or "").strip()

    def clean_apellidos(self):
        return (self.cleaned_data.get("apellidos") or "").strip()

    def clean_correo(self):
        correo = (self.cleaned_data.get("correo") or "").strip().lower()
        qs = User.objects.filter(email__iexact=correo)
        if self.user:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("Ya existe un usuario con este correo electrónico.")
        return correo

    def clean(self):
        cleaned = super().clean()
        actual = cleaned.get("password_actual") or ""
        nueva = cleaned.get("password_nueva") or ""
        confirm = cleaned.get("password_confirmacion") or ""

        if nueva or confirm or actual:
            if not self.user or not self.user.check_password(actual):
                self.add_error("password_actual", "La contraseña actual no es válida.")
            if nueva != confirm:
                self.add_error("password_confirmacion", "La confirmación no coincide con la nueva contraseña.")
            if nueva:
                try:
                    validate_password(nueva, self.user)
                except ValidationError as exc:
                    self.add_error("password_nueva", exc.messages)
        return cleaned

    def save(self, commit: bool = True):
        if self.user is None:
            raise ValueError("ParticipanteCuentaForm requiere un usuario asociado para guardar.")

        self.user.first_name = self.cleaned_data["nombres"]
        self.user.last_name = self.cleaned_data["apellidos"]
        self.user.email = self.cleaned_data["correo"]

        if commit:
            self.user.save(update_fields=["first_name", "last_name", "email"])
        return self.user


class ParticipantePerfilForm(forms.ModelForm):
    class Meta:
        model = PerfilParticipante
        fields = ["telefono", "institucion", "biografia", "avatar", "cv"]
        widgets = {
            "biografia": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = getattr(field.widget, "attrs", {})
            css.setdefault("class", "w-full rounded-xl border-slate-300 focus:border-primary focus:ring-primary")
            field.widget.attrs = css

    def clean_telefono(self):
        telefono = "".join(ch for ch in (self.cleaned_data.get("telefono") or "") if ch.isdigit())
        if telefono and len(telefono) != 10:
            raise forms.ValidationError("El teléfono debe contener 10 dígitos.")
        return telefono
