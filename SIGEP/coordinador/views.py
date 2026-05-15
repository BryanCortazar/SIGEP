from __future__ import annotations

def _time_sort_default():
    return datetime.strptime("08:00", "%H:%M").time()


import csv
import io
import os
from datetime import date, datetime, timedelta

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
    ConfigCuentaForm,
    ConfigPerfilForm,
    EspacioForm,
    EvaluacionProyectoForm,
    EventoGestionForm,
    InscripcionUsuarioForm,
    ReporteEditarForm,
    ReporteGenerarForm,
    RubricaForm,
)
from .models import (
    ActividadCronograma,
    Espacio,
    EvaluacionAsignacion,
    EvaluacionProyecto,
    Inscripcion,
    PerfilUsuario,
    Reporte,
    Rubrica,
    RubricaAdjunto,
    RubricaCriterio,
)

User = get_user_model()

try:
    from administrador.utils_auditoria import registrar_auditoria
except Exception:  # pragma: no cover
    def registrar_auditoria(**kwargs):
        return None


# =========================================================
# Helpers generales
# =========================================================
def _get_evento_model():
    for app_label, model_name in [
        ("coordinador", "Evento"),
        ("administrador", "Evento"),
        ("eventos", "Evento"),
    ]:
        try:
            return apps.get_model(app_label, model_name)
        except Exception:
            continue
    return None


def _get_evento_id_from_session(request: HttpRequest) -> int | None:
    eid = request.session.get("evento_id") or request.session.get("evento_actual_id")
    try:
        return int(eid) if eid else None
    except Exception:
        return None


def _get_evento_actual(request: HttpRequest):
    Evento = _get_evento_model()
    if Evento is None:
        return None
    evento_id = _get_evento_id_from_session(request)
    if not evento_id:
        return None
    return Evento.objects.filter(id=evento_id).first()


def _user_can_manage_evento(user, evento) -> bool:
    if not user or not getattr(user, "is_authenticated", False) or not evento:
        return False

    if Inscripcion.objects.filter(
        evento=evento,
        usuario=user,
        rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
    ).exists():
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


def _flatten_form_errors(form) -> str:
    errores = []
    for field, field_errors in form.errors.items():
        label = form.fields.get(field).label if field in getattr(form, "fields", {}) else None
        nombre = label or ("Formulario" if field == "__all__" else field.replace("_", " ").capitalize())
        for err in field_errors:
            errores.append(f"{nombre}: {err}")
    return " | ".join(errores)


def _cronograma_form_from_request(request: HttpRequest, *, instance=None) -> ActividadCronogramaForm:
    data = {
        "titulo": _first_post_value(request, "titulo", "actividad", "nombre", "nombre_actividad"),
        "inicio": _first_post_value(request, "inicio", "hora_inicio"),
        "fin": _first_post_value(request, "fin", "hora_fin"),
        "responsable": _first_post_value(request, "responsable", "encargado"),
    }
    return ActividadCronogramaForm(data, instance=instance)


def _safe_get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _safe_user_display(user) -> str:
    if not user:
        return "Sin usuario"
    nombre = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return nombre or getattr(user, "username", "Usuario") or getattr(user, "email", "Usuario")


