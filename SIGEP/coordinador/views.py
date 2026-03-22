from __future__ import annotations

import csv
import io
import os
from datetime import timedelta

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ActividadCronogramaForm,
    EvaluacionProyectoForm,
    InscripcionUsuarioForm,
    RubricaForm,
    EspacioForm,
    ReporteGenerarForm,
    ReporteEditarForm,
    ConfigCuentaForm,
    ConfigPerfilForm,
    EventoGestionForm,
)
from .models import (
    ActividadCronograma,
    EvaluacionAsignacion,
    EvaluacionProyecto,
    Inscripcion,
    Rubrica,
    RubricaAdjunto,
    RubricaCriterio,
    Espacio,
    Reporte,
    PerfilUsuario,
)

User = get_user_model()

try:
    from administrador.utils_auditoria import registrar_auditoria
except Exception:  # pragma: no cover
    def registrar_auditoria(**kwargs):
        return None


# =========================================================
# Helpers: Evento actual desde sesión + permisos
# =========================================================
def _get_evento_model():
    """
    Resuelve el modelo Evento sin romper el server aunque cambie de app.
    Ajusta el orden según tu arquitectura real.
    """
    try:
        from .models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    try:
        from administrador.models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    try:
        from eventos.models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    return None


def _get_evento_id_from_session(request: HttpRequest) -> int | None:
    eid = request.session.get("evento_id") or request.session.get("evento_actual_id")
    try:
        return int(eid) if eid else None
    except Exception:
        return None


def _get_evento_actual(request: HttpRequest):
    Evento = _get_evento_model()
    if not Evento:
        return None

    evento_id = _get_evento_id_from_session(request)
    if not evento_id:
        return None

    return Evento.objects.filter(id=evento_id).first()


def _user_can_manage_evento(user, evento) -> bool:
    """
    Permiso: coordinador/admin inscrito en el evento, o creador si existe creado_por.
    """
    if not user or not getattr(user, "is_authenticated", False) or not evento:
        return False

    permitido = Inscripcion.objects.filter(
        evento=evento,
        usuario=user,
        rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
    ).exists()
    if permitido:
        return True

    try:
        field_names = {f.name for f in evento._meta.fields}
        if "creado_por" in field_names and getattr(evento, "creado_por_id", None) == user.id:
            return True
    except Exception:
        pass

    return False


def _require_evento_or_redirect(request: HttpRequest):
    evento = _get_evento_actual(request)
    if not evento:
        messages.warning(request, "Selecciona un evento antes de continuar.")
        return None, redirect("coordinador:dashboard")

    if not _user_can_manage_evento(request.user, evento):
        request.session.pop("evento_id", None)
        request.session.pop("evento_actual_id", None)
        request.session.modified = True
        messages.error(request, "No tienes permisos para gestionar ese evento.")
        return None, redirect("coordinador:dashboard")

    return evento, None


def _first_post_value(request: HttpRequest, *keys: str) -> str:
    for key in keys:
        value = request.POST.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _cronograma_form_from_request(request: HttpRequest, *, instance=None) -> ActividadCronogramaForm:
    """
    Normaliza nombres de campos del modal/formulario para evitar que el guardado
    falle si el template usa variantes como hora_inicio/hora_fin o actividad/nombre.
    """
    data = {
        "titulo": _first_post_value(request, "titulo", "actividad", "nombre", "nombre_actividad"),
        "inicio": _first_post_value(request, "inicio", "hora_inicio"),
        "fin": _first_post_value(request, "fin", "hora_fin"),
        "responsable": _first_post_value(request, "responsable", "encargado"),
    }
    return ActividadCronogramaForm(data, instance=instance)


def _flatten_form_errors(form) -> str:
    errores = []
    for field, field_errors in form.errors.items():
        label = form.fields.get(field).label if field in getattr(form, "fields", {}) else None
        nombre = label or ("Formulario" if field == "__all__" else field.replace("_", " ").capitalize())
        for err in field_errors:
            errores.append(f"{nombre}: {err}")
    return " | ".join(errores)


