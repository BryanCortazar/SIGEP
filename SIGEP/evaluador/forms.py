from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model

from .models import PerfilUsuario


INPUT_CSS = (
    "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-sm text-slate-800 "
    "focus:border-primary focus:outline-none focus:ring-4 focus:ring-blue-100"
)
TEXTAREA_CSS = (
    "w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-sm text-slate-800 "
    "focus:border-primary focus:outline-none focus:ring-4 focus:ring-blue-100 min-h-[120px]"
)
FILE_CSS = "block w-full text-sm text-slate-700 file:mr-4 file:rounded-lg file:border-0 file:bg-slate-100 file:px-4 file:py-2 file:font-medium hover:file:bg-slate-200"


class EvaluadorCuentaForm(forms.Form):
    nombres = forms.CharField(max_length=150)
    apellidos = forms.CharField(max_length=150, required=False)
    correo = forms.EmailField()
    password_actual = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    password_nueva = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    password_confirmacion = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["nombres"].initial = user.first_name
        self.fields["apellidos"].initial = user.last_name
        self.fields["correo"].initial = user.email
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", INPUT_CSS)
            field.widget.attrs.setdefault("placeholder", field.label or name.replace("_", " ").title())

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        User = get_user_model()
        exists = User.objects.filter(email__iexact=correo).exclude(pk=self.user.pk).exists()
        if exists:
            raise forms.ValidationError("Este correo ya está registrado por otro usuario.")
        return correo

    def clean(self):
        data = super().clean()
        actual = data.get("password_actual", "")
        nueva = data.get("password_nueva", "")
        confirm = data.get("password_confirmacion", "")

        if any([actual, nueva, confirm]):
            if not actual:
                self.add_error("password_actual", "Debes escribir tu contraseña actual.")
            elif not self.user.check_password(actual):
                self.add_error("password_actual", "La contraseña actual es incorrecta.")

            if not nueva:
                self.add_error("password_nueva", "Debes escribir una nueva contraseña.")
            elif len(nueva) < 8:
                self.add_error("password_nueva", "La nueva contraseña debe tener al menos 8 caracteres.")

            if nueva and confirm and nueva != confirm:
                self.add_error("password_confirmacion", "La confirmación no coincide con la nueva contraseña.")

        return data


class EvaluadorPerfilForm(forms.ModelForm):
    class Meta:
        model = PerfilUsuario
        fields = ["puesto", "institucion", "telefono", "bio", "avatar", "cv"]
        widgets = {
            "puesto": forms.TextInput(attrs={"class": INPUT_CSS, "placeholder": "Puesto o especialidad"}),
            "institucion": forms.TextInput(attrs={"class": INPUT_CSS, "placeholder": "Institución"}),
            "telefono": forms.TextInput(attrs={"class": INPUT_CSS, "placeholder": "Teléfono"}),
            "bio": forms.Textarea(attrs={"class": TEXTAREA_CSS, "placeholder": "Descripción profesional"}),
            "avatar": forms.ClearableFileInput(attrs={"class": FILE_CSS, "accept": ".jpg,.jpeg,.png,.webp"}),
            "cv": forms.ClearableFileInput(attrs={"class": FILE_CSS, "accept": ".pdf"}),
        }

    def clean_telefono(self):
        telefono = "".join(ch for ch in (self.cleaned_data.get("telefono") or "") if ch.isdigit())
        if telefono and len(telefono) not in (10, 12, 13):
            raise forms.ValidationError("Captura un teléfono válido.")
        return telefono