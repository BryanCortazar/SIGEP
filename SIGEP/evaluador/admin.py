from django.contrib import admin
from .models import EvaluacionEntrega


@admin.register(EvaluacionEntrega)
class EvaluacionEntregaAdmin(admin.ModelAdmin):
    # ✅ Campos reales del modelo + tu TimeStampedModel (creado_en/actualizado_en)
    list_display = ("id", "asignacion", "estado", "calificacion", "fecha_envio", "creado_en")
    list_filter = ("estado",)
    search_fields = ("asignacion__evaluador__email", "asignacion__proyecto__titulo")