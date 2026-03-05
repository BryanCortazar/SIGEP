from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .models import Ponencia


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
                "class": "w-full h-11 rounded-lg border-slate-300 focus:border-primary focus:ring-primary",
                "placeholder": "Título de la ponencia"
            }),
            "tipo": forms.TextInput(attrs={
                "class": "w-full h-11 rounded-lg border-slate-300 focus:border-primary focus:ring-primary",
                "placeholder": "Ej: Ponencia magistral, cartel, taller, mesa redonda..."
            }),
            "area_tematica": forms.TextInput(attrs={
                "class": "w-full h-11 rounded-lg border-slate-300 focus:border-primary focus:ring-primary",
                "placeholder": "Área temática (opcional)"
            }),
            "resumen": forms.Textarea(attrs={
                "class": "w-full rounded-lg border-slate-300 focus:border-primary focus:ring-primary",
                "rows": 5,
                "placeholder": "Resumen (opcional)"
            }),
            "autores": forms.Textarea(attrs={
                "class": "w-full rounded-lg border-slate-300 focus:border-primary focus:ring-primary",
                "rows": 3,
                "placeholder": "Autores (opcional). Ej: Nombre 1; Nombre 2; ..."
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
        t = (self.cleaned_data.get("titulo") or "").strip()
        if len(t) < 5:
            raise ValidationError("El título es demasiado corto.")
        return t

    def clean_resumen(self):
        r = (self.cleaned_data.get("resumen") or "").strip()
        if len(r) > 3000:
            raise ValidationError("El resumen excede el límite (3000 caracteres).")
        return 