# =========================================================
# Helpers PDF / XLSX mínimos
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
    for row in rows:
        ws.append(row)

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# =========================================================
# Dashboard
# =========================================================
def _coordinador_eventos_queryset(user):
    Evento = _get_evento_model()
    if Evento is None or not getattr(user, "is_authenticated", False):
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
    eventos_ids = list(eventos_qs.values_list("id", flat=True)) if hasattr(eventos_qs, "values_list") else []
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
    else:
        inscripciones_total = proyectos_total = rubricas_total = rubricas_activas = 0
        rubricas_borrador = espacios_total = reportes_total = asignaciones_total = 0

    ponencias_total = 0
    ponencias_revision = 0
    ponencias_aceptadas = 0
    if Ponencia and scope_ids:
        try:
            pon_qs = Ponencia.objects.filter(evento_id__in=scope_ids)
            ponencias_total = pon_qs.count()
            if hasattr(Ponencia, "ESTADO_EN_REVISION"):
                ponencias_revision = pon_qs.filter(estado=Ponencia.ESTADO_EN_REVISION).count()
            if hasattr(Ponencia, "ESTADO_ACEPTADA"):
                ponencias_aceptadas = pon_qs.filter(estado=Ponencia.ESTADO_ACEPTADA).count()
        except Exception:
            pass

    user_logs = None
    logs_hoy = 0
    logs_7d = 0
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
        {"label": "Eventos bajo gestión", "value": total_eventos, "help": "Eventos que puedes coordinar actualmente", "icon": "event_note"},
        {"label": "Eventos publicados", "value": eventos_publicados, "help": "Disponibles para registro operativo", "icon": "campaign"},
        {"label": "Registros operativos", "value": inscripciones_total + proyectos_total + ponencias_total, "help": "Inscripciones, proyectos y ponencias del alcance actual", "icon": "analytics"},
        {"label": "Movimientos 7 días", "value": logs_7d, "help": "Actividad reciente registrada para tu cuenta", "icon": "history"},
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
        "eventos_estado": {"title": "Estado de eventos", "labels": ["Borrador", "Publicado", "Cerrado"], "values": [eventos_borrador, eventos_publicados, eventos_cerrados]},
        "registros_alcance": {"title": "Registros del alcance actual", "labels": ["Inscripciones", "Proyectos", "Ponencias"], "values": [inscripciones_total, proyectos_total, ponencias_total]},
        "logistica": {"title": "Rúbricas y logística", "labels": ["Rúbricas activas", "Rúbricas borrador", "Espacios", "Asignaciones"], "values": [rubricas_activas, rubricas_borrador, espacios_total, asignaciones_total]},
        "actividad_7d": {"title": "Actividad últimos 7 días", "labels": dias_labels, "values": dias_values},
    }

    evento_actual_stats = None
    if evento:
        evento_actual_stats = {
            "titulo": getattr(evento, "titulo", f"Evento {evento.id}"),
            "estado": getattr(evento, "estado", "BORRADOR"),
            "fecha": getattr(evento, "fecha", None),
            "lugar": getattr(evento, "lugar", ""),
            "proyectos": EvaluacionProyecto.objects.filter(evento=evento).count(),
            "ponencias": Ponencia.objects.filter(evento=evento).count() if Ponencia else 0,
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
    context = {"active": "dashboard", **_build_dashboard_context(request, evento)}
    return render(request, "coordinador/dashboard/index.html", context)


# =========================================================
# Eventos
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
        eventos_qs = eventos_qs.filter(Q(titulo__icontains=q) | Q(descripcion__icontains=q) | Q(lugar__icontains=q))
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
        event_cards.append({"obj": ev, "stats": _evento_related_counts(ev), "is_selected": bool(selected_evento and ev.id == selected_evento.id)})

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

    return {
        "active": "eventos",
        "eventos": eventos_qs,
        "event_cards": event_cards,
        "selected_evento": selected_evento,
        "selected_stats": _evento_related_counts(selected_evento) if selected_evento else None,
        "evento_form": form,
        "filter_q": q,
        "filter_estado": estado,
        "estado_choices": [("", "Todos"), ("BORRADOR", "Borrador"), ("PUBLICADO", "Publicado"), ("CERRADO", "Cerrado")],
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
    if Evento is None:
        messages.error(request, "No se encontró el modelo Evento.")
        return redirect("coordinador:eventos")

    form = EventoGestionForm(request.POST)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la información del evento.")
        return render(request, "coordinador/eventos/eventos.html", _eventos_base_context(request, form=form))

    evento_id = form.cleaned_data.get("evento_id")
    is_new = not bool(evento_id)
    evento = get_object_or_404(_coordinador_eventos_queryset(request.user), pk=evento_id) if evento_id else Evento()

    exists_qs = Evento.objects.filter(titulo__iexact=form.cleaned_data["titulo"], fecha=form.cleaned_data["fecha"])
    if evento_id:
        exists_qs = exists_qs.exclude(pk=evento_id)
    if exists_qs.exists():
        messages.error(request, "Ya existe un evento con el mismo título y fecha.")
        return render(request, "coordinador/eventos/eventos.html", _eventos_base_context(request, selected_evento=evento if evento_id else None, form=form))

    evento = _save_evento_instance(evento=evento, cleaned_data=form.cleaned_data, user=request.user, is_new=is_new)
    if is_new:
        Inscripcion.objects.get_or_create(evento=evento, usuario=request.user, defaults={"rol": Inscripcion.ROL_COORDINADOR})
        request.session["evento_id"] = evento.id
        request.session.modified = True
        registrar_auditoria(request=request, accion="COORDINADOR | CREAR | EVENTO", modulo="COORDINADOR", accion_tipo="CREAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
        messages.success(request, "Evento creado correctamente.")
    else:
        registrar_auditoria(request=request, accion="COORDINADOR | EDITAR | EVENTO", modulo="COORDINADOR", accion_tipo="EDITAR", entidad="Evento", objeto_id=evento.id, detalles={"titulo": evento.titulo})
        messages.success(request, "Evento actualizado correctamente.")

    return redirect("coordinador:dashboard" if (request.POST.get("next") or "").strip() == "dashboard" else "coordinador:eventos")


@login_required
@require_POST
@transaction.atomic
def evento_crear(request: HttpRequest) -> HttpResponse:
    data = request.POST.copy()
    data.setdefault("estado", "BORRADOR")
    form = EventoGestionForm(data)
    Evento = _get_evento_model()
    if Evento is None:
        messages.error(request, "No se encontró el modelo Evento.")
        return redirect("coordinador:dashboard")
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la información del evento.")
        return redirect("coordinador:dashboard")
    if Evento.objects.filter(titulo__iexact=form.cleaned_data["titulo"], fecha=form.cleaned_data["fecha"]).exists():
        messages.error(request, "Ya existe un evento con el mismo título y fecha.")
        return redirect("coordinador:dashboard")

    evento = _save_evento_instance(evento=Evento(), cleaned_data=form.cleaned_data, user=request.user, is_new=True)
    Inscripcion.objects.get_or_create(evento=evento, usuario=request.user, defaults={"rol": Inscripcion.ROL_COORDINADOR})
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
    if Evento is None:
        messages.error(request, "No se encontró el modelo Evento.")
        return redirect("coordinador:dashboard")
    evento_id = (request.POST.get("evento_id") or "").strip()
    if not evento_id.isdigit():
        messages.error(request, "Selecciona un evento válido.")
        return redirect("coordinador:dashboard")
    evento = Evento.objects.filter(id=int(evento_id)).first()
    if evento is None or not _user_can_manage_evento(request.user, evento):
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
    if getattr(evento, "estado", None) == destino:
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

    if getattr(evento, "estado", "BORRADOR") == "PUBLICADO":
        messages.error(request, "No se puede eliminar un evento publicado. Cámbialo a borrador o cerrado y vuelve a intentarlo.")
        return redirect("coordinador:eventos")

    counts = _evento_related_counts(evento)
    titulo = evento.titulo
    detalles = {
        "titulo": titulo,
        "estado": getattr(evento, "estado", "BORRADOR"),
        "relaciones": counts,
    }

    if _get_evento_id_from_session(request) == evento.id:
        request.session.pop("evento_id", None)
        request.session.pop("evento_actual_id", None)
        request.session.modified = True

    evento.delete()
    registrar_auditoria(
        request=request,
        accion="COORDINADOR | ELIMINAR | EVENTO",
        modulo="COORDINADOR",
        accion_tipo="ELIMINAR",
        entidad="Evento",
        objeto_id=pk,
        detalles=detalles,
    )
    messages.success(request, "Evento eliminado correctamente junto con sus registros relacionados.")
    return redirect("coordinador:eventos")


@login_required
def gestion_evento(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    operation_cards = [
        {"title": "Registrar cronograma", "url": "coordinador:cronograma", "icon": "calendar_month", "desc": "Define fechas y sesiones del evento.", "tone": "blue"},
        {"title": "Administrar inscripciones", "url": "coordinador:inscripciones", "icon": "groups", "desc": "Gestiona participantes y ponentes.", "tone": "emerald"},
        {"title": "Asignar evaluadores", "url": "coordinador:evaluadores", "icon": "fact_check", "desc": "Vincula jurados expertos.", "tone": "orange"},
        {"title": "Gestión de rúbricas", "url": "coordinador:rubricas", "icon": "rule", "desc": "Criterios y escalas de evaluación.", "tone": "purple"},
        {"title": "Asignar espacios", "url": "coordinador:espacios", "icon": "meeting_room", "desc": "Aulas, auditorios y salas virtuales.", "tone": "yellow"},
        {"title": "Reportes", "url": "coordinador:reportes", "icon": "bar_chart", "desc": "Estadísticas y exportables.", "tone": "red"},
    ]
    return render(request, "coordinador/eventos/gestion.html", {"active": "eventos", "evento": evento, "selected_stats": _evento_related_counts(evento), "operation_cards": operation_cards})


# =========================================================
# Cronograma
# =========================================================
@login_required
def cronograma(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    actividades = ActividadCronograma.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    return render(request, "coordinador/cronograma/cronograma.html", {"active": "cronograma", "evento": evento, "actividades": actividades, "form": ActividadCronogramaForm()})


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
        messages.success(request, "Actividad actualizada correctamente." if instance else "Actividad agregada correctamente.")
    except ValidationError as e:
        messages.error(request, "; ".join(getattr(e, "messages", []) or ["No se pudo guardar la actividad."]))
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
# Inscripciones
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
            | Q(usuario__username__icontains=q)
        )
    if rol:
        qs = qs.filter(rol=rol)
    if activo in {"1", "true", "TRUE"}:
        qs = qs.filter(usuario__is_active=True)
    elif activo in {"0", "false", "FALSE"}:
        qs = qs.filter(usuario__is_active=False)

    return render(request, "coordinador/inscripciones/inscripciones.html", {
        "active": "inscripciones",
        "evento": evento,
        "inscripciones": qs.order_by("-id"),
        "form": InscripcionUsuarioForm(),
        "q": q,
        "rol": rol,
        "activo": activo,
    })


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
    writer.writerow(["ID", "Nombre", "Correo", "Rol", "Activo"])
    for insc in qs:
        user = insc.usuario
        writer.writerow([
            insc.id,
            _safe_user_display(user),
            getattr(user, "email", ""),
            insc.rol,
            "Sí" if getattr(user, "is_active", False) else "No",
        ])
    return response


# =========================================================
# Evaluadores / programación
# =========================================================
def _get_ponencia_model():
    return _safe_get_model("ponente", "Ponencia")


def _ponencia_responsable(ponencia) -> str:
    user = getattr(ponencia, "ponente", None)
    if user:
        return _safe_user_display(user)
    return (getattr(ponencia, "autores", "") or "").strip() or "Ponente"


def _get_ponencias_evento_qs(evento):
    Ponencia = _get_ponencia_model()
    if Ponencia is None:
        return []
    try:
        return Ponencia.objects.filter(evento=evento).select_related("ponente", "evaluacion_proyecto").order_by("-id")
    except Exception:
        return Ponencia.objects.filter(evento=evento).order_by("-id")


def _norm_lookup(value) -> str:
    """Normaliza cadenas para comparar títulos/responsables sin depender de mayúsculas o espacios."""
    return " ".join(str(value or "").strip().lower().split())


def _eval_records_for_ponencia(evento, ponencia):
    """
    Devuelve los registros coordinador.EvaluacionProyecto que representan
    operativamente a una ponencia.

    No se usa directamente ponencia.evaluacion_proyecto_id salvo que el campo
    apunte al modelo coordinador.EvaluacionProyecto. En este proyecto ese campo
    suele apuntar al modelo evaluador.EvaluacionProyecto, por lo que usar ese ID
    como si fuera coordinador puede provocar cruces y duplicidad visual.
    """
    if ponencia is None:
        return EvaluacionProyecto.objects.none()

    titulo = (getattr(ponencia, "titulo", "") or "").strip()
    if not titulo:
        return EvaluacionProyecto.objects.none()

    qs = EvaluacionProyecto.objects.filter(evento=evento, titulo__iexact=titulo).order_by("id")
    responsable = _norm_lookup(_ponencia_responsable(ponencia))

    if responsable:
        ids_responsable = [
            registro.id
            for registro in qs
            if _norm_lookup(getattr(registro, "ponente", "")) == responsable
        ]
        if ids_responsable:
            return EvaluacionProyecto.objects.filter(id__in=ids_responsable).order_by("id")

    # Si solamente existe un registro con ese título, se considera puente operativo.
    if qs.count() == 1:
        return qs

    return EvaluacionProyecto.objects.none()


def _match_eval_record_for_ponencia(evento, ponencia):
    """
    Resuelve el registro operativo coordinador.EvaluacionProyecto asociado
    a una ponencia, sin crear registros nuevos.
    """
    registros = _eval_records_for_ponencia(evento, ponencia)
    return registros.order_by("id").first()


def _eval_record_ids_for_ponencias(evento) -> set[int]:
    """
    Obtiene todos los IDs de EvaluacionProyecto que ya representan ponencias.
    Estos registros no deben mostrarse como PROYECTO en espacios, evaluadores
    ni selectores, porque su representación visible correcta es PONENCIA.
    """
    ids: set[int] = set()
    for ponencia in _get_ponencias_evento_qs(evento):
        for registro_id in _eval_records_for_ponencia(evento, ponencia).values_list("id", flat=True):
            if registro_id:
                ids.add(int(registro_id))
    return ids




def _resolve_programacion_estado(instance, programado: bool) -> str:
    Model = instance.__class__
    if programado:
        return (
            getattr(Model, "PROG_CONFIRMADO", None)
            or getattr(Model, "PROG_PROGRAMADO", None)
            or getattr(Model, "ESTADO_PROGRAMADO", None)
            or "PROGRAMADO"
        )
    return (
        getattr(Model, "PROG_PENDIENTE", None)
        or getattr(Model, "ESTADO_PENDIENTE", None)
        or "PENDIENTE"
    )


def _save_changed_fields(instance, payload: dict) -> None:
    dirty = []
    for field, value in payload.items():
        if not hasattr(instance, field):
            continue
        if getattr(instance, field, None) != value:
            setattr(instance, field, value)
            dirty.append(field)
    if dirty:
        if hasattr(instance, "actualizado_en") and "actualizado_en" not in dirty:
            dirty.append("actualizado_en")
        instance.save(update_fields=dirty)


def _propagate_visible_schedule_to_origin_models(evento, coord_proyecto, eval_proy=None) -> None:
    """
    Refleja en Ponencia y ProyectoParticipante la programación visible cuando
    existe un espacio real. Si solo se asignaron evaluadores sin espacio, no se
    copian horas técnicas/default al usuario final.
    """
    titulo = (getattr(coord_proyecto, "titulo", "") or "").strip()
    if not titulo:
        return

    fecha_programada = getattr(evento, "fecha", None)
    hora_inicio = getattr(coord_proyecto, "inicio", None)
    hora_fin = getattr(coord_proyecto, "fin", None)
    espacio = (getattr(coord_proyecto, "lugar", "") or "").strip()
    programado = bool(fecha_programada and hora_inicio and hora_fin and espacio)
    eval_pk = getattr(eval_proy, "pk", None)

    # Ponencia
    try:
        Ponencia = apps.get_model("ponente", "Ponencia")
        filtros = Q(titulo__iexact=titulo)
        if eval_pk:
            filtros |= Q(evaluacion_proyecto_id=eval_pk)
        ponencia = (
            Ponencia.objects.filter(evento_id=getattr(coord_proyecto, "evento_id", None))
            .filter(filtros)
            .order_by("id")
            .first()
        )
        if ponencia is not None:
            payload = {}
            if eval_pk:
                payload["evaluacion_proyecto_id"] = eval_pk
            if programado:
                payload.update({
                    "fecha_programada": fecha_programada,
                    "hora_inicio": hora_inicio,
                    "hora_fin": hora_fin,
                    "espacio_asignado": espacio,
                    "estado_programacion": _resolve_programacion_estado(ponencia, True),
                })
            _save_changed_fields(ponencia, payload)
    except Exception:
        pass

    # Proyecto participante
    try:
        ProyectoParticipante = apps.get_model("participante", "ProyectoParticipante")
        filtros = Q(nombre_proyecto__iexact=titulo)
        if eval_pk:
            filtros |= Q(evaluacion_proyecto_id=eval_pk)
        proyecto = (
            ProyectoParticipante.objects.filter(evento_id=getattr(coord_proyecto, "evento_id", None))
            .filter(filtros)
            .order_by("id")
            .first()
        )
        if proyecto is not None:
            payload = {}
            if eval_pk:
                payload["evaluacion_proyecto_id"] = eval_pk
            if programado:
                payload.update({
                    "fecha_programada": fecha_programada,
                    "hora_inicio": hora_inicio,
                    "hora_fin": hora_fin,
                    "espacio_asignado": espacio,
                    "estado_programacion": _resolve_programacion_estado(proyecto, True),
                })
            _save_changed_fields(proyecto, payload)
    except Exception:
        pass


def _coor_parse_time(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _coor_validation_error_text(exc, default="No se pudo completar la operación.") -> str:
    if hasattr(exc, "message_dict") and exc.message_dict:
        errores = []
        for campo, mensajes in exc.message_dict.items():
            etiqueta = str(campo).replace("_", " ").capitalize()
            for msg in mensajes:
                errores.append(f"{etiqueta}: {msg}")
        return " | ".join(errores) or default
    mensajes = getattr(exc, "messages", None)
    if mensajes:
        return "; ".join(mensajes)
    return str(exc) or default


def _coor_default_inicio():
    return datetime.strptime("08:00", "%H:%M").time()


def _coor_default_fin():
    return datetime.strptime("08:30", "%H:%M").time()


def _coor_has_real_schedule(inicio_raw: str, fin_raw: str) -> bool:
    return bool((inicio_raw or "").strip() and (fin_raw or "").strip())
def _sync_to_evaluador_tables(evento, coord_proyecto, evaluador_ids):
    """
    Mantiene sincronizadas las tablas del módulo evaluador con las del coordinador
    y además propaga la programación visible a ponente y participante.
    """
    if not getattr(coord_proyecto, "pk", None):
        return
    if not getattr(coord_proyecto, "inicio", None) or not getattr(coord_proyecto, "fin", None):
        return

    EvalProyectoEval = _safe_get_model("evaluador", "EvaluacionProyecto")
    EvalAsgEval = _safe_get_model("evaluador", "EvaluacionAsignacion")
    if EvalProyectoEval is None or EvalAsgEval is None:
        return

    eval_proy, _ = EvalProyectoEval.objects.get_or_create(
        evento=evento,
        titulo=coord_proyecto.titulo,
        defaults={
            "ponente": coord_proyecto.ponente,
            "inicio": coord_proyecto.inicio,
            "fin": coord_proyecto.fin,
            "lugar": coord_proyecto.lugar,
        },
    )

    changed = False
    if getattr(eval_proy, "ponente", "") != getattr(coord_proyecto, "ponente", ""):
        eval_proy.ponente = coord_proyecto.ponente
        changed = True
    if getattr(eval_proy, "inicio", None) != getattr(coord_proyecto, "inicio", None):
        eval_proy.inicio = coord_proyecto.inicio
        changed = True
    if getattr(eval_proy, "fin", None) != getattr(coord_proyecto, "fin", None):
        eval_proy.fin = coord_proyecto.fin
        changed = True
    if (getattr(eval_proy, "lugar", "") or "") != (getattr(coord_proyecto, "lugar", "") or ""):
        eval_proy.lugar = coord_proyecto.lugar
        changed = True
    if changed:
        eval_proy.save()

    _propagate_visible_schedule_to_origin_models(evento, coord_proyecto, eval_proy)

    target_ids = set(evaluador_ids or [])
    EvalAsgEval.objects.filter(proyecto=eval_proy).exclude(evaluador_id__in=target_ids).delete()
    existing_ids = set(EvalAsgEval.objects.filter(proyecto=eval_proy).values_list("evaluador_id", flat=True))
    for uid in target_ids:
        if uid not in existing_ids:
            try:
                EvalAsgEval.objects.create(proyecto=eval_proy, evaluador_id=uid)
            except Exception:
                pass


def _sync_space_to_eval_record(evento, espacio_obj):
    registro = getattr(espacio_obj, "proyecto", None)
    if getattr(espacio_obj, "ponencia", None) is not None:
        ponencia = espacio_obj.ponencia
        registro = _match_eval_record_for_ponencia(evento, ponencia)
        if registro is None:
            registro = EvaluacionProyecto(
                evento=evento,
                titulo=(getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip(),
                ponente=_ponencia_responsable(ponencia),
                inicio=espacio_obj.inicio,
                fin=espacio_obj.fin,
                lugar=espacio_obj.nombre,
            )
    if registro is None:
        return None
    registro.evento = evento
    registro.inicio = espacio_obj.inicio
    registro.fin = espacio_obj.fin
    registro.lugar = espacio_obj.nombre
    try:
        registro.full_clean()
        registro.save()
    except ValidationError:
        raise
    return registro


def _event_evaluadores_qs(evento):
    """
    Devuelve candidatos a evaluador para el evento actual.

    Incluye:
    - usuarios ya inscritos al evento con rol EVALUADOR
    - usuarios con rol base global de evaluador, aunque todavía no estén inscritos
      al evento (la inscripción se crea automáticamente al guardar la asignación)
    """
    qs = User.objects.none()

    # Relación real del modelo coordinador.Inscripcion -> User
    for rel_name in ("inscripciones_evento_evaluador", "inscripciones_evento", "inscripcion"):
        try:
            qs = qs | User.objects.filter(**{
                f"{rel_name}__evento": evento,
                f"{rel_name}__rol": Inscripcion.ROL_EVALUADOR,
            })
        except Exception:
            continue

    # Compatibilidad con modelos de usuario que manejan rol global
    try:
        field_names = {f.name for f in User._meta.fields}
        for role_field in ("rol", "tipo_usuario", "tipo", "user_type"):
            if role_field in field_names:
                qs = qs | User.objects.filter(**{f"{role_field}__in": ["EVAL", "EVALUADOR"]})
    except Exception:
        pass

    return qs.distinct().order_by("first_name", "last_name", "username", "id")


def _ensure_event_project_records_integrated(evento) -> int:
    """
    Garantiza que los proyectos del módulo participante existan también como
    registros operativos en coordinador.EvaluacionProyecto, para que puedan
    gestionarse desde evaluadores, espacios y rúbricas.
    No duplica registros existentes y no sobreescribe horarios ya programados.
    """
    ProyectoParticipante = _safe_get_model("participante", "ProyectoParticipante")
    if ProyectoParticipante is None:
        return 0

    try:
        proyectos_qs = ProyectoParticipante.objects.filter(evento=evento).select_related("participante")
    except Exception:
        proyectos_qs = ProyectoParticipante.objects.filter(evento=evento)

    created = 0
    default_inicio = datetime.strptime("08:00", "%H:%M").time()
    default_fin = datetime.strptime("08:30", "%H:%M").time()

    for proyecto in proyectos_qs:
        titulo = (getattr(proyecto, "nombre_proyecto", "") or "").strip()
        if not titulo:
            continue
        responsable = (
            (getattr(proyecto, "nombre_participante", "") or "").strip()
            or _safe_user_display(getattr(proyecto, "participante", None))
        )
        registro = EvaluacionProyecto.objects.filter(evento=evento, titulo__iexact=titulo).order_by("id").first()
        if registro is None:
            EvaluacionProyecto.objects.create(
                evento=evento,
                titulo=titulo,
                ponente=responsable,
                inicio=getattr(proyecto, "hora_inicio", None) or default_inicio,
                fin=getattr(proyecto, "hora_fin", None) or default_fin,
                lugar=(getattr(proyecto, "espacio_asignado", "") or "").strip(),
            )
            created += 1
        else:
            changed = False
            if (getattr(registro, "ponente", "") or "").strip() != responsable and not (getattr(registro, "ponente", "") or "").strip():
                registro.ponente = responsable
                changed = True
            if changed:
                registro.save(update_fields=["ponente", "actualizado_en"])
    return created


def _minutes_between(start, end) -> int:
    if not start or not end:
        return 0
    return max(((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)), 0)


def _build_evaluadores_cards(evento):
    cards = []
    for user in _event_evaluadores_qs(evento).order_by("first_name", "last_name", "id"):
        cards.append({
            "id": user.id,
            "display_name": _safe_user_display(user),
            "email": getattr(user, "email", "") or "",
            "rol_base": "Evaluador",
            "carga_total": EvaluacionAsignacion.objects.filter(proyecto__evento=evento, evaluador=user).count(),
        })
    return cards


def _build_eval_items(evento):
    """
    Construye la lista visible de registros evaluables sin duplicar ponencias.

    Los registros coordinador.EvaluacionProyecto que representan a una ponencia
    se ocultan como PROYECTO y se muestran únicamente como PONENCIA.
    """
    items = []
    used_eval_record_ids: set[int] = set()
    seen_display_keys: set[tuple] = set()

    def append_item(item: dict) -> None:
        key = (
            item.get("source_type"),
            _norm_lookup(item.get("titulo")),
            _norm_lookup(item.get("responsable")),
        )
        if key in seen_display_keys:
            return
        seen_display_keys.add(key)
        items.append(item)

    # Primero se muestran las ponencias reales.
    for ponencia in _get_ponencias_evento_qs(evento):
        registros_ponencia = list(_eval_records_for_ponencia(evento, ponencia))
        for registro_tmp in registros_ponencia:
            if getattr(registro_tmp, "id", None):
                used_eval_record_ids.add(int(registro_tmp.id))

        registro = registros_ponencia[0] if registros_ponencia else None
        asignaciones = list(registro.asignaciones.select_related("evaluador")) if registro else []
        assigned_names = [_safe_user_display(a.evaluador) for a in asignaciones]
        assigned_ids = [str(a.evaluador_id) for a in asignaciones if a.evaluador_id]

        append_item({
            "source_type": "PONENCIA",
            "source_id": getattr(ponencia, "id", ""),
            "tipo_label": "Ponencia",
            "titulo": (getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip(),
            "responsable": _ponencia_responsable(ponencia),
            "inicio": getattr(registro, "inicio", None),
            "fin": getattr(registro, "fin", None),
            "lugar": getattr(registro, "lugar", "") if registro else "",
            "assigned_names": assigned_names,
            "assigned_ids_csv": ",".join(assigned_ids),
            "asignados_count": len(assigned_ids),
            "estado": "Evaluadores asignados" if assigned_ids else "Pendiente",
        })

    # Después se muestran solo proyectos reales; se excluyen los puentes de ponencia.
    proyectos_qs = (
        EvaluacionProyecto.objects
        .filter(evento=evento)
        .prefetch_related("asignaciones__evaluador")
        .order_by("inicio", "fin", "id")
    )
    if used_eval_record_ids:
        proyectos_qs = proyectos_qs.exclude(id__in=used_eval_record_ids)

    for proyecto in proyectos_qs:
        asignaciones = list(proyecto.asignaciones.select_related("evaluador"))
        assigned_names = [_safe_user_display(a.evaluador) for a in asignaciones]
        assigned_ids = [str(a.evaluador_id) for a in asignaciones if a.evaluador_id]

        append_item({
            "source_type": "PROYECTO",
            "source_id": proyecto.id,
            "tipo_label": "Proyecto",
            "titulo": proyecto.titulo,
            "responsable": proyecto.ponente or "Sin responsable",
            "inicio": proyecto.inicio,
            "fin": proyecto.fin,
            "lugar": proyecto.lugar,
            "assigned_names": assigned_names,
            "assigned_ids_csv": ",".join(assigned_ids),
            "asignados_count": len(assigned_ids),
            "estado": "Evaluadores asignados" if assigned_ids else "Pendiente",
        })

    items.sort(key=lambda x: ((x.get("inicio") is None), x.get("inicio") or _time_sort_default(), x.get("titulo") or ""))
    return items


def _build_space_rows(evento):
    rows = []
    espacios_by_project = {
        e.proyecto_id: e
        for e in Espacio.objects.filter(evento=evento, proyecto_id__isnull=False).order_by("-id")
    }
    espacios_by_ponencia = {
        e.ponencia_id: e
        for e in Espacio.objects.filter(evento=evento, ponencia_id__isnull=False).order_by("-id")
    }

    used_eval_record_ids = _eval_record_ids_for_ponencias(evento)

    # Primero se muestran las ponencias reales.
    for ponencia in _get_ponencias_evento_qs(evento):
        registros_ponencia = list(_eval_records_for_ponencia(evento, ponencia))
        registro = registros_ponencia[0] if registros_ponencia else None

        # Si el espacio fue guardado sobre el puente EvaluacionProyecto en una versión anterior,
        # se reutiliza para no perder la programación, pero se presenta visualmente como PONENCIA.
        espacio = espacios_by_ponencia.get(getattr(ponencia, "id", None))
        if espacio is None and registro is not None:
            espacio = espacios_by_project.get(getattr(registro, "id", None))

        inicio = getattr(espacio, "inicio", None) or getattr(registro, "inicio", None)
        fin = getattr(espacio, "fin", None) or getattr(registro, "fin", None)

        rows.append({
            "source_type": "PONENCIA",
            "source_id": getattr(ponencia, "id", ""),
            "tipo_label": "Ponencia",
            "titulo": (getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip(),
            "responsable": _ponencia_responsable(ponencia),
            "space_id": getattr(espacio, "id", None),
            "asignada": bool(espacio),
            "nombre": getattr(espacio, "nombre", "") or getattr(registro, "lugar", ""),
            "tipo": getattr(espacio, "tipo", "") or "",
            "capacidad": getattr(espacio, "capacidad", "") or "",
            "ubicacion": getattr(espacio, "ubicacion", "") or "",
            "inicio": inicio,
            "fin": fin,
            "duracion": _minutes_between(inicio, fin),
            "estado": getattr(espacio, "estado", "") or ("OCUPADO" if espacio else "PENDIENTE"),
            "tags": getattr(espacio, "tags", "") or "",
        })

    # Después se muestran solo proyectos reales; los puentes de ponencia se excluyen.
    proyectos_qs = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    if used_eval_record_ids:
        proyectos_qs = proyectos_qs.exclude(id__in=used_eval_record_ids)

    for proyecto in proyectos_qs:
        espacio = espacios_by_project.get(proyecto.id)
        inicio = getattr(espacio, "inicio", None) or getattr(proyecto, "inicio", None)
        fin = getattr(espacio, "fin", None) or getattr(proyecto, "fin", None)
        rows.append({
            "source_type": "PROYECTO",
            "source_id": proyecto.id,
            "tipo_label": "Proyecto",
            "titulo": proyecto.titulo,
            "responsable": proyecto.ponente or "Sin responsable",
            "space_id": getattr(espacio, "id", None),
            "asignada": bool(espacio),
            "nombre": getattr(espacio, "nombre", "") or getattr(proyecto, "lugar", ""),
            "tipo": getattr(espacio, "tipo", "") or "",
            "capacidad": getattr(espacio, "capacidad", "") or "",
            "ubicacion": getattr(espacio, "ubicacion", "") or "",
            "inicio": inicio,
            "fin": fin,
            "duracion": _minutes_between(inicio, fin),
            "estado": getattr(espacio, "estado", "") or ("OCUPADO" if espacio else "PENDIENTE"),
            "tags": getattr(espacio, "tags", "") or "",
        })

    rows.sort(key=lambda x: ((x.get("inicio") is None), x.get("inicio") or _time_sort_default(), x.get("titulo") or ""))
    return rows


def _build_eval_cards(evento):
    proyectos = list(EvaluacionProyecto.objects.filter(evento=evento).prefetch_related("asignaciones").order_by("inicio", "fin", "id"))
    cards = []
    for p in proyectos:
        cards.append({
            "kind": "PROYECTO",
            "source_id": p.id,
            "obj": p,
            "titulo": p.titulo,
            "responsable": p.ponente,
            "inicio": p.inicio,
            "fin": p.fin,
            "lugar": p.lugar,
            "evaluadores": list(p.asignaciones.select_related("evaluador")),
        })
    for ponencia in _get_ponencias_evento_qs(evento):
        registro = _match_eval_record_for_ponencia(evento, ponencia)
        asignaciones = list(registro.asignaciones.select_related("evaluador")) if registro else []
        cards.append({
            "kind": "PONENCIA",
            "source_id": ponencia.id,
            "obj": ponencia,
            "titulo": getattr(ponencia, "titulo", "Ponencia"),
            "responsable": _ponencia_responsable(ponencia),
            "inicio": getattr(registro, "inicio", None),
            "fin": getattr(registro, "fin", None),
            "lugar": getattr(registro, "lugar", "") if registro else "",
            "evaluadores": asignaciones,
            "registro": registro,
        })
    return cards


@login_required
def evaluadores(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    _ensure_event_project_records_integrated(evento)
    q = (request.GET.get("q") or "").strip()
    items = _build_eval_items(evento)
    if q:
        ql = q.lower()
        items = [
            item for item in items
            if ql in str(item.get("titulo", "")).lower()
            or ql in str(item.get("responsable", "")).lower()
            or ql in str(item.get("lugar", "")).lower()
        ]

    resumen = {
        "total_evaluadores": _event_evaluadores_qs(evento).count(),
        "total_registros": len(items),
        "pendientes": sum(1 for item in items if not item.get("asignados_count")),
        "ponencias_total": sum(1 for item in items if item.get("source_type") == "PONENCIA"),
        "proyectos_total": sum(1 for item in items if item.get("source_type") == "PROYECTO"),
    }

    used_eval_record_ids = _eval_record_ids_for_ponencias(evento)
    proyectos_qs = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    if used_eval_record_ids:
        proyectos_qs = proyectos_qs.exclude(id__in=used_eval_record_ids)
    ponencias_qs = _get_ponencias_evento_qs(evento)

    return render(request, "coordinador/evaluadores/evaluadores.html", {
        "active": "evaluadores",
        "evento": evento,
        "items": items,
        "cards": items,
        "evaluadores_cards": _build_evaluadores_cards(evento),
        "resumen_evaluadores": resumen,
        "evaluadores": _event_evaluadores_qs(evento).order_by("first_name", "last_name", "id"),
        "proyectos": proyectos_qs,
        "ponencias": ponencias_qs,
        "q": q,
    })


@login_required
@require_POST
@transaction.atomic
def eval_proyecto_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    proyecto_id = (request.POST.get("proyecto_id") or "").strip()
    instance = get_object_or_404(EvaluacionProyecto, pk=proyecto_id, evento=evento) if proyecto_id else None
    form = EvaluacionProyectoForm(request.POST, instance=instance)
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica la programación del registro.")
        return redirect("coordinador:evaluadores")
    obj = form.save(commit=False)
    obj.evento = evento
    try:
        obj.full_clean()
        obj.save()
        _sync_to_evaluador_tables(evento, obj, list(EvaluacionAsignacion.objects.filter(proyecto=obj).values_list("evaluador_id", flat=True)))
        messages.success(request, "Programación guardada correctamente.")
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
    obj = get_object_or_404(EvaluacionProyecto, pk=pk, evento=evento)
    titulo = obj.titulo
    obj.delete()
    try:
        EvalProyectoEval = apps.get_model("evaluador", "EvaluacionProyecto")
        EvalProyectoEval.objects.filter(evento=evento, titulo__iexact=titulo).delete()
    except Exception:
        pass
    messages.success(request, "Registro programado eliminado correctamente.")
    return redirect("coordinador:evaluadores")


@login_required
@require_POST
@transaction.atomic
def eval_gestionar_guardar(request: HttpRequest) -> HttpResponse:
    """
    Asigna evaluadores a una ponencia o proyecto del evento activo.

    Reglas reforzadas:
    - No depende de que previamente exista espacio asignado.
    - Requiere un registro evaluable válido.
    - Requiere al menos un evaluador seleccionado.
    - Si existe horario real capturado, valida traslapes.
    - Si todavía no hay horario real, permite guardar la asignación y deja
      la programación pendiente para la opción de espacios.
    - Todo error se responde mediante messages/modal, nunca con pantalla técnica.
    """
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    source_type = (request.POST.get("source_type") or "PROYECTO").strip().upper()
    source_id = (request.POST.get("source_id") or request.POST.get("proyecto_id") or "").strip()

    inicio_raw = _first_post_value(request, "inicio", "hora_inicio")
    fin_raw = _first_post_value(request, "fin", "hora_fin")
    lugar = _first_post_value(request, "lugar", "ubicacion")

    seleccionados = []
    for raw_id in request.POST.getlist("evaluadores"):
        if str(raw_id).isdigit():
            uid = int(raw_id)
            if uid not in seleccionados:
                seleccionados.append(uid)

    evaluadores_validos = set(_event_evaluadores_qs(evento).values_list("id", flat=True))
    seleccionados = [uid for uid in seleccionados if uid in evaluadores_validos]

    if not seleccionados:
        messages.error(request, "Selecciona al menos un evaluador para guardar la asignación.")
        return redirect("coordinador:evaluadores")

    Ponencia = _get_ponencia_model()
    ponencia = None

    if source_type == "PONENCIA":
        if Ponencia is None or not source_id.isdigit():
            messages.error(request, "Selecciona una ponencia válida del evento activo.")
            return redirect("coordinador:evaluadores")

        ponencia = Ponencia.objects.filter(pk=int(source_id), evento=evento).first()
        if ponencia is None:
            messages.error(request, "La ponencia seleccionada no pertenece al evento activo.")
            return redirect("coordinador:evaluadores")

        proyecto = _match_eval_record_for_ponencia(evento, ponencia)
        titulo = str(getattr(ponencia, "titulo", "Ponencia") or "Ponencia").strip()
        responsable = _ponencia_responsable(ponencia)
        if proyecto is None:
            proyecto = EvaluacionProyecto(evento=evento, titulo=titulo, ponente=responsable)
    else:
        if not source_id.isdigit():
            messages.error(request, "Selecciona un proyecto válido del evento activo.")
            return redirect("coordinador:evaluadores")
        proyecto = EvaluacionProyecto.objects.filter(pk=int(source_id), evento=evento).first()
        if proyecto is None:
            messages.error(request, "El proyecto seleccionado no pertenece al evento activo.")
            return redirect("coordinador:evaluadores")
        titulo = (getattr(proyecto, "titulo", "") or "").strip()
        responsable = (getattr(proyecto, "ponente", "") or "").strip()

    if not titulo:
        messages.error(request, "No se puede guardar la asignación porque el registro no tiene título.")
        return redirect("coordinador:evaluadores")

    real_schedule = _coor_has_real_schedule(inicio_raw, fin_raw)
    inicio_parsed = _coor_parse_time(inicio_raw)
    fin_parsed = _coor_parse_time(fin_raw)

    proyecto.evento = evento
    proyecto.titulo = titulo
    proyecto.ponente = responsable
    proyecto.inicio = inicio_parsed or getattr(proyecto, "inicio", None) or _coor_default_inicio()
    proyecto.fin = fin_parsed or getattr(proyecto, "fin", None) or _coor_default_fin()
    proyecto.lugar = lugar if lugar else (getattr(proyecto, "lugar", "") or "")

    if proyecto.fin <= proyecto.inicio:
        messages.error(request, "La hora fin debe ser mayor que la hora inicio.")
        return redirect("coordinador:evaluadores")

    try:
        proyecto.full_clean()
        proyecto.save()
    except ValidationError as exc:
        messages.error(request, _coor_validation_error_text(exc, "No se pudo validar el registro evaluable."))
        return redirect("coordinador:evaluadores")
    except Exception:
        messages.error(request, "No fue posible guardar el registro evaluable. Verifica los datos capturados.")
        return redirect("coordinador:evaluadores")

    if real_schedule:
        conflictos = []
        for uid in seleccionados:
            conflicto_qs = (
                EvaluacionAsignacion.objects
                .select_related("proyecto", "evaluador")
                .filter(evaluador_id=uid, proyecto__evento=evento)
                .filter(proyecto__inicio__lt=proyecto.fin, proyecto__fin__gt=proyecto.inicio)
                .exclude(proyecto_id=proyecto.pk)
            )
            conflict = conflicto_qs.first()
            if conflict:
                conflictos.append(
                    f"{_safe_user_display(conflict.evaluador)} ({conflict.proyecto.titulo}: "
                    f"{conflict.proyecto.inicio.strftime('%H:%M')} - {conflict.proyecto.fin.strftime('%H:%M')})"
                )
        if conflictos:
            messages.error(
                request,
                "No se guardó la asignación porque existe conflicto de horario para: "
                + ", ".join(dict.fromkeys(conflictos))
                + ".",
            )
            return redirect("coordinador:evaluadores")

    for uid in seleccionados:
        Inscripcion.objects.get_or_create(
            evento=evento,
            usuario_id=uid,
            defaults={"rol": Inscripcion.ROL_EVALUADOR},
        )

    EvaluacionAsignacion.objects.filter(proyecto=proyecto).exclude(evaluador_id__in=seleccionados).delete()
    actuales = set(EvaluacionAsignacion.objects.filter(proyecto=proyecto).values_list("evaluador_id", flat=True))

    for uid in seleccionados:
        if uid in actuales:
            continue
        asignacion = EvaluacionAsignacion(proyecto=proyecto, evaluador_id=uid)
        try:
            if real_schedule:
                asignacion.full_clean()
            asignacion.save()
        except ValidationError as exc:
            messages.error(request, _coor_validation_error_text(exc, "No se pudo guardar la asignación."))
            return redirect("coordinador:evaluadores")
        except Exception:
            messages.error(request, "No fue posible guardar una de las asignaciones de evaluador.")
            return redirect("coordinador:evaluadores")

    _sync_to_evaluador_tables(evento, proyecto, seleccionados)

    if real_schedule:
        messages.success(request, "Asignación de evaluadores guardada correctamente.")
    else:
        messages.warning(
            request,
            "Evaluadores asignados correctamente. Aún falta asignar espacio y horario para completar la programación del registro."
        )

    return redirect("coordinador:evaluadores")



# =========================================================
# Espacios
# =========================================================
@login_required
def espacios(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    _ensure_event_project_records_integrated(evento)
    q = (request.GET.get("q") or "").strip()
    used_eval_record_ids = _eval_record_ids_for_ponencias(evento)
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    if used_eval_record_ids:
        proyectos = proyectos.exclude(id__in=used_eval_record_ids)
    ponencias_qs = _get_ponencias_evento_qs(evento)
    espacios_qs = Espacio.objects.filter(evento=evento).select_related("proyecto", "ponencia").order_by("inicio", "fin", "id")
    if q:
        espacios_qs = espacios_qs.filter(
            Q(nombre__icontains=q)
            | Q(ubicacion__icontains=q)
            | Q(tags__icontains=q)
            | Q(proyecto__titulo__icontains=q)
            | Q(ponencia__titulo__icontains=q)
        )

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

    return render(request, "coordinador/espacios/espacios.html", {
        "active": "espacios",
        "evento": evento,
        "espacios": espacios_qs,
        "rows": rows,
        "resumen_espacios": resumen,
        "proyectos": proyectos,
        "ponencias": ponencias_qs,
        "form_espacio": EspacioForm(proyectos_qs=proyectos, ponencias_qs=ponencias_qs),
        "q": q,
    })


@login_required
@require_POST
@transaction.atomic
def espacio_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    espacio_id = (request.POST.get("espacio_id") or "").strip()
    instance = get_object_or_404(Espacio, pk=espacio_id, evento=evento) if espacio_id else Espacio(evento=evento)
    used_eval_record_ids = _eval_record_ids_for_ponencias(evento)
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    if used_eval_record_ids:
        proyectos = proyectos.exclude(id__in=used_eval_record_ids)
    ponencias_qs = _get_ponencias_evento_qs(evento)
    form = EspacioForm(request.POST, instance=instance, proyectos_qs=proyectos, ponencias_qs=ponencias_qs)
    form.instance.evento = evento
    if not form.is_valid():
        messages.error(request, _flatten_form_errors(form) or "Verifica los datos del espacio.")
        return redirect("coordinador:espacios")

    obj = form.save(commit=False)
    obj.evento = evento
    obj.proyecto = form.cleaned_data.get("proyecto")
    obj.ponencia = form.cleaned_data.get("ponencia")
    if obj.ponencia_id:
        obj.proyecto = None
    elif obj.proyecto_id:
        obj.ponencia = None
    obj.inicio = form.cleaned_data.get("inicio")
    obj.fin = form.cleaned_data.get("fin_calculado")
    obj.estado = form.cleaned_data.get("estado") or Espacio.ESTADO_OCUPADO

    try:
        obj.full_clean()
        registro = _sync_space_to_eval_record(evento, obj)
        if registro and getattr(registro, "pk", None):
            actuales = list(EvaluacionAsignacion.objects.filter(proyecto=registro).values_list("evaluador_id", flat=True))
            _sync_to_evaluador_tables(evento, registro, actuales)
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

            eval_proy = None
            try:
                EvalProyectoEval = apps.get_model("evaluador", "EvaluacionProyecto")
                if EvalProyectoEval is not None:
                    eval_proy = EvalProyectoEval.objects.filter(evento=evento, titulo__iexact=(getattr(registro, "titulo", "") or "").strip()).first()
            except Exception:
                eval_proy = None

            _propagate_visible_schedule_to_origin_models(evento, registro, eval_proy)
    except Exception:
        pass

    messages.success(request, "Asignación de espacio eliminada.")
    return redirect("coordinador:espacios")


# =========================================================
# Rúbricas
# =========================================================
def _rubrica_adjunto_payload(adjunto) -> dict:
    return {
        "id": getattr(adjunto, "id", None),
        "nombre": getattr(adjunto, "nombre_original", "") or os.path.basename(getattr(getattr(adjunto, "archivo", None), "name", "")),
    }


def _build_rubricas_context(evento, *, request=None, form_rubrica=None, open_modal=False, modal_prefill=None):
    q = (request.GET.get("q") or "").strip() if request else ""
    estado = (request.GET.get("estado") or "").strip().upper() if request else ""
    tipo = (request.GET.get("tipo") or "").strip().upper() if request else ""

    _ensure_event_project_records_integrated(evento)

    rubricas_qs = Rubrica.objects.filter(evento=evento).prefetch_related("criterios", "adjuntos").order_by("-id")
    if q:
        rubricas_qs = rubricas_qs.filter(Q(titulo__icontains=q))
    if estado in {Rubrica.ESTADO_ACTIVA, Rubrica.ESTADO_BORRADOR, getattr(Rubrica, 'ESTADO_INACTIVA', 'INACTIVA')}:
        rubricas_qs = rubricas_qs.filter(estado=estado)
    if tipo in {"PROYECTO", "PONENCIA"}:
        rubricas_qs = rubricas_qs.filter(ponencia__isnull=(tipo != "PONENCIA"))

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)

    total_rubricas = rubricas_qs.count()
    activas = rubricas_qs.filter(estado=Rubrica.ESTADO_ACTIVA).count()
    borrador = rubricas_qs.filter(estado=Rubrica.ESTADO_BORRADOR).count()
    rubricas_proyecto = rubricas_qs.filter(ponencia__isnull=True).count()
    rubricas_ponencia = rubricas_qs.filter(ponencia__isnull=False).count()
    total_ponencias = len(ponencias_qs) if not hasattr(ponencias_qs, "count") else ponencias_qs.count()
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
    return render(request, "coordinador/rubricas/rubricas.html", _build_rubricas_context(evento, request=request))


@login_required
@require_POST
@transaction.atomic
def rubrica_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubrica_id = (request.POST.get("rubrica_id") or "").strip()
    instance = get_object_or_404(Rubrica.objects.prefetch_related("adjuntos", "criterios"), pk=rubrica_id, evento=evento) if rubrica_id else Rubrica(evento=evento)
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    ponencias_qs = _get_ponencias_evento_qs(evento)
    form = RubricaForm(request.POST, instance=instance, proyectos_qs=proyectos, ponencias_qs=ponencias_qs)
    form.instance.evento = evento

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
    rubrica.save()

    RubricaCriterio.objects.filter(rubrica=rubrica).delete()
    nuevos = []
    for idx, item in enumerate(criterios_limpios, start=1):
        c = RubricaCriterio(rubrica=rubrica, titulo=item["titulo"], descripcion=item["descripcion"], puntaje_max=item["puntaje"], orden=idx)
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

    if getattr(rubrica, "ponencia_id", None) and getattr(rubrica, "proyecto_id", None):
        messages.success(request, "Rúbrica guardada correctamente y enlazada al puente evaluable de la ponencia.")
    else:
        messages.success(request, "Rúbrica guardada correctamente.")
    return redirect("coordinador:rubricas")


@login_required
def rubrica_adjunto_descargar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    adjunto = get_object_or_404(RubricaAdjunto.objects.select_related("rubrica"), pk=pk, rubrica__evento=evento)
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
    adjunto = get_object_or_404(RubricaAdjunto.objects.select_related("rubrica"), pk=pk, rubrica__evento=evento)
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


# =========================================================
# Reportes
# =========================================================
def _inscripciones_kpis(evento):
    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento)
    return {
        "total": qs.count(),
        "activas": qs.filter(usuario__is_active=True).count(),
        "evaluadores": qs.filter(rol=Inscripcion.ROL_EVALUADOR).count(),
        "ponentes": qs.filter(rol=Inscripcion.ROL_PONENTE).count(),
        "participantes": qs.filter(rol=Inscripcion.ROL_PARTICIPANTE).count(),
    }


def _evaluaciones_kpis(evento):
    qs = EvaluacionProyecto.objects.filter(evento=evento)
    return {
        "total": qs.count(),
        "programadas": qs.exclude(inicio__isnull=True).exclude(fin__isnull=True).count(),
        "pendientes": qs.filter(Q(lugar="") | Q(lugar__isnull=True)).count(),
        "asignaciones": EvaluacionAsignacion.objects.filter(proyecto__evento=evento).count(),
    }


def _asistencia_kpis(evento):
    return {"registros": 0, "estado_label": "Pendiente de integración"}


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


def _dataset_inscripciones(evento):
    headers = ["Nombre", "Correo", "Rol", "Activo"]
    rows = []
    for insc in Inscripcion.objects.select_related("usuario").filter(evento=evento).order_by("-id"):
        user = insc.usuario
        rows.append([_safe_user_display(user), getattr(user, "email", ""), insc.rol, "Sí" if getattr(user, "is_active", False) else "No"])
    return headers, rows


def _dataset_evaluaciones(evento, proyecto=None):
    headers = ["Título", "Responsable", "Inicio", "Fin", "Lugar", "Evaluadores"]
    rows = []
    qs = EvaluacionProyecto.objects.filter(evento=evento).prefetch_related("asignaciones__evaluador").order_by("inicio", "fin", "id")
    if proyecto is not None:
        qs = qs.filter(pk=proyecto.pk)
    for p in qs:
        evaluadores = ", ".join(_safe_user_display(a.evaluador) for a in p.asignaciones.all())
        rows.append([p.titulo, p.ponente, str(p.inicio or ""), str(p.fin or ""), p.lugar or "", evaluadores])
    return headers, rows


def _dataset_asistencia(evento):
    return ["Módulo", "Estado"], [["Asistencia", "Pendiente de integración"]]


def _dataset_general(evento):
    headers = ["Indicador", "Valor"]
    resumen = _general_kpis(evento)
    rows = [[k.replace("_", " ").title(), v] for k, v in resumen.items()]
    return headers, rows


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
def reportes(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    q = (request.GET.get("q") or "").strip()
    categoria = (request.GET.get("categoria") or Reporte.CATEG_TODOS).strip().upper() if hasattr(Reporte, 'CATEG_TODOS') else (request.GET.get("categoria") or "").strip().upper()
    qs = Reporte.objects.filter(evento=evento).select_related("proyecto", "creado_por").order_by("-generado_en", "-id")
    if q:
        qs = qs.filter(Q(nombre__icontains=q))
    if categoria and hasattr(Reporte, 'CATEG_TODOS') and categoria != Reporte.CATEG_TODOS:
        qs = qs.filter(categoria=categoria)
    elif categoria and not hasattr(Reporte, 'CATEG_TODOS'):
        qs = qs.filter(categoria=categoria)

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form_generar = ReporteGenerarForm(request.POST or None, proyectos_qs=proyectos)

    reportes_counts = {item["categoria"]: int(item["total"] or 0) for item in Reporte.objects.filter(evento=evento).values("categoria").annotate(total=Count("id"))}
    resumen_inscripciones = _inscripciones_kpis(evento)
    resumen_evaluaciones = _evaluaciones_kpis(evento)
    resumen_asistencia = _asistencia_kpis(evento)
    resumen_general = _general_kpis(evento, resumen_inscripciones, resumen_evaluaciones)

    preview_categoria = categoria if not hasattr(Reporte, 'CATEG_TODOS') or categoria != Reporte.CATEG_TODOS else Reporte.CATEG_GENERAL
    preview_headers, preview_rows = _report_preview_for_categoria(evento, preview_categoria or Reporte.CATEG_GENERAL)

    categoria_cards = [
        {"code": Reporte.CATEG_INSCRIPCIONES, "title": "Inscripciones", "description": "Altas registradas al evento, separadas por rol y estatus.", "reports_total": reportes_counts.get(Reporte.CATEG_INSCRIPCIONES, 0), "primary": resumen_inscripciones["total"], "primary_label": "Total registradas", "secondary": f"Activas: {resumen_inscripciones['activas']} | Evaluadores: {resumen_inscripciones['evaluadores']}"},
        {"code": Reporte.CATEG_EVALUACIONES, "title": "Evaluaciones", "description": "Programación, asignaciones y cobertura de ponencias/proyectos.", "reports_total": reportes_counts.get(Reporte.CATEG_EVALUACIONES, 0), "primary": resumen_evaluaciones["programadas"], "primary_label": "Programadas", "secondary": f"Pendientes: {resumen_evaluaciones['pendientes']} | Asignaciones: {resumen_evaluaciones['asignaciones']}"},
        {"code": Reporte.CATEG_ASISTENCIA, "title": "Asistencia", "description": "Control de asistencia del evento. Queda preparado aunque el módulo aún no exista.", "reports_total": reportes_counts.get(Reporte.CATEG_ASISTENCIA, 0), "primary": resumen_asistencia["registros"], "primary_label": "Registros", "secondary": resumen_asistencia["estado_label"]},
        {"code": Reporte.CATEG_GENERAL, "title": "General", "description": "Consolidado ejecutivo del evento con métricas globales.", "reports_total": reportes_counts.get(Reporte.CATEG_GENERAL, 0), "primary": resumen_general["total_reportes"], "primary_label": "Informes generados", "secondary": f"Inscripciones: {resumen_general['inscripciones']} | Registros evaluables: {resumen_general['registros_evaluables']}"},
    ]

    return render(request, "coordinador/reportes/reportes.html", {
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
        "preview_headers": preview_headers,
        "preview_rows": preview_rows,
    })


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

    rep = Reporte(evento=evento, proyecto=proyecto, creado_por=request.user, nombre=nombre, categoria=categoria, formato=formato, modo=modo, estado=Reporte.ESTADO_LISTO, generado_en=timezone.now())
    content = b""
    filename = ""
    mimetype = "application/octet-stream"
    if formato.upper() == "CSV":
        sio = io.StringIO()
        writer = csv.writer(sio)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        content = sio.getvalue().encode("utf-8")
        filename = f"{nombre}.csv"
        mimetype = "text/csv"
    elif formato.upper() == "XLSX":
        content = _xlsx_from_rows(sheet, headers, rows)
        filename = f"{nombre}.xlsx"
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        lines = [nombre, f"Evento: {getattr(evento, 'titulo', evento.id)}", ""] + [" | ".join(map(str, headers))] + [" | ".join(map(str, r)) for r in rows]
        content = _build_minimal_pdf(lines)
        filename = f"{nombre}.pdf"
        mimetype = "application/pdf"

    if hasattr(rep, "archivo"):
        rep.archivo.save(filename, ContentFile(content), save=False)
    if hasattr(rep, "mime_type"):
        rep.mime_type = mimetype
    rep.save()
    messages.success(request, "Reporte generado correctamente.")
    return redirect("coordinador:reportes")


@login_required
def reporte_descargar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    archivo = getattr(rep, "archivo", None)
    if not archivo:
        raise Http404("El reporte solicitado no existe.")
    filename = os.path.basename(archivo.name)
    return FileResponse(archivo.open("rb"), as_attachment=True, filename=filename)


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
        messages.error(request, _flatten_form_errors(form) or "No se pudo actualizar el reporte.")
        return redirect("coordinador:reportes")
    form.save()
    messages.success(request, "Reporte actualizado correctamente.")
    return redirect("coordinador:reportes")


@login_required
@require_POST
@transaction.atomic
def reporte_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    rep.delete()
    messages.success(request, "Reporte eliminado correctamente.")
    return redirect("coordinador:reportes")


# =========================================================
# Configuración
# =========================================================
def _resolve_profile(user):
    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=user)
    return perfil


@login_required
def configuracion(request: HttpRequest) -> HttpResponse:
    user = request.user
    perfil = _resolve_profile(user)

    if request.method == "POST":
        cuenta_form = ConfigCuentaForm(request.POST, user=user)
        perfil_form = ConfigPerfilForm(request.POST, request.FILES, instance=perfil)
        if cuenta_form.is_valid() and perfil_form.is_valid():
            user.first_name = cuenta_form.cleaned_data["nombres"].strip()
            user.last_name = cuenta_form.cleaned_data["apellidos"].strip()
            user.email = cuenta_form.cleaned_data["correo"].strip().lower()
            if hasattr(user, "username"):
                user.username = user.email
            nueva = (cuenta_form.cleaned_data.get("password_nueva") or "").strip()
            if nueva:
                user.set_password(nueva)
            user.save()
            perfil_form.save()
            if nueva:
                update_session_auth_hash(request, user)
            registrar_auditoria(request=request, accion="COORDINADOR | CONFIGURACION | Actualización de cuenta/perfil", modulo="COORDINADOR", accion_tipo="CONFIGURACION", entidad="PerfilUsuario", objeto_id=user.pk, resultado="EXITOSO")
            messages.success(request, "La configuración del coordinador fue actualizada correctamente.")
            return redirect("coordinador:configuracion")
        messages.error(request, "No fue posible actualizar la configuración. Verifica los datos capturados.")
    else:
        cuenta_form = ConfigCuentaForm(user=user, initial={"nombres": user.first_name, "apellidos": user.last_name, "correo": user.email})
        perfil_form = ConfigPerfilForm(instance=perfil)

    return render(request, "coordinador/configuracion/configuracion.html", {"active": "configuracion", "cuenta_form": cuenta_form, "perfil_form": perfil_form, "perfil": perfil})