# =========================================================
# PDF mínimo (sin dependencias externas)
# =========================================================
def _build_minimal_pdf(lines: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y_start = 760
    leading = 14
    text_ops = ["BT", "/F1 12 Tf", f"72 {y_start} Td"]
    for i, line in enumerate(lines[:55]):
        if i > 0:
            text_ops.append(f"0 -{leading} Td")
        text_ops.append(f"({esc(line)}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", "ignore")

    objs = []
    objs.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objs.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objs.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources<< /Font<< /F1 4 0 R >> >> /Contents 5 0 R >>endobj\n"
    )
    objs.append(b"4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
    objs.append(
        f"5 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n"
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    xref = [0]
    for obj in objs:
        xref.append(out.tell())
        out.write(obj)

    xref_pos = out.tell()
    out.write(f"xref\n0 {len(xref)}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for pos in xref[1:]:
        out.write(f"{pos:010d} 00000 n \n".encode("ascii"))

    out.write(b"trailer<< ")
    out.write(f"/Size {len(xref)} /Root 1 0 R".encode("ascii"))
    out.write(b" >>\nstartxref\n")
    out.write(f"{xref_pos}\n%%EOF".encode("ascii"))
    return out.getvalue()


def _xlsx_from_rows(sheet_name: str, headers: list[str], rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook  # type: ignore

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Reporte")[:31]
    ws.append(headers)
    for r in rows:
        ws.append(r)

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# =========================================================
# Dashboard (Panel de coordinación)
# =========================================================
def _safe_get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _coordinador_eventos_queryset(user):
    Evento = _get_evento_model()
    if not Evento or not getattr(user, "is_authenticated", False):
        return Evento.objects.none() if Evento else []

    qs = Evento.objects.none()
    try:
        field_names = {f.name for f in Evento._meta.fields}
        if "creado_por" in field_names:
            qs = qs | Evento.objects.filter(creado_por=user)
    except Exception:
        pass

    evento_ids = Inscripcion.objects.filter(
        usuario=user,
        rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
    ).values_list("evento_id", flat=True)
    qs = (qs | Evento.objects.filter(id__in=evento_ids)).distinct()

    try:
        return qs.order_by("-fecha", "-id")
    except Exception:
        return qs.order_by("-id")


def _event_count(qs, estado: str) -> int:
    try:
        return qs.filter(estado=estado).count()
    except Exception:
        return 0


def _build_dashboard_context(request: HttpRequest, evento):
    eventos_qs = _coordinador_eventos_queryset(request.user)
    eventos_ids = list(eventos_qs.values_list("id", flat=True)) if hasattr(eventos_qs, 'values_list') else []
    scope_ids = [evento.id] if evento else eventos_ids

    Ponencia = _safe_get_model("ponente", "Ponencia")
    AuditoriaLog = _safe_get_model("administrador", "AuditoriaLog")

    today = timezone.localdate()

    total_eventos = len(eventos_ids)
    eventos_borrador = _event_count(eventos_qs, "BORRADOR")
    eventos_publicados = _event_count(eventos_qs, "PUBLICADO")
    eventos_cerrados = _event_count(eventos_qs, "CERRADO")
    try:
        eventos_proximos = eventos_qs.filter(fecha__gte=today).count()
    except Exception:
        eventos_proximos = 0

    if scope_ids:
        inscripciones_total = Inscripcion.objects.filter(evento_id__in=scope_ids).count()
        proyectos_total = EvaluacionProyecto.objects.filter(evento_id__in=scope_ids).count()
        rubricas_qs = Rubrica.objects.filter(evento_id__in=scope_ids)
        rubricas_total = rubricas_qs.count()
        rubricas_activas = rubricas_qs.filter(estado=Rubrica.ESTADO_ACTIVA).count()
        rubricas_borrador = rubricas_qs.filter(estado=Rubrica.ESTADO_BORRADOR).count()
        espacios_total = Espacio.objects.filter(evento_id__in=scope_ids).count()
        reportes_total = Reporte.objects.filter(evento_id__in=scope_ids).count()
        asignaciones_total = EvaluacionAsignacion.objects.filter(proyecto__evento_id__in=scope_ids).count()
        asignaciones_hoy = EvaluacionProyecto.objects.filter(evento_id__in=scope_ids).filter(inicio__isnull=False).count()
    else:
        inscripciones_total = proyectos_total = rubricas_total = rubricas_activas = 0
        rubricas_borrador = espacios_total = reportes_total = asignaciones_total = asignaciones_hoy = 0

    ponencias_total = 0
    ponencias_revision = 0
    ponencias_aceptadas = 0
    if Ponencia and scope_ids:
        try:
            pon_qs = Ponencia.objects.filter(evento_id__in=scope_ids)
            ponencias_total = pon_qs.count()
            state_field = {f.name for f in Ponencia._meta.fields}
            if "estado" in state_field:
                ponencias_revision = pon_qs.filter(estado=getattr(Ponencia, "ESTADO_EN_REVISION", "EN_REVISION")).count()
                ponencias_aceptadas = pon_qs.filter(estado=getattr(Ponencia, "ESTADO_ACEPTADA", "ACEPTADA")).count()
        except Exception:
            pass

    proyectos_sin_rubrica = max(proyectos_total - rubricas_total, 0)
    registros_total = proyectos_total + ponencias_total + inscripciones_total

    user_logs = None
    logs_hoy = logs_7d = 0
    modulo_top = "Sin datos"
    accion_top = "Sin datos"
    actividad_reciente = []
    dias_labels = []
    dias_values = []
    if AuditoriaLog:
        try:
            user_logs = AuditoriaLog.objects.filter(usuario=request.user)
            logs_hoy = user_logs.filter(fecha__date=today).count()
            logs_7d = user_logs.filter(fecha__date__gte=today - timedelta(days=6)).count()
            actividad_reciente = list(user_logs.order_by("-fecha")[:10])

            modulo_data = user_logs.exclude(modulo="").values("modulo").annotate(total=Count("id")).order_by("-total").first()
            if modulo_data:
                modulo_top = modulo_data["modulo"]
            accion_data = user_logs.exclude(accion_tipo="").values("accion_tipo").annotate(total=Count("id")).order_by("-total").first()
            if accion_data:
                accion_top = accion_data["accion_tipo"].replace("_", " ").title()

            for i in range(6, -1, -1):
                day = today - timedelta(days=i)
                dias_labels.append(day.strftime("%d/%m"))
                dias_values.append(user_logs.filter(fecha__date=day).count())
        except Exception:
            actividad_reciente = []

    summary_cards = [
        {
            "label": "Eventos bajo gestión",
            "value": total_eventos,
            "help": "Eventos que puedes coordinar actualmente",
            "icon": "event_note",
        },
        {
            "label": "Eventos publicados",
            "value": eventos_publicados,
            "help": "Disponibles para registro operativo",
            "icon": "campaign",
        },
        {
            "label": "Registros operativos",
            "value": registros_total,
            "help": "Inscripciones, proyectos y ponencias del alcance actual",
            "icon": "analytics",
        },
        {
            "label": "Movimientos 7 días",
            "value": logs_7d,
            "help": "Actividad reciente registrada para tu cuenta",
            "icon": "history",
        },
    ]

    kpi_groups = [
        {
            "title": "Gestión de eventos",
            "description": "Estado general de los eventos que operas.",
            "items": [
                {"label": "Total eventos", "value": total_eventos},
                {"label": "Borrador", "value": eventos_borrador},
                {"label": "Publicados", "value": eventos_publicados},
                {"label": "Cerrados", "value": eventos_cerrados},
                {"label": "Próximos", "value": eventos_proximos},
            ],
        },
        {
            "title": "Registros académicos",
            "description": "Carga operativa del alcance seleccionado.",
            "items": [
                {"label": "Inscripciones", "value": inscripciones_total},
                {"label": "Proyectos", "value": proyectos_total},
                {"label": "Ponencias", "value": ponencias_total},
                {"label": "Ponencias en revisión", "value": ponencias_revision},
                {"label": "Ponencias aceptadas", "value": ponencias_aceptadas},
            ],
        },
        {
            "title": "Rúbricas y logística",
            "description": "Seguimiento de instrumentos y programación.",
            "items": [
                {"label": "Rúbricas totales", "value": rubricas_total},
                {"label": "Rúbricas activas", "value": rubricas_activas},
                {"label": "Rúbricas borrador", "value": rubricas_borrador},
                {"label": "Proyectos sin rúbrica", "value": proyectos_sin_rubrica},
                {"label": "Espacios", "value": espacios_total},
                {"label": "Asignaciones", "value": asignaciones_total},
                {"label": "Reportes", "value": reportes_total},
            ],
        },
        {
            "title": "Trazabilidad",
            "description": "Actividad reciente y patrones de uso del coordinador.",
            "items": [
                {"label": "Logs de hoy", "value": logs_hoy},
                {"label": "Logs 7 días", "value": logs_7d},
                {"label": "Módulo más activo", "value": modulo_top},
                {"label": "Acción frecuente", "value": accion_top},
            ],
        },
    ]

    charts_data = {
        "eventos_estado": {
            "title": "Estado de eventos",
            "labels": ["Borrador", "Publicado", "Cerrado"],
            "values": [eventos_borrador, eventos_publicados, eventos_cerrados],
        },
        "registros_alcance": {
            "title": "Registros del alcance actual",
            "labels": ["Inscripciones", "Proyectos", "Ponencias"],
            "values": [inscripciones_total, proyectos_total, ponencias_total],
        },
        "logistica": {
            "title": "Rúbricas y logística",
            "labels": ["Rúbricas activas", "Rúbricas borrador", "Espacios", "Asignaciones"],
            "values": [rubricas_activas, rubricas_borrador, espacios_total, asignaciones_total],
        },
        "actividad_7d": {
            "title": "Actividad últimos 7 días",
            "labels": dias_labels,
            "values": dias_values,
        },
    }

    evento_actual_stats = None
    if evento:
        evento_actual_stats = {
            "titulo": getattr(evento, "titulo", f"Evento {evento.id}"),
            "estado": getattr(evento, "estado", "BORRADOR"),
            "fecha": getattr(evento, "fecha", None),
            "lugar": getattr(evento, "lugar", ""),
            "proyectos": EvaluacionProyecto.objects.filter(evento=evento).count(),
            "ponencias": ponencias_total if scope_ids == [evento.id] else (Ponencia.objects.filter(evento=evento).count() if Ponencia else 0),
            "rubricas": Rubrica.objects.filter(evento=evento).count(),
            "espacios": Espacio.objects.filter(evento=evento).count(),
        }

    return {
        "eventos": eventos_qs,
        "evento": evento,
        "scope_label": "evento actual" if evento else "alcance operativo",
        "summary_cards": summary_cards,
        "kpi_groups": kpi_groups,
        "charts_data": charts_data,
        "actividad_reciente": actividad_reciente,
        "evento_actual_stats": evento_actual_stats,
    }


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    evento = _get_evento_actual(request)
    context = {
        "active": "dashboard",
        **_build_dashboard_context(request, evento),
    }
    return render(request, "coordinador/dashboard/index.html", context)


# =========================================================
# EVENTOS (crear / seleccionar / gestión)
# =========================================================
def _evento_related_counts(evento) -> dict[str, int]:
    Ponencia = _safe_get_model("ponente", "Ponencia")
    counts = {
        "inscripciones": Inscripcion.objects.filter(evento=evento).count(),
        "proyectos": EvaluacionProyecto.objects.filter(evento=evento).count(),
        "rubricas": Rubrica.objects.filter(evento=evento).count(),
        "espacios": Espacio.objects.filter(evento=evento).count(),
        "reportes": Reporte.objects.filter(evento=evento).count(),
        "ponencias": 0,
    }
    if Ponencia:
        try:
            counts["ponencias"] = Ponencia.objects.filter(evento=evento).count()
        except Exception:
            counts["ponencias"] = 0
    counts["total_relaciones"] = sum(counts.values())
    return counts


def _eventos_base_context(request: HttpRequest, *, selected_evento=None, form: EventoGestionForm | None = None):
    eventos_qs = _coordinador_eventos_queryset(request.user)
    q = (request.GET.get("q") or "").strip()
    estado = (request.GET.get("estado") or "").strip().upper()

    if q:
        eventos_qs = eventos_qs.filter(
            Q(titulo__icontains=q)
            | Q(descripcion__icontains=q)
            | Q(lugar__icontains=q)
        )
    if estado in {"BORRADOR", "PUBLICADO", "CERRADO"}:
        eventos_qs = eventos_qs.filter(estado=estado)

    if selected_evento is None:
        selected_id = (request.GET.get("evento") or "").strip()
        if selected_id.isdigit():
            selected_evento = eventos_qs.filter(id=int(selected_id)).first()
        if selected_evento is None:
            current = _get_evento_actual(request)
            if current and eventos_qs.filter(id=current.id).exists():
                selected_evento = current

    event_cards = []
    for ev in eventos_qs.order_by("-fecha", "-id"):
        counts = _evento_related_counts(ev)
        event_cards.append({
            "obj": ev,
            "stats": counts,
            "is_selected": bool(selected_evento and ev.id == selected_evento.id),
        })

    if form is None:
        initial = {}
        if selected_evento:
            initial = {
                "evento_id": selected_evento.id,
                "titulo": getattr(selected_evento, "titulo", ""),
                "descripcion": getattr(selected_evento, "descripcion", ""),
                "fecha": getattr(selected_evento, "fecha", None),
                "lugar": getattr(selected_evento, "lugar", ""),
                "cupo": getattr(selected_evento, "cupo", 0),
                "estado": getattr(selected_evento, "estado", "BORRADOR"),
            }
        form = EventoGestionForm(initial=initial)

    selected_stats = _evento_related_counts(selected_evento) if selected_evento else None

    return {
        "active": "eventos",
        "eventos": eventos_qs,
        "event_cards": event_cards,
        "selected_evento": selected_evento,
        "selected_stats": selected_stats,
        "evento_form": form,
        "filter_q": q,
        "filter_estado": estado,
        "estado_choices": [
            ("", "Todos"),
            ("BORRADOR", "Borrador"),
            ("PUBLICADO", "Publicado"),
            ("CERRADO", "Cerrado"),
        ],
    }


@login_required
def eventos(request: HttpRequest) -> HttpResponse:
    return render(request, "coordinador/eventos/eventos.html", _eventos_base_context(request))


def _save_evento_instance(*, evento, cleaned_data: dict, user, is_new: bool):
    evento.titulo = cleaned_data["titulo"]
    evento.descripcion = cleaned_data.get("descripcion", "")
    evento.fecha = cleaned_data["fecha"]
    evento.lugar = cleaned_data.get("lugar", "")
    evento.cupo = cleaned_data.get("cupo") or 0

    if is_new:
        try:
            field_names = {f.name for f in evento._meta.fields}
            if "creado_por" in field_names:
                evento.creado_por = user
        except Exception:
            pass
    evento.save()
    return evento


@login_required
@require_POST
@transaction.atomic
def evento_guardar(request: HttpRequest) -> HttpResponse:
    Evento = _get_evento_model()
    if not Evento:
        messages.error(request, "No se encontró el modelo Evento.")
        return redirect("coordinador:eventos")

    form = EventoGestionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la información del evento.")
        return render(request, "coordinador/eventos/eventos.html", _eventos_base_context(request, form=form))

    evento_id = form.cleaned_data.get("evento_id")
    is_new = not bool(evento_id)
    if evento_id:
        evento = get_object_or_404(_coordinador_eventos_queryset(request.user), pk=evento_id)
    else:
        evento = Evento()

    exists_qs = Evento.objects.filter(
        titulo__iexact=form.cleaned_data["titulo"],
        fecha=form.cleaned_data["fecha"],
    )
    if evento_id:
        exists_qs = exists_qs.exclude(pk=evento_id)
    if exists_qs.exists():
        messages.error(request, "Ya existe un evento con el mismo título y fecha.")
        return render(request, "coordinador/eventos/eventos.html", _eventos_base_context(request, selected_evento=evento if evento_id else None, form=form))

    evento = _save_evento_instance(evento=evento, cleaned_data=form.cleaned_data, user=request.user, is_new=is_new)

    if is_new:
        Inscripcion.objects.get_or_create(
            evento=evento,
            usuario=request.user,
            defaults={"rol": Inscripcion.ROL_COORDINADOR},
        )
        request.session["evento_id"] = evento.id
        request.session.modified = True
        registrar_auditoria(request=request, accion="COORDINADOR | CREAR | EVENTO", modulo="COORDINADOR", accion_tipo="CREAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
        messages.success(request, "Evento creado correctamente.")
    else:
        registrar_auditoria(request=request, accion="COORDINADOR | EDITAR | EVENTO", modulo="COORDINADOR", accion_tipo="EDITAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
        messages.success(request, "Evento actualizado correctamente.")

    next_target = (request.POST.get("next") or "eventos").strip()
    if next_target == "dashboard":
        return redirect("coordinador:dashboard")
    return redirect(f"{request.path.replace('/guardar/', '/') if False else ''}" or "coordinador:eventos")


@login_required
@require_POST
@transaction.atomic
def evento_crear(request: HttpRequest) -> HttpResponse:
    # Mantiene compatibilidad con el modal del dashboard.
    data = request.POST.copy()
    data.setdefault("estado", "BORRADOR")
    form = EventoGestionForm(data)
    Evento = _get_evento_model()
    if not Evento:
        messages.error(request, "No se encontró el modelo Evento.")
        return redirect("coordinador:dashboard")

    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la información del evento.")
        return redirect("coordinador:dashboard")

    if Evento.objects.filter(
        titulo__iexact=form.cleaned_data["titulo"],
        fecha=form.cleaned_data["fecha"],
    ).exists():
        messages.error(request, "Ya existe un evento con el mismo título y fecha.")
        return redirect("coordinador:dashboard")

    evento = _save_evento_instance(evento=Evento(), cleaned_data=form.cleaned_data, user=request.user, is_new=True)
    Inscripcion.objects.get_or_create(
        evento=evento,
        usuario=request.user,
        defaults={"rol": Inscripcion.ROL_COORDINADOR},
    )
    request.session["evento_id"] = evento.id
    request.session.modified = True
    registrar_auditoria(request=request, accion="COORDINADOR | CREAR | EVENTO", modulo="COORDINADOR", accion_tipo="CREAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
    messages.success(request, "Evento creado y seleccionado correctamente.")
    return redirect("coordinador:dashboard")


@login_required
@require_POST
@transaction.atomic
def evento_seleccionar(request: HttpRequest) -> HttpResponse:
    Evento = _get_evento_model()
    if not Evento:
        messages.error(request, "No se encontró el modelo Evento. Revisa dónde está definido.")
        return redirect("coordinador:dashboard")

    evento_id = (request.POST.get("evento_id") or "").strip()
    if not evento_id.isdigit():
        messages.error(request, "Selecciona un evento válido.")
        return redirect("coordinador:dashboard")

    evento = Evento.objects.filter(id=int(evento_id)).first()
    if not evento:
        messages.error(request, "El evento seleccionado no existe.")
        return redirect("coordinador:dashboard")

    if not _user_can_manage_evento(request.user, evento):
        messages.error(request, "No tienes permisos para gestionar este evento.")
        return redirect("coordinador:dashboard")

    request.session["evento_id"] = evento.id
    request.session.modified = True
    registrar_auditoria(request=request, accion="COORDINADOR | SELECCIONAR | EVENTO", modulo="COORDINADOR", accion_tipo="SELECCIONAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
    messages.success(request, f"Evento seleccionado: {getattr(evento, 'titulo', 'Evento')}")
    return redirect(request.POST.get("next") or "coordinador:dashboard")


@login_required
@require_POST
@transaction.atomic
def evento_seleccionar_directo(request: HttpRequest, pk: int) -> HttpResponse:
    evento = get_object_or_404(_coordinador_eventos_queryset(request.user), pk=pk)
    request.session["evento_id"] = evento.id
    request.session.modified = True
    registrar_auditoria(request=request, accion="COORDINADOR | SELECCIONAR | EVENTO", modulo="COORDINADOR", accion_tipo="SELECCIONAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
    messages.success(request, f"Evento seleccionado: {evento.titulo}")
    return redirect("coordinador:eventos")


@login_required
@require_POST
@transaction.atomic
def evento_cambiar_estado(request: HttpRequest, pk: int) -> HttpResponse:
    evento = get_object_or_404(_coordinador_eventos_queryset(request.user), pk=pk)
    destino = (request.POST.get("estado") or "").strip().upper()
    if destino not in {"BORRADOR", "PUBLICADO", "CERRADO"}:
        messages.error(request, "Estado de evento inválido.")
        return redirect("coordinador:eventos")

    if evento.estado == destino:
        messages.info(request, "El evento ya se encuentra en ese estado.")
        return redirect("coordinador:eventos")

    evento.estado = destino
    evento.save(update_fields=["estado", "actualizado_en"] if hasattr(evento, "actualizado_en") else ["estado"])
    registrar_auditoria(request=request, accion="COORDINADOR | ESTADO | EVENTO", modulo="COORDINADOR", accion_tipo="ESTADO", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo, "estado": destino})
    messages.success(request, f"El evento ahora está en estado {destino.title()}.")
    return redirect("coordinador:eventos")


@login_required
@require_POST
@transaction.atomic
def evento_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento = get_object_or_404(_coordinador_eventos_queryset(request.user), pk=pk)
    counts = _evento_related_counts(evento)
    if counts["total_relaciones"] > 1 or counts["inscripciones"] > 1 or any(counts[k] > 0 for k in ["proyectos", "rubricas", "espacios", "reportes", "ponencias"]):
        messages.error(request, "No se puede eliminar el evento porque ya tiene información vinculada.")
        return redirect("coordinador:eventos")

    titulo = evento.titulo
    if _get_evento_id_from_session(request) == evento.id:
        request.session.pop("evento_id", None)
        request.session.pop("evento_actual_id", None)
        request.session.modified = True

    evento.delete()
    registrar_auditoria(request=request, accion="COORDINADOR | ELIMINAR | EVENTO", modulo="COORDINADOR", accion_tipo="ELIMINAR", entidad="Evento", objeto_id=pk, detalles={"titulo": titulo})
    messages.success(request, "Evento eliminado correctamente.")
    return redirect("coordinador:eventos")


@login_required
def gestion_evento(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    selected_stats = _evento_related_counts(evento)
    operation_cards = [
        {"title": "Registrar cronograma", "url": "coordinador:cronograma", "icon": "calendar_month", "desc": "Define fechas y sesiones del evento.", "tone": "blue"},
        {"title": "Administrar inscripciones", "url": "coordinador:inscripciones", "icon": "groups", "desc": "Gestiona participantes y ponentes.", "tone": "emerald"},
        {"title": "Asignar evaluadores", "url": "coordinador:evaluadores", "icon": "fact_check", "desc": "Vincula jurados expertos.", "tone": "orange"},
        {"title": "Gestión de rúbricas", "url": "coordinador:rubricas", "icon": "rule", "desc": "Criterios y escalas de evaluación.", "tone": "purple"},
        {"title": "Asignar espacios", "url": "coordinador:espacios", "icon": "meeting_room", "desc": "Aulas, auditorios y salas virtuales.", "tone": "yellow"},
        {"title": "Reportes", "url": "coordinador:reportes", "icon": "bar_chart", "desc": "Estadísticas y exportables.", "tone": "red"},
    ]
    return render(
        request,
        "coordinador/eventos/gestion.html",
        {
            "active": "eventos",
            "evento": evento,
            "selected_stats": selected_stats,
            "operation_cards": operation_cards,
        },
    )


# =========================================================
# CRONOGRAMA
# =========================================================
@login_required
def cronograma(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    actividades = ActividadCronograma.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = ActividadCronogramaForm()
    return render(
        request,
        "coordinador/cronograma/cronograma.html",
        {"active": "cronograma", "evento": evento, "actividades": actividades, "form": form},
    )


@login_required
@require_POST
@transaction.atomic
def cronograma_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    actividad_id = (request.POST.get("actividad_id") or "").strip()
    instance = get_object_or_404(ActividadCronograma, id=actividad_id, evento=evento) if actividad_id else None

    form = _cronograma_form_from_request(request, instance=instance)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica los datos del cronograma.")
        return redirect("coordinador:cronograma")

    try:
        actividad = form.save(commit=False)
        actividad.evento = evento
        actividad.full_clean()
        actividad.save()

        if instance:
            messages.success(request, "Actividad actualizada correctamente.")
        else:
            messages.success(request, "Actividad agregada correctamente.")
    except ValidationError as e:
        if hasattr(e, "message_dict") and e.message_dict:
            errores = []
            for campo, mensajes in e.message_dict.items():
                for msg in mensajes:
                    errores.append(f"{campo}: {msg}")
            messages.error(request, " | ".join(errores))
        else:
            messages.error(request, "; ".join(e.messages) if getattr(e, "messages", None) else "No se pudo guardar la actividad.")

    return redirect("coordinador:cronograma")


@login_required
@require_POST
@transaction.atomic
def cronograma_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    actividad = get_object_or_404(ActividadCronograma, id=pk, evento=evento)
    actividad.delete()
    messages.success(request, "Actividad eliminada correctamente.")
    return redirect("coordinador:cronograma")


# =========================================================
# INSCRIPCIONES
# =========================================================
@login_required
def inscripciones(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    q = (request.GET.get("q") or "").strip()
    rol = (request.GET.get("rol") or "").strip()
    activo = (request.GET.get("activo") or "").strip()

    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento)

    if q:
        qs = qs.filter(
            Q(usuario__first_name__icontains=q)
            | Q(usuario__last_name__icontains=q)
            | Q(usuario__email__icontains=q)
            | Q(rol__icontains=q)
        )
    if rol:
        qs = qs.filter(rol=rol)
    if activo in ("0", "1"):
        qs = qs.filter(usuario__is_active=(activo == "1"))

    return render(
        request,
        "coordinador/inscripciones/inscripciones.html",
        {
            "active": "inscripciones",
            "evento": evento,
            "inscripciones": qs.order_by("-id"),
            "form": InscripcionUsuarioForm(),
            "q": q,
            "rol": rol,
            "activo": activo,
        },
    )


@login_required
@require_POST
@transaction.atomic
def inscripcion_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    form = InscripcionUsuarioForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Verifica los datos del usuario/inscripción.")
        return redirect("coordinador:inscripciones")

    user_id = form.cleaned_data.get("user_id")
    nombres = form.cleaned_data["nombres"].strip()
    apellidos = form.cleaned_data["apellidos"].strip()
    correo = form.cleaned_data["correo"].strip().lower()
    rol = form.cleaned_data["rol"]
    activo = bool(form.cleaned_data.get("activo"))
    password = (form.cleaned_data.get("password") or "").strip()

    if user_id:
        user = get_object_or_404(User, id=user_id)
        user.first_name = nombres
        user.last_name = apellidos
        user.email = correo
        if hasattr(user, "username"):
            user.username = correo
        user.is_active = activo
        if password:
            user.set_password(password)
        user.save()

        insc = Inscripcion.objects.filter(evento=evento, usuario=user).first()
        if insc:
            insc.rol = rol
            insc.save(update_fields=["rol"])

        messages.success(request, "Inscripción actualizada correctamente.")
        return redirect("coordinador:inscripciones")

    if User.objects.filter(email__iexact=correo).exists():
        messages.error(request, "Ya existe un usuario registrado con ese correo.")
        return redirect("coordinador:inscripciones")

    if not password:
        messages.error(request, "La contraseña es obligatoria para crear el usuario.")
        return redirect("coordinador:inscripciones")

    user = User(first_name=nombres, last_name=apellidos, email=correo, is_active=activo)
    if hasattr(user, "username"):
        user.username = correo
    user.set_password(password)
    user.save()

    Inscripcion.objects.create(evento=evento, usuario=user, rol=rol)
    messages.success(request, "Inscripción creada correctamente.")
    return redirect("coordinador:inscripciones")


@login_required
@require_POST
@transaction.atomic
def inscripcion_eliminar(request: HttpRequest, user_id: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    user = get_object_or_404(User, id=user_id)
    insc = Inscripcion.objects.filter(evento=evento, usuario=user).first()
    if not insc:
        messages.error(request, "No se encontró la inscripción a eliminar.")
        return redirect("coordinador:inscripciones")

    insc.delete()

    if not Inscripcion.objects.filter(usuario=user).exists():
        user.is_active = False
        user.save(update_fields=["is_active"])

    messages.success(request, "Inscripción eliminada correctamente.")
    return redirect("coordinador:inscripciones")


@login_required
def inscripciones_exportar_csv(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento).order_by("-id")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="inscripciones_evento_{evento.id}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Nombres", "Apellidos", "Correo", "Rol", "Activo", "Evento"])

    for insc in qs:
        u = insc.usuario
        writer.writerow(
            [
                (u.first_name or ""),
                (u.last_name or ""),
                (u.email or ""),
                (insc.rol or ""),
                ("SI" if u.is_active else "NO"),
                getattr(evento, "titulo", f"Evento {evento.id}"),
            ]
        )
    return response


# =========================================================
# EVALUADORES
# =========================================================
def _safe_user_display(user) -> str:
    if not user:
        return "Sin responsable"
    try:
        full_name = user.get_full_name()
    except Exception:
        full_name = ""
    return (full_name or getattr(user, "email", "") or getattr(user, "username", "") or f"Usuario {getattr(user, 'pk', '')}").strip()


def _user_role_code(user) -> str:
    value = getattr(user, "rol", "")
    try:
        value = value() if callable(value) else value
    except Exception:
        value = ""
    return str(value or "").strip().upper()


def _event_evaluadores_qs(evento):
    inscritos_ids = list(
        Inscripcion.objects.filter(
            evento=evento,
            rol=Inscripcion.ROL_EVALUADOR,
            usuario__is_active=True,
        ).values_list("usuario_id", flat=True)
    )

    filtros = Q(pk__in=inscritos_ids)
    try:
        field_names = {f.name for f in User._meta.fields}
        if "rol" in field_names:
            filtros |= Q(rol__in=["EVAL", "EVALUADOR"])
    except Exception:
        pass

    return User.objects.filter(is_active=True).filter(filtros).distinct().order_by(
        "first_name", "last_name", "email", "id"
    )


def _evaluadores_load_map(evento) -> dict[int, dict[str, int]]:
    rows = (
        EvaluacionAsignacion.objects.filter(proyecto__evento=evento)
        .values("evaluador_id")
        .annotate(total=Count("id"))
    )
    return {int(r["evaluador_id"]): {"total": int(r["total"] or 0)} for r in rows}


def _ponencia_responsable(ponencia) -> str:
    user = getattr(ponencia, "ponente", None)
    if user is not None:
        return _safe_user_display(user)
    return str(getattr(ponencia, "autor", "") or getattr(ponencia, "responsable", "") or "Sin responsable")


def _match_eval_record_for_ponencia(evento, ponencia):
    relacion = getattr(ponencia, "evaluacion_proyecto", None)
    if relacion is not None:
        try:
            if getattr(relacion, "evento_id", None) == evento.id:
                return relacion
        except Exception:
            pass

    titulo = str(getattr(ponencia, "titulo", "") or "").strip()
    responsable = _ponencia_responsable(ponencia)

    exact = EvaluacionProyecto.objects.filter(evento=evento, titulo=titulo, ponente=responsable).order_by("id").first()
    if exact:
        return exact
    return EvaluacionProyecto.objects.filter(evento=evento, titulo=titulo).order_by("id").first()


def _build_evaluable_rows(evento):
    rows = []
    matched_ids = set()

    ponencias_qs = _get_ponencias_evento_qs(evento)
    for ponencia in ponencias_qs:
        registro = _match_eval_record_for_ponencia(evento, ponencia)
        if registro:
            matched_ids.add(registro.id)
            asignaciones = list(registro.asignaciones.select_related("evaluador").all())
        else:
            asignaciones = []

        rows.append(
            {
                "source_type": "PONENCIA",
                "source_id": ponencia.id,
                "registro_id": getattr(registro, "id", ""),
                "tipo_label": "Ponencia",
                "titulo": getattr(ponencia, "titulo", "Ponencia"),
                "responsable": _ponencia_responsable(ponencia),
                "inicio": getattr(registro, "inicio", None),
                "fin": getattr(registro, "fin", None),
                "lugar": getattr(registro, "lugar", "") or "",
                "asignados_count": len(asignaciones),
                "assigned_ids_csv": ",".join(str(a.evaluador_id) for a in asignaciones),
                "assigned_names": [_safe_user_display(a.evaluador) for a in asignaciones],
                "estado": "Sin programación" if not registro else ("Sin asignar" if not asignaciones else "Asignada"),
            }
        )

    proyectos_qs = (
        EvaluacionProyecto.objects.filter(evento=evento)
        .prefetch_related("asignaciones__evaluador")
        .order_by("inicio", "fin", "id")
    )
    for proyecto in proyectos_qs:
        if proyecto.id in matched_ids:
            continue
        asignaciones = list(proyecto.asignaciones.select_related("evaluador").all())
        rows.append(
            {
                "source_type": "PROYECTO",
                "source_id": proyecto.id,
                "registro_id": proyecto.id,
                "tipo_label": "Proyecto",
                "titulo": proyecto.titulo,
                "responsable": proyecto.ponente or "Sin responsable",
                "inicio": proyecto.inicio,
                "fin": proyecto.fin,
                "lugar": proyecto.lugar or "",
                "asignados_count": len(asignaciones),
                "assigned_ids_csv": ",".join(str(a.evaluador_id) for a in asignaciones),
                "assigned_names": [_safe_user_display(a.evaluador) for a in asignaciones],
                "estado": "Sin asignar" if not asignaciones else "Asignada",
            }
        )

    rows.sort(key=lambda item: (
        0 if item["source_type"] == "PONENCIA" else 1,
        item["inicio"] is None,
        item["inicio"] or "",
        str(item["titulo"]).lower(),
        int(item["source_id"]),
    ))
    return rows


@login_required
def evaluadores(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    evaluadores_disponibles = list(_event_evaluadores_qs(evento))
    carga_map = _evaluadores_load_map(evento)
    evaluadores_cards = []
    for user in evaluadores_disponibles:
        evaluadores_cards.append(
            {
                "obj": user,
                "display_name": _safe_user_display(user),
                "email": getattr(user, "email", ""),
                "rol_base": _user_role_code(user) or "EVALUADOR",
                "carga_total": carga_map.get(int(user.id), {}).get("total", 0),
            }
        )

    items = _build_evaluable_rows(evento)
    pendientes = sum(1 for item in items if item["asignados_count"] == 0)
    ponencias_total = sum(1 for item in items if item["source_type"] == "PONENCIA")
    proyectos_total = sum(1 for item in items if item["source_type"] == "PROYECTO")

    return render(
        request,
        "coordinador/evaluadores/evaluadores.html",
        {
            "active": "evaluadores",
            "evento": evento,
            "items": items,
            "evaluadores_cards": evaluadores_cards,
            "resumen_evaluadores": {
                "total_evaluadores": len(evaluadores_cards),
                "total_registros": len(items),
                "pendientes": pendientes,
                "ponencias_total": ponencias_total,
                "proyectos_total": proyectos_total,
            },
            "proyectos_habilitados": proyectos_total > 0,
        },
    )


@login_required
@require_POST
@transaction.atomic
def eval_proyecto_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyecto_id = (request.POST.get("proyecto_id") or "").strip()
    if not proyecto_id:
        messages.error(request, "Registro inválido.")
        return redirect("coordinador:evaluadores")

    instance = get_object_or_404(EvaluacionProyecto, pk=proyecto_id, evento=evento)
    form = EvaluacionProyectoForm(request.POST, instance=instance)

    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la programación del registro.")
        return redirect("coordinador:evaluadores")

    try:
        obj = form.save(commit=False)
        obj.evento = evento
        obj.full_clean()
        obj.save()
        messages.success(request, "Programación actualizada correctamente.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))

    return redirect("coordinador:evaluadores")


@login_required
@require_POST
@transaction.atomic
def eval_proyecto_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyecto = get_object_or_404(EvaluacionProyecto, pk=pk, evento=evento)
    proyecto.delete()
    messages.success(request, "Registro de asignación eliminado correctamente.")
    return redirect("coordinador:evaluadores")


@login_required
@require_POST
@transaction.atomic
def eval_gestionar_guardar(request: HttpRequest) -> HttpResponse:
    """
    Asigna evaluadores a una ponencia o proyecto del evento activo.
    Regla: un evaluador puede tener varias asignaciones, pero nunca en horarios traslapados.
    """
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    source_type = (request.POST.get("source_type") or "PONENCIA").strip().upper()
    source_id = (request.POST.get("source_id") or request.POST.get("proyecto_id") or "").strip()
    if not source_id.isdigit():
        messages.error(request, "Registro evaluable inválido.")
        return redirect("coordinador:evaluadores")

    inicio = request.POST.get("inicio")
    fin = request.POST.get("fin")
    lugar = (request.POST.get("lugar") or "").strip()

    permitidos = {int(u.id) for u in _event_evaluadores_qs(evento)}
    seleccionados = []
    for raw in request.POST.getlist("evaluadores"):
        if str(raw).isdigit() and int(raw) in permitidos:
            seleccionados.append(int(raw))
    seleccionados = list(dict.fromkeys(seleccionados))

    proyecto = None
    titulo = ""
    responsable = ""

    if source_type == "PONENCIA":
        Ponencia = _get_ponencia_model()
        if Ponencia is None:
            messages.error(request, "No se encontró el modelo de ponencias.")
            return redirect("coordinador:evaluadores")
        ponencia = get_object_or_404(Ponencia, pk=int(source_id), evento=evento)
        proyecto = _match_eval_record_for_ponencia(evento, ponencia)
        titulo = str(getattr(ponencia, "titulo", "Ponencia")).strip() or "Ponencia"
        responsable = _ponencia_responsable(ponencia)
        if proyecto is None:
            proyecto = EvaluacionProyecto(evento=evento, titulo=titulo, ponente=responsable)
    else:
        proyecto = get_object_or_404(EvaluacionProyecto, pk=int(source_id), evento=evento)
        titulo = proyecto.titulo
        responsable = proyecto.ponente

    form = EvaluacionProyectoForm(
        {
            "titulo": titulo,
            "ponente": responsable,
            "inicio": inicio,
            "fin": fin,
            "lugar": lugar,
        },
        instance=proyecto,
    )
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Horario o lugar inválidos.")
        return redirect("coordinador:evaluadores")

    obj = form.save(commit=False)
    obj.evento = evento
    try:
        obj.full_clean()
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect("coordinador:evaluadores")

    conflictos = []
    for uid in seleccionados:
        conflict = (
            EvaluacionAsignacion.objects.select_related("proyecto", "evaluador")
            .filter(evaluador_id=uid, proyecto__evento=evento)
            .exclude(proyecto=obj)
            .filter(proyecto__inicio__lt=obj.fin, proyecto__fin__gt=obj.inicio)
            .first()
        )
        if conflict:
            conflictos.append(_safe_user_display(conflict.evaluador))

    if conflictos:
        nombres = ", ".join(dict.fromkeys(conflictos))
        messages.error(
            request,
            "No se guardó la asignación porque estos evaluadores ya tienen otro proyecto o ponencia en horario traslapado: " + nombres + ".",
        )
        return redirect("coordinador:evaluadores")

    obj.save()

    for uid in seleccionados:
        Inscripcion.objects.get_or_create(
            evento=evento,
            usuario_id=uid,
            defaults={"rol": Inscripcion.ROL_EVALUADOR},
        )

    EvaluacionAsignacion.objects.filter(proyecto=obj).exclude(evaluador_id__in=seleccionados).delete()
    actuales = set(EvaluacionAsignacion.objects.filter(proyecto=obj).values_list("evaluador_id", flat=True))
    for uid in seleccionados:
        if uid not in actuales:
            EvaluacionAsignacion.objects.create(proyecto=obj, evaluador_id=uid)

    if not seleccionados:
        messages.success(request, "Programación guardada sin evaluadores asignados todavía.")
    else:
        messages.success(request, "Asignación de evaluadores guardada correctamente.")
    return redirect("coordinador:evaluadores")


def _get_ponencia_model():
    try:
        return apps.get_model("ponente", "Ponencia")
    except Exception:
        return None


def _get_ponencias_evento_qs(evento):
    Ponencia = _get_ponencia_model()
    if Ponencia is None:
        return None
    try:
        return Ponencia.objects.filter(evento=evento).order_by("titulo", "id")
    except Exception:
        return Ponencia.objects.none()

# =========================================================
# RÚBRICAS
# =========================================================
def _rubrica_adjunto_payload(adjunto):
    return {
        "id": int(adjunto.id),
        "nombre": str(adjunto.nombre_original or os.path.basename(adjunto.archivo.name)),
        "url": f"/coordinador/rubricas/adjuntos/{adjunto.id}/descargar/",
    }


def _rubrica_payload(rubrica):
    return {
        "id": int(rubrica.id),
        "titulo": str(rubrica.titulo or ""),
        "estado": str(rubrica.estado or Rubrica.ESTADO_BORRADOR),
        "target_type": "PONENCIA" if rubrica.ponencia_id else "PROYECTO",
        "proyecto_id": int(rubrica.proyecto_id) if rubrica.proyecto_id else "",
        "ponencia_id": int(rubrica.ponencia_id) if rubrica.ponencia_id else "",
        "criterios": [
            {
                "titulo": str(c.titulo or ""),
                "descripcion": str(c.descripcion or ""),
                "puntaje": int(c.puntaje_max or 0),
            }
            for c in rubrica.criterios.all().order_by("orden", "id")
        ],
        "adjuntos": [_rubrica_adjunto_payload(a) for a in rubrica.adjuntos.all()],
    }


def _build_rubricas_context(evento, *, request=None, form_rubrica=None, open_modal=False, modal_prefill=None):
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)

    rubricas_qs = (
        Rubrica.objects.filter(evento=evento)
        .select_related("proyecto", "ponencia")
        .prefetch_related("criterios", "adjuntos")
        .order_by("-actualizado_en", "-id")
    )

    q = ((request.GET.get("q") if request else "") or "").strip()
    estado = ((request.GET.get("estado") if request else "") or "").strip().upper()
    tipo = ((request.GET.get("tipo") if request else "") or "").strip().upper()

    if q:
        rubricas_qs = rubricas_qs.filter(
            Q(titulo__icontains=q)
            | Q(proyecto__titulo__icontains=q)
            | Q(ponencia__titulo__icontains=q)
        )
    if estado in {Rubrica.ESTADO_BORRADOR, Rubrica.ESTADO_ACTIVA}:
        rubricas_qs = rubricas_qs.filter(estado=estado)
    if tipo == "PROYECTO":
        rubricas_qs = rubricas_qs.filter(ponencia__isnull=True, proyecto__isnull=False)
    elif tipo == "PONENCIA":
        rubricas_qs = rubricas_qs.filter(ponencia__isnull=False)

    total_rubricas = Rubrica.objects.filter(evento=evento).count()
    activas = Rubrica.objects.filter(evento=evento, estado=Rubrica.ESTADO_ACTIVA).count()
    borrador = Rubrica.objects.filter(evento=evento, estado=Rubrica.ESTADO_BORRADOR).count()
    rubricas_proyecto = Rubrica.objects.filter(evento=evento, ponencia__isnull=True, proyecto__isnull=False).count()
    rubricas_ponencia = Rubrica.objects.filter(evento=evento, ponencia__isnull=False).count()

    total_ponencias = ponencias_qs.count() if ponencias_qs is not None else 0
    pendientes_proyecto = max(proyectos.count() - rubricas_proyecto, 0)
    pendientes_ponencia = max(total_ponencias - rubricas_ponencia, 0)

    if form_rubrica is None:
        form_rubrica = RubricaForm(proyectos_qs=proyectos, ponencias_qs=ponencias_qs)

    return {
        "active": "rubricas",
        "evento": evento,
        "rubricas": rubricas_qs,
        "proyectos": proyectos,
        "ponencias": ponencias_qs,
        "form_rubrica": form_rubrica,
        "q": q,
        "estado": estado,
        "tipo": tipo,
        "open_modal": open_modal,
        "modal_prefill": modal_prefill or {},
        "kpis": {
            "total": total_rubricas,
            "activas": activas,
            "borrador": borrador,
            "proyecto": rubricas_proyecto,
            "ponencia": rubricas_ponencia,
            "pendientes_proyecto": pendientes_proyecto,
            "pendientes_ponencia": pendientes_ponencia,
            "total_ponencias": total_ponencias,
        },
    }


@login_required
def rubricas(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    context = _build_rubricas_context(evento, request=request)
    return render(request, "coordinador/rubricas/rubricas.html", context)


@login_required
@require_POST
@transaction.atomic
def rubrica_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubrica_id = (request.POST.get("rubrica_id") or "").strip()
    instance = get_object_or_404(Rubrica.objects.prefetch_related("adjuntos", "criterios"), pk=rubrica_id, evento=evento) if rubrica_id else None

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)
    form = RubricaForm(request.POST, instance=instance, proyectos_qs=proyectos, ponencias_qs=ponencias_qs)

    titulos = request.POST.getlist("criterio_titulo")
    descripciones = request.POST.getlist("criterio_desc")
    puntajes = request.POST.getlist("criterio_puntos")
    criterios_limpios = []
    for i in range(max(len(titulos), len(descripciones), len(puntajes))):
        t = (titulos[i] or "").strip() if i < len(titulos) else ""
        d = (descripciones[i] or "").strip() if i < len(descripciones) else ""
        p = (puntajes[i] or "").strip() if i < len(puntajes) else ""
        if not t and not d and not p:
            continue
        if not t:
            form.add_error(None, "Todos los criterios deben incluir un título.")
            break
        if not p.isdigit() or int(p) <= 0:
            form.add_error(None, f"El puntaje del criterio '{t}' debe ser un número mayor que 0.")
            break
        criterios_limpios.append({"titulo": t, "descripcion": d, "puntaje": int(p)})

    if not criterios_limpios:
        form.add_error(None, "Debes agregar al menos un criterio de evaluación.")

    if not form.is_valid():
        modal_prefill = {
            "id": int(instance.id) if instance else "",
            "titulo": request.POST.get("titulo", ""),
            "estado": request.POST.get("estado", Rubrica.ESTADO_BORRADOR),
            "target_type": request.POST.get("target_type", "PROYECTO"),
            "proyecto_id": request.POST.get("proyecto", ""),
            "ponencia_id": request.POST.get("ponencia", ""),
            "criterios": criterios_limpios,
            "adjuntos": [_rubrica_adjunto_payload(a) for a in instance.adjuntos.all()] if instance else [],
        }
        context = _build_rubricas_context(evento, request=request, form_rubrica=form, open_modal=True, modal_prefill=modal_prefill)
        return render(request, "coordinador/rubricas/rubricas.html", context, status=400)

    rubrica = form.save(commit=False)
    rubrica.evento = evento

    target_type = form.cleaned_data.get("target_type")
    if target_type == RubricaForm.TARGET_PROYECTO:
        rubrica.proyecto = form.cleaned_data.get("proyecto")
        rubrica.ponencia = None
    else:
        ponencia = form.cleaned_data.get("ponencia")
        rubrica.ponencia = ponencia
        bridge = _match_eval_record_for_ponencia(evento, ponencia) if ponencia is not None else None
        rubrica.proyecto = bridge

    try:
        rubrica.full_clean()
    except ValidationError as e:
        form.add_error(None, "; ".join(e.messages))
        modal_prefill = {
            "id": int(instance.id) if instance else "",
            "titulo": request.POST.get("titulo", ""),
            "estado": request.POST.get("estado", Rubrica.ESTADO_BORRADOR),
            "target_type": request.POST.get("target_type", "PROYECTO"),
            "proyecto_id": request.POST.get("proyecto", ""),
            "ponencia_id": request.POST.get("ponencia", ""),
            "criterios": criterios_limpios,
            "adjuntos": [_rubrica_adjunto_payload(a) for a in instance.adjuntos.all()] if instance else [],
        }
        context = _build_rubricas_context(evento, request=request, form_rubrica=form, open_modal=True, modal_prefill=modal_prefill)
        return render(request, "coordinador/rubricas/rubricas.html", context, status=400)

    rubrica.save()

    RubricaCriterio.objects.filter(rubrica=rubrica).delete()
    nuevos = []
    for idx, item in enumerate(criterios_limpios, start=1):
        c = RubricaCriterio(
            rubrica=rubrica,
            titulo=item["titulo"],
            descripcion=item["descripcion"],
            puntaje_max=item["puntaje"],
            orden=idx,
        )
        c.full_clean()
        nuevos.append(c)
    RubricaCriterio.objects.bulk_create(nuevos)

    max_mb = 15
    max_bytes = max_mb * 1024 * 1024
    for f in request.FILES.getlist("archivos"):
        size = int(getattr(f, "size", 0) or 0)
        if size > max_bytes:
            messages.error(request, f"El archivo '{getattr(f, 'name', 'archivo')}' excede {max_mb}MB.")
            return redirect("coordinador:rubricas")
        adj = RubricaAdjunto(rubrica=rubrica, archivo=f, nombre_original=getattr(f, "name", "")[:255])
        adj.full_clean()
        adj.save()

    if rubrica.ponencia_id and rubrica.proyecto_id:
        messages.success(request, "Rúbrica guardada correctamente y enlazada al puente evaluable de la ponencia.")
    else:
        messages.success(request, "Rúbrica guardada correctamente.")
    return redirect("coordinador:rubricas")


@login_required
def rubrica_adjunto_descargar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    adjunto = get_object_or_404(
        RubricaAdjunto.objects.select_related("rubrica"),
        pk=pk,
        rubrica__evento=evento,
    )
    if not adjunto.archivo:
        raise Http404("El adjunto solicitado no existe.")

    filename = adjunto.nombre_original or os.path.basename(adjunto.archivo.name)
    return FileResponse(adjunto.archivo.open("rb"), as_attachment=True, filename=filename)


@login_required
@require_POST
@transaction.atomic
def rubrica_adjunto_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    adjunto = get_object_or_404(
        RubricaAdjunto.objects.select_related("rubrica"),
        pk=pk,
        rubrica__evento=evento,
    )
    nombre = adjunto.nombre_original or os.path.basename(adjunto.archivo.name)
    adjunto.delete()
    messages.success(request, f"Adjunto eliminado: {nombre}")
    return redirect("coordinador:rubricas")


@login_required
@require_POST
@transaction.atomic
def rubrica_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubrica = get_object_or_404(Rubrica, pk=pk, evento=evento)
    rubrica.delete()
    messages.success(request, "Rúbrica eliminada correctamente.")
    return redirect("coordinador:rubricas")


def _build_space_rows(evento):
    rows = []
    espacios_qs = list(
        Espacio.objects.filter(evento=evento)
        .select_related("proyecto", "ponencia")
        .order_by("inicio", "fin", "nombre", "id")
    )
    space_by_project = {int(s.proyecto_id): s for s in espacios_qs if getattr(s, "proyecto_id", None)}
    space_by_ponencia = {int(s.ponencia_id): s for s in espacios_qs if getattr(s, "ponencia_id", None)}
    matched_project_ids = set()

    ponencias_qs = _get_ponencias_evento_qs(evento)
    for ponencia in ponencias_qs:
        registro = _match_eval_record_for_ponencia(evento, ponencia)
        if registro is not None:
            matched_project_ids.add(int(registro.id))
        espacio = space_by_ponencia.get(int(ponencia.id))
        inicio = getattr(espacio, "inicio", None) or getattr(registro, "inicio", None)
        fin = getattr(espacio, "fin", None) or getattr(registro, "fin", None)
        lugar = (getattr(espacio, "nombre", "") or getattr(registro, "lugar", "") or "").strip()
        rows.append(
            {
                "source_type": "PONENCIA",
                "source_id": int(ponencia.id),
                "space_id": getattr(espacio, "id", ""),
                "tipo_label": "Ponencia",
                "titulo": str(getattr(ponencia, "titulo", "Ponencia") or "Ponencia"),
                "responsable": _ponencia_responsable(ponencia),
                "nombre": getattr(espacio, "nombre", "") or "",
                "tipo": getattr(espacio, "tipo", "") or "",
                "capacidad": getattr(espacio, "capacidad", 0) or 0,
                "ubicacion": getattr(espacio, "ubicacion", "") or "",
                "estado": getattr(espacio, "estado", "") or (Espacio.ESTADO_OCUPADO if espacio else Espacio.ESTADO_DISPONIBLE),
                "inicio": inicio,
                "fin": fin,
                "duracion": max(((fin.hour * 60 + fin.minute) - (inicio.hour * 60 + inicio.minute)), 0) if inicio and fin else 0,
                "tags": getattr(espacio, "tags", "") or "",
                "asignada": bool(espacio),
            }
        )

    proyectos_qs = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    for proyecto in proyectos_qs:
        if int(proyecto.id) in matched_project_ids:
            continue
        espacio = space_by_project.get(int(proyecto.id))
        inicio = getattr(espacio, "inicio", None) or getattr(proyecto, "inicio", None)
        fin = getattr(espacio, "fin", None) or getattr(proyecto, "fin", None)
        lugar = (getattr(espacio, "nombre", "") or getattr(proyecto, "lugar", "") or "").strip()
        rows.append(
            {
                "source_type": "PROYECTO",
                "source_id": int(proyecto.id),
                "space_id": getattr(espacio, "id", ""),
                "tipo_label": "Proyecto",
                "titulo": proyecto.titulo,
                "responsable": proyecto.ponente or "Sin responsable",
                "nombre": getattr(espacio, "nombre", "") or "",
                "tipo": getattr(espacio, "tipo", "") or "",
                "capacidad": getattr(espacio, "capacidad", 0) or 0,
                "ubicacion": getattr(espacio, "ubicacion", "") or "",
                "estado": getattr(espacio, "estado", "") or (Espacio.ESTADO_OCUPADO if espacio else Espacio.ESTADO_DISPONIBLE),
                "inicio": inicio,
                "fin": fin,
                "duracion": max(((fin.hour * 60 + fin.minute) - (inicio.hour * 60 + inicio.minute)), 0) if inicio and fin else 0,
                "tags": getattr(espacio, "tags", "") or "",
                "asignada": bool(espacio),
            }
        )

    rows.sort(key=lambda item: (
        0 if item["source_type"] == "PONENCIA" else 1,
        item["inicio"] is None,
        item["inicio"] or "",
        str(item["titulo"]).lower(),
        int(item["source_id"]),
    ))
    return rows


def _get_or_create_eval_record_for_space(evento, *, proyecto=None, ponencia=None):
    if proyecto is not None:
        return proyecto

    registro = _match_eval_record_for_ponencia(evento, ponencia)
    if registro is not None:
        return registro

    titulo = str(getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip() or "Ponencia"
    responsable = _ponencia_responsable(ponencia)
    registro = EvaluacionProyecto(evento=evento, titulo=titulo, ponente=responsable)
    return registro


def _try_link_ponencia_eval_record(ponencia, registro):
    try:
        if hasattr(ponencia, "evaluacion_proyecto_id"):
            setattr(ponencia, "evaluacion_proyecto", registro)
            ponencia.save(update_fields=["evaluacion_proyecto"])
    except Exception:
        return None
    return None


def _assigned_evaluadores_conflicts(evento, registro, inicio, fin):
    conflictos = []
    if not inicio or not fin or not getattr(registro, "pk", None):
        return conflictos

    asignaciones = EvaluacionAsignacion.objects.filter(proyecto=registro).select_related("evaluador")
    for asignacion in asignaciones:
        conflict = (
            EvaluacionAsignacion.objects.select_related("proyecto", "evaluador")
            .filter(evaluador=asignacion.evaluador, proyecto__evento=evento)
            .exclude(proyecto=registro)
            .filter(proyecto__inicio__lt=fin, proyecto__fin__gt=inicio)
            .first()
        )
        if conflict:
            conflictos.append(_safe_user_display(asignacion.evaluador))
    return list(dict.fromkeys(conflictos))


def _sync_space_to_eval_record(evento, espacio):
    proyecto = getattr(espacio, "proyecto", None)
    ponencia = getattr(espacio, "ponencia", None)
    registro = _get_or_create_eval_record_for_space(evento, proyecto=proyecto, ponencia=ponencia)
    registro.inicio = espacio.inicio
    registro.fin = espacio.fin
    registro.lugar = (espacio.nombre or "").strip()
    registro.evento = evento
    if ponencia is not None:
        registro.titulo = str(getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip() or registro.titulo
        registro.ponente = _ponencia_responsable(ponencia)

    conflictos = _assigned_evaluadores_conflicts(evento, registro, espacio.inicio, espacio.fin)
    if conflictos:
        nombres = ", ".join(conflictos)
        raise ValidationError(
            "No se puede cambiar el espacio/horario porque estos evaluadores ya tienen otra asignación en ese tramo: " + nombres + "."
        )

    registro.full_clean()
    registro.save()
    if ponencia is not None:
        _try_link_ponencia_eval_record(ponencia, registro)
    return registro


# =========================================================
# ESPACIOS
# =========================================================
@login_required
def espacios(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    q = (request.GET.get("q") or "").strip()

    espacios_qs = (
        Espacio.objects.filter(evento=evento)
        .select_related("proyecto", "ponencia")
        .order_by("inicio", "fin", "nombre", "id")
    )
    if q:
        espacios_qs = espacios_qs.filter(
            Q(nombre__icontains=q)
            | Q(ubicacion__icontains=q)
            | Q(tags__icontains=q)
            | Q(proyecto__titulo__icontains=q)
            | Q(ponencia__titulo__icontains=q)
        )

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)
    rows = _build_space_rows(evento)
    if q:
        ql = q.lower()
        rows = [
            row for row in rows
            if ql in str(row.get("titulo", "")).lower()
            or ql in str(row.get("responsable", "")).lower()
            or ql in str(row.get("nombre", "")).lower()
            or ql in str(row.get("ubicacion", "")).lower()
        ]

    resumen = {
        "total_asignaciones": espacios_qs.count(),
        "areas_unicas": espacios_qs.values("nombre").distinct().count(),
        "pendientes": sum(1 for row in rows if not row.get("space_id")),
        "asignadas": sum(1 for row in rows if row.get("space_id")),
        "ponencias_total": sum(1 for row in rows if row.get("source_type") == "PONENCIA"),
        "proyectos_total": sum(1 for row in rows if row.get("source_type") == "PROYECTO"),
    }

    return render(
        request,
        "coordinador/espacios/espacios.html",
        {
            "active": "espacios",
            "evento": evento,
            "espacios": espacios_qs,
            "rows": rows,
            "resumen_espacios": resumen,
            "proyectos": proyectos,
            "ponencias": ponencias_qs,
            "form_espacio": EspacioForm(proyectos_qs=proyectos, ponencias_qs=ponencias_qs),
            "q": q,
        },
    )


@login_required
@require_POST
@transaction.atomic
def espacio_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    espacio_id = (request.POST.get("espacio_id") or "").strip()
    instance = get_object_or_404(Espacio, pk=espacio_id, evento=evento) if espacio_id else None

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)
    form = EspacioForm(request.POST, instance=instance, proyectos_qs=proyectos, ponencias_qs=ponencias_qs)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica los datos del espacio.")
        return redirect("coordinador:espacios")

    obj = form.save(commit=False)
    obj.evento = evento
    obj.proyecto = form.cleaned_data.get("proyecto")
    obj.ponencia = form.cleaned_data.get("ponencia")
    obj.inicio = form.cleaned_data.get("inicio")
    obj.fin = form.cleaned_data.get("fin_calculado")
    obj.estado = form.cleaned_data.get("estado") or Espacio.ESTADO_OCUPADO

    try:
        obj.full_clean()
        _sync_space_to_eval_record(evento, obj)
        obj.save()
        messages.success(request, "Asignación de espacio guardada correctamente.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))

    return redirect("coordinador:espacios")


@login_required
@require_POST
@transaction.atomic
def espacio_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    espacio = get_object_or_404(Espacio, pk=pk, evento=evento)
    registro = getattr(espacio, "proyecto", None)
    if getattr(espacio, "ponencia", None) is not None:
        registro = _match_eval_record_for_ponencia(evento, espacio.ponencia)

    nombre = espacio.nombre
    inicio = espacio.inicio
    fin = espacio.fin
    espacio.delete()

    try:
        if registro is not None and getattr(registro, "lugar", "") == nombre and getattr(registro, "inicio", None) == inicio and getattr(registro, "fin", None) == fin:
            registro.lugar = ""
            registro.save(update_fields=["lugar", "actualizado_en"])
    except Exception:
        pass

    messages.success(request, "Asignación de espacio eliminada.")
    return redirect("coordinador:espacios")


# =========================================================
# REPORTES
# =========================================================
REPORTES_LABELS = {
    Reporte.CATEG_INSCRIPCIONES: "Inscripciones",
    Reporte.CATEG_EVALUACIONES: "Evaluaciones",
    Reporte.CATEG_ASISTENCIA: "Asistencia",
    Reporte.CATEG_GENERAL: "General",
}


@login_required
def reportes(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    categoria = (request.GET.get("categoria") or Reporte.CATEG_TODOS).strip().upper()
    q = (request.GET.get("q") or "").strip()

    qs = (
        Reporte.objects.filter(evento=evento)
        .select_related("proyecto", "creado_por")
        .order_by("-generado_en", "-id")
    )

    categorias_validas = {c for c, _ in Reporte.CATEGORIAS}
    if categoria in categorias_validas and categoria != Reporte.CATEG_TODOS:
        qs = qs.filter(categoria=categoria)

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q)
            | Q(nombre_original__icontains=q)
            | Q(proyecto__titulo__icontains=q)
            | Q(categoria__icontains=q)
            | Q(formato__icontains=q)
        )

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form_generar = ReporteGenerarForm(request.POST or None, proyectos_qs=proyectos)

    reportes_counts = {
        item["categoria"]: int(item["total"] or 0)
        for item in Reporte.objects.filter(evento=evento)
        .values("categoria")
        .annotate(total=Count("id"))
    }

    resumen_inscripciones = _inscripciones_kpis(evento)
    resumen_evaluaciones = _evaluaciones_kpis(evento)
    resumen_asistencia = _asistencia_kpis(evento)
    resumen_general = _general_kpis(evento, resumen_inscripciones, resumen_evaluaciones)

    preview_categoria = categoria if categoria != Reporte.CATEG_TODOS else Reporte.CATEG_GENERAL
    preview_headers, preview_rows = _report_preview_for_categoria(evento, preview_categoria)

    categoria_cards = [
        {
            "code": Reporte.CATEG_INSCRIPCIONES,
            "title": "Inscripciones",
            "description": "Altas registradas al evento, separadas por rol y estatus.",
            "reports_total": reportes_counts.get(Reporte.CATEG_INSCRIPCIONES, 0),
            "primary": resumen_inscripciones["total"],
            "primary_label": "Total registradas",
            "secondary": f"Activas: {resumen_inscripciones['activas']} | Evaluadores: {resumen_inscripciones['evaluadores']}",
        },
        {
            "code": Reporte.CATEG_EVALUACIONES,
            "title": "Evaluaciones",
            "description": "Programación, asignaciones y cobertura de ponencias/proyectos.",
            "reports_total": reportes_counts.get(Reporte.CATEG_EVALUACIONES, 0),
            "primary": resumen_evaluaciones["programadas"],
            "primary_label": "Programadas",
            "secondary": f"Pendientes: {resumen_evaluaciones['pendientes']} | Asignaciones: {resumen_evaluaciones['asignaciones']}",
        },
        {
            "code": Reporte.CATEG_ASISTENCIA,
            "title": "Asistencia",
            "description": "Control de asistencia del evento. Queda preparado aunque el módulo aún no exista.",
            "reports_total": reportes_counts.get(Reporte.CATEG_ASISTENCIA, 0),
            "primary": resumen_asistencia["registros"],
            "primary_label": "Registros",
            "secondary": resumen_asistencia["estado_label"],
        },
        {
            "code": Reporte.CATEG_GENERAL,
            "title": "General",
            "description": "Consolidado ejecutivo del evento con métricas globales.",
            "reports_total": reportes_counts.get(Reporte.CATEG_GENERAL, 0),
            "primary": resumen_general["total_reportes"],
            "primary_label": "Informes generados",
            "secondary": f"Inscripciones: {resumen_general['inscripciones']} | Registros evaluables: {resumen_general['registros_evaluables']}",
        },
    ]

    return render(
        request,
        "coordinador/reportes/reportes.html",
        {
            "active": "reportes",
            "evento": evento,
            "reportes": qs,
            "form_generar": form_generar,
            "categoria": categoria,
            "q": q,
            "categoria_cards": categoria_cards,
            "resumen_inscripciones": resumen_inscripciones,
            "resumen_evaluaciones": resumen_evaluaciones,
            "resumen_asistencia": resumen_asistencia,
            "resumen_general": resumen_general,
            "preview_categoria": preview_categoria,
            "preview_headers": preview_headers,
            "preview_rows": preview_rows,
        },
    )


def _dataset_inscripciones(evento):
    headers = ["Nombre completo", "Correo", "Rol", "Activo"]
    rows = []
    qs = (
        Inscripcion.objects.select_related("usuario")
        .filter(evento=evento)
        .order_by("rol", "usuario__first_name", "usuario__last_name", "usuario__email")
    )
    for ins in qs:
        u = ins.usuario
        nombre = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or getattr(u, 'email', '') or f"Usuario #{u.pk}"
        rows.append([
            nombre,
            getattr(u, "email", "") or "",
            ins.get_rol_display() if hasattr(ins, "get_rol_display") else (ins.rol or ""),
            "Sí" if getattr(u, "is_active", False) else "No",
        ])
    return headers, rows


def _dataset_evaluaciones(evento, proyecto=None):
    headers = ["Tipo", "Título", "Responsable", "Inicio", "Fin", "Lugar", "Evaluadores asignados", "Estado"]
    rows = []
    items = _build_evaluable_rows(evento)
    if proyecto is not None:
        pid = int(proyecto.id)
        items = [
            item for item in items
            if int(item.get("registro_id") or 0) == pid or (item["source_type"] == "PROYECTO" and int(item["source_id"]) == pid)
        ]

    for item in items:
        rows.append([
            item["tipo_label"],
            str(item["titulo"] or ""),
            str(item["responsable"] or ""),
            str(item["inicio"] or ""),
            str(item["fin"] or ""),
            str(item["lugar"] or ""),
            ", ".join(item["assigned_names"]) if item["assigned_names"] else "SIN ASIGNACIÓN",
            item["estado"],
        ])
    return headers, rows


def _dataset_asistencia(evento):
    headers = ["Indicador", "Valor"]
    rows = [
        ["Estado del módulo", "Pendiente de implementación"],
        ["Evento", getattr(evento, "titulo", f"Evento {evento.id}")],
        ["Observación", "La categoría ya está disponible para descarga, pero aún no existe una fuente de control de asistencia integrada."],
    ]
    return headers, rows


def _dataset_general(evento):
    evaluables = _build_evaluable_rows(evento)
    headers = ["Métrica", "Valor"]
    rows = [
        ["Inscripciones", str(Inscripcion.objects.filter(evento=evento).count())],
        ["Registros evaluables", str(len(evaluables))],
        ["Ponencias registradas", str(sum(1 for item in evaluables if item["source_type"] == "PONENCIA"))],
        ["Proyectos registrados", str(sum(1 for item in evaluables if item["source_type"] == "PROYECTO"))],
        ["Evaluadores disponibles", str(_event_evaluadores_qs(evento).count())],
        ["Asignaciones de evaluación", str(EvaluacionAsignacion.objects.filter(proyecto__evento=evento).count())],
        ["Rúbricas", str(Rubrica.objects.filter(evento=evento).count())],
        ["Espacios configurados", str(Espacio.objects.filter(evento=evento).count())],
        ["Reportes generados", str(Reporte.objects.filter(evento=evento).count())],
    ]
    return headers, rows


def _inscripciones_kpis(evento):
    qs = Inscripcion.objects.filter(evento=evento).select_related("usuario")
    return {
        "total": qs.count(),
        "activas": qs.filter(usuario__is_active=True).count(),
        "inactivas": qs.filter(usuario__is_active=False).count(),
        "evaluadores": qs.filter(rol=Inscripcion.ROL_EVALUADOR).count(),
        "ponentes": qs.filter(rol=Inscripcion.ROL_PONENTE).count(),
        "participantes": qs.filter(rol=Inscripcion.ROL_PARTICIPANTE).count(),
        "coordinadores": qs.filter(rol=Inscripcion.ROL_COORDINADOR).count(),
    }


def _evaluaciones_kpis(evento):
    items = _build_evaluable_rows(evento)
    total = len(items)
    programadas = sum(1 for item in items if item["inicio"] and item["fin"])
    con_evaluadores = sum(1 for item in items if item["asignados_count"] > 0)
    return {
        "total": total,
        "programadas": programadas,
        "sin_programar": max(total - programadas, 0),
        "con_evaluadores": con_evaluadores,
        "pendientes": max(total - con_evaluadores, 0),
        "asignaciones": EvaluacionAsignacion.objects.filter(proyecto__evento=evento).count(),
        "ponencias": sum(1 for item in items if item["source_type"] == "PONENCIA"),
        "proyectos": sum(1 for item in items if item["source_type"] == "PROYECTO"),
    }


def _asistencia_kpis(evento):
    return {
        "registros": 0,
        "porcentaje": "N/D",
        "estado_label": "Pendiente de integración",
    }


def _general_kpis(evento, resumen_inscripciones=None, resumen_evaluaciones=None):
    resumen_inscripciones = resumen_inscripciones or _inscripciones_kpis(evento)
    resumen_evaluaciones = resumen_evaluaciones or _evaluaciones_kpis(evento)
    return {
        "inscripciones": resumen_inscripciones["total"],
        "registros_evaluables": resumen_evaluaciones["total"],
        "evaluadores": _event_evaluadores_qs(evento).count(),
        "rubricas": Rubrica.objects.filter(evento=evento).count(),
        "espacios": Espacio.objects.filter(evento=evento).count(),
        "total_reportes": Reporte.objects.filter(evento=evento).count(),
    }


def _report_preview_for_categoria(evento, categoria: str):
    categoria = (categoria or Reporte.CATEG_GENERAL).strip().upper()
    if categoria == Reporte.CATEG_INSCRIPCIONES:
        headers, rows = _dataset_inscripciones(evento)
    elif categoria == Reporte.CATEG_EVALUACIONES:
        headers, rows = _dataset_evaluaciones(evento)
    elif categoria == Reporte.CATEG_ASISTENCIA:
        headers, rows = _dataset_asistencia(evento)
    else:
        headers, rows = _dataset_general(evento)
    return headers, rows[:12]


@login_required
@require_POST
@transaction.atomic
def reporte_generar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = ReporteGenerarForm(request.POST, proyectos_qs=proyectos)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica los datos para generar el reporte.")
        return redirect("coordinador:reportes")

    categoria = form.cleaned_data["categoria"]
    formato = form.cleaned_data["formato"]
    modo = form.cleaned_data["modo"]
    proyecto = form.cleaned_data.get("proyecto")
    nombre = (form.cleaned_data.get("nombre") or "").strip()

    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    if not nombre:
        base = f"Reporte_{categoria}"
        if proyecto:
            base += f"_{proyecto.id}"
        nombre = f"{base}_{ts}"

    if categoria == Reporte.CATEG_INSCRIPCIONES:
        headers, rows = _dataset_inscripciones(evento)
        sheet = "Inscripciones"
    elif categoria == Reporte.CATEG_EVALUACIONES:
        headers, rows = _dataset_evaluaciones(evento, proyecto=proyecto)
        sheet = "Evaluaciones"
    elif categoria == Reporte.CATEG_ASISTENCIA:
        headers, rows = _dataset_asistencia(evento)
        sheet = "Asistencia"
    else:
        headers, rows = _dataset_general(evento)
        sheet = "General"

    rep = Reporte(
        evento=evento,
        proyecto=proyecto,
        creado_por=request.user,
        nombre=nombre,
        categoria=categoria,
        formato=formato,
        modo=modo,
        estado=Reporte.ESTADO_LISTO,
        generado_en=timezone.now(),
    )
    rep.full_clean()
    rep.save()

    try:
        if formato == Reporte.FORMATO_XLSX:
            content = _xlsx_from_rows(sheet, headers, rows)
            rep.archivo.save(f"{nombre}.xlsx", ContentFile(content), save=True)
        else:
            title = f"{nombre} | Evento {getattr(evento, 'titulo', evento.id)}"
            lines = [
                title,
                f"Categoría: {REPORTES_LABELS.get(categoria, categoria)} | Modo: {modo} | Generado: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                " | ".join(headers),
                "-" * 100,
            ]
            for r in rows[:250]:
                lines.append(" | ".join([str(x) for x in r]))
            content = _build_minimal_pdf(lines)
            rep.archivo.save(f"{nombre}.pdf", ContentFile(content), save=True)

        messages.success(request, "Reporte generado correctamente.")
    except ImportError:
        messages.error(request, "Falta openpyxl para Excel. Instala: pip install openpyxl")
    except Exception as e:
        messages.error(request, f"No se pudo generar el reporte: {e}")

    return redirect("coordinador:reportes")


@login_required
def reporte_descargar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    if not rep.archivo:
        raise Http404("El reporte no tiene archivo asociado.")

    file_path = rep.archivo.path
    if not os.path.exists(file_path):
        raise Http404("Archivo no encontrado.")

    return FileResponse(open(file_path, "rb"), as_attachment=True, filename=os.path.basename(file_path))


@login_required
@require_POST
@transaction.atomic
def reporte_editar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    form = ReporteEditarForm(request.POST, instance=rep)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica los datos del reporte.")
        return redirect("coordinador:reportes")

    obj = form.save(commit=False)
    obj.full_clean()
    obj.save()
    messages.success(request, "Reporte actualizado.")
    return redirect("coordinador:reportes")


@login_required
@require_POST
@transaction.atomic
def reporte_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    try:
        if rep.archivo:
            rep.archivo.delete(save=False)
    except Exception:
        pass
    rep.delete()
    messages.success(request, "Reporte eliminado.")
    return redirect("coordinador:reportes")


# =========================================================
# CONFIGURACIÓN (✅ COMPLETA Y FUNCIONAL)
# =========================================================
@login_required
def configuracion(request: HttpRequest) -> HttpResponse:
    """
    Configuración de perfil/cuenta (segura):
    - Solo usuario autenticado y activo
    - Cambios con POST + CSRF
    - Cambio de password requiere password actual
    - Archivos con validación (en modelo PerfilUsuario)
    - Mantiene sesión si cambia password
    """
    if not request.user.is_active:
        messages.error(request, "Tu cuenta está inactiva. No puedes editar tu perfil.")
        return redirect("coordinador:dashboard")

    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        cuenta_form = ConfigCuentaForm(request.POST, user=request.user)
        perfil_form = ConfigPerfilForm(request.POST, request.FILES, instance=perfil)

        if cuenta_form.is_valid() and perfil_form.is_valid():
            with transaction.atomic():
                request.user.first_name = cuenta_form.cleaned_data["nombres"].strip()
                request.user.last_name = cuenta_form.cleaned_data["apellidos"].strip()
                request.user.email = cuenta_form.cleaned_data["correo"].strip().lower()
                if hasattr(request.user, "username"):
                    request.user.username = request.user.email

                nueva = (cuenta_form.cleaned_data.get("password_nueva") or "").strip()
                if nueva:
                    request.user.set_password(nueva)

                request.user.save()
                perfil_form.save()

                if nueva:
                    update_session_auth_hash(request, request.user)

            messages.success(request, "Configuración actualizada correctamente.")
            return redirect("coordinador:configuracion")

        messages.error(request, "Verifica los campos marcados.")
    else:
        cuenta_form = ConfigCuentaForm(
            user=request.user,
            initial={
                "nombres": request.user.first_name or "",
                "apellidos": request.user.last_name or "",
                "correo": request.user.email or "",
            },
        )
        perfil_form = ConfigPerfilForm(instance=perfil)

    evento = _get_evento_actual(request)
    return render(
        request,
        "coordinador/configuracion/configuracion.html",
        {
            "active": "configuracion",
            "evento": evento,
            "cuenta_form": cuenta_form,
            "perfil_form": perfil_form,
            "perfil": perfil,
        },
    )