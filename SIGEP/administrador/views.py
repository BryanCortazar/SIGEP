from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import datetime, timedelta
from functools import wraps
from typing import Callable
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import AuditoriaLog, ConfiguracionSistema, Evento
from .utils_auditoria import infer_audit_action_type, infer_audit_modulo, infer_audit_result, registrar_auditoria

User = get_user_model()

# =========================
# Decorador Admin
# =========================
def admin_required(view_func: Callable):
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if getattr(request.user, "rol", None) != "ADMIN":
            return HttpResponseForbidden("Acceso denegado.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =========================
# Utilidades
# =========================
def _get_roles_disponibles():
    # Ajusta aquí si tus roles reales son otros
    return ["ADMIN", "COOR", "EVAL", "PON", "PART"]


def _roles_ui():
    return [
        ("ADMIN", "Administrador"),
        ("COOR", "Coordinador"),
        ("EVAL", "Evaluador / Jurado"),
        ("PON", "Ponente"),
        ("PART", "Participante"),
    ]


def _role_label(role_code: str) -> str:
    mapping = dict(_roles_ui())
    return mapping.get(role_code, role_code)


def _role_group_name(role_code: str) -> str:
    return f"SIGEP_ROLE_{role_code}"


def _permission_catalog() -> list[dict]:
    return [
        {
            "module": "Administrador",
            "items": [
                {"code": "adm_dashboard_view", "name": "Ver panel de control", "desc": "Permite acceder al dashboard administrativo y consultar sus indicadores."},
                {"code": "adm_users_view", "name": "Ver usuarios", "desc": "Permite consultar el listado de usuarios y sus datos generales."},
                {"code": "adm_users_create", "name": "Crear usuarios", "desc": "Permite registrar nuevas cuentas de usuario en el sistema."},
                {"code": "adm_users_update", "name": "Editar usuarios", "desc": "Permite actualizar datos, rol y estado de usuarios existentes."},
                {"code": "adm_users_delete", "name": "Eliminar usuarios", "desc": "Permite eliminar cuentas de usuario. Debe reservarse al administrador."},
                {"code": "adm_roles_view", "name": "Ver roles y permisos", "desc": "Permite abrir la matriz de acceso y consultar permisos por rol."},
                {"code": "adm_roles_manage", "name": "Gestionar roles y permisos", "desc": "Permite asignar y retirar permisos a cada rol operativo."},
                {"code": "adm_events_view", "name": "Ver eventos", "desc": "Permite acceder a la gestión central de eventos."},
                {"code": "adm_events_manage", "name": "Gestionar eventos", "desc": "Permite crear, editar, publicar y cerrar eventos desde administración."},
                {"code": "adm_audit_view", "name": "Ver auditoría", "desc": "Permite consultar la bitácora de movimientos del sistema."},
                {"code": "adm_audit_export", "name": "Exportar auditoría", "desc": "Permite exportar la bitácora y reportes de control."},
                {"code": "adm_config_view", "name": "Ver configuración", "desc": "Permite acceder a la configuración general del sistema."},
                {"code": "adm_config_update", "name": "Editar configuración", "desc": "Permite modificar parámetros de seguridad, soporte y mantenimiento."},
            ],
        },
        {
            "module": "Coordinador",
            "items": [
                {"code": "coord_dashboard_view", "name": "Ver panel coordinador", "desc": "Permite acceder al panel principal del módulo coordinador."},
                {"code": "coord_events_view", "name": "Ver eventos coordinados", "desc": "Permite consultar eventos y datos operativos del módulo coordinador."},
                {"code": "coord_events_manage", "name": "Gestionar eventos coordinados", "desc": "Permite intervenir información operativa generada por coordinación."},
                {"code": "coord_evaluators_manage", "name": "Gestionar evaluadores", "desc": "Permite asignar y administrar evaluadores o jurados."},
                {"code": "coord_rubrics_manage", "name": "Gestionar rúbricas", "desc": "Permite crear, editar y controlar rúbricas del módulo coordinador."},
                {"code": "coord_spaces_manage", "name": "Gestionar espacios", "desc": "Permite asignar, editar y liberar espacios o salas del evento."},
                {"code": "coord_reports_view", "name": "Ver reportes coordinador", "desc": "Permite consultar información consolidada del módulo coordinador."},
            ],
        },
        {
            "module": "Evaluador",
            "items": [
                {"code": "eval_dashboard_view", "name": "Ver panel evaluador", "desc": "Permite acceder al dashboard de evaluaciones y asignaciones."},
                {"code": "eval_projects_view", "name": "Ver proyectos asignados", "desc": "Permite consultar los proyectos cargados para evaluación."},
                {"code": "eval_projects_score", "name": "Evaluar proyectos", "desc": "Permite calificar, emitir dictámenes y registrar resultados."},
                {"code": "eval_rubrics_view", "name": "Ver rúbricas", "desc": "Permite consultar rúbricas disponibles para la evaluación."},
                {"code": "eval_deliveries_review", "name": "Revisar entregas", "desc": "Permite revisar evidencias y documentos vinculados a proyectos."},
                {"code": "eval_reports_view", "name": "Ver reportes evaluador", "desc": "Permite consultar salidas y reportes del módulo evaluador."},
            ],
        },
        {
            "module": "Ponente",
            "items": [
                {"code": "pon_dashboard_view", "name": "Ver panel ponente", "desc": "Permite acceder al panel del ponente y su estatus general."},
                {"code": "pon_events_view", "name": "Ver eventos disponibles", "desc": "Permite consultar eventos y convocatorias habilitadas para ponentes."},
                {"code": "pon_submissions_create", "name": "Registrar ponencias", "desc": "Permite capturar nuevas propuestas o ponencias."},
                {"code": "pon_submissions_update", "name": "Editar ponencias", "desc": "Permite actualizar información y materiales de una ponencia."},
                {"code": "pon_submissions_delete", "name": "Eliminar ponencias", "desc": "Permite retirar registros de ponencias cuando proceda."},
                {"code": "pon_materials_upload", "name": "Cargar materiales", "desc": "Permite subir presentaciones, anexos y material complementario."},
            ],
        },
        {
            "module": "Participante",
            "items": [
                {"code": "part_dashboard_view", "name": "Ver panel participante", "desc": "Permite acceder al panel general del participante."},
                {"code": "part_events_view", "name": "Ver eventos disponibles", "desc": "Permite consultar la oferta de eventos abiertos al participante."},
                {"code": "part_registrations_create", "name": "Registrar inscripciones", "desc": "Permite realizar inscripciones a eventos o actividades."},
                {"code": "part_registrations_update", "name": "Editar inscripciones", "desc": "Permite ajustar datos de inscripción o participación."},
                {"code": "part_registrations_delete", "name": "Cancelar inscripciones", "desc": "Permite anular registros de inscripción cuando corresponda."},
                {"code": "part_constancias_view", "name": "Ver constancias", "desc": "Permite consultar constancias, comprobantes o resultados del participante."},
            ],
        },
    ]


def _all_permission_codes() -> set[str]:
    return {item["code"] for block in _permission_catalog() for item in block["items"]}


def _permission_content_type() -> ContentType:
    return ContentType.objects.get_for_model(User)


def _permission_full_name(codename: str) -> str:
    return f"{User._meta.app_label}.{codename}"


def _ensure_sigep_permission_registry() -> None:
    ct = _permission_content_type()
    for role_code in _get_roles_disponibles():
        Group.objects.get_or_create(name=_role_group_name(role_code))

    for block in _permission_catalog():
        for item in block["items"]:
            Permission.objects.get_or_create(
                content_type=ct,
                codename=item["code"],
                defaults={"name": item["name"]},
            )

    admin_group = Group.objects.get(name=_role_group_name("ADMIN"))
    admin_perms = Permission.objects.filter(content_type=ct, codename__in=_all_permission_codes())
    admin_group.permissions.set(admin_perms)


def _sync_user_role_group(user: User) -> None:
    _ensure_sigep_permission_registry()
    managed_groups = Group.objects.filter(name__in=[_role_group_name(r) for r in _get_roles_disponibles()])
    user.groups.remove(*managed_groups)
    group = Group.objects.filter(name=_role_group_name(getattr(user, "rol", "PART"))).first()
    if group is not None:
        user.groups.add(group)


def _sync_role_members(role_code: str) -> int:
    count = 0
    for user in User.objects.filter(rol=role_code):
        _sync_user_role_group(user)
        count += 1
    return count


def _filter_permission_catalog(current_codes: set[str], q: str, module_filter: str) -> list[dict]:
    q_norm = (q or "").strip().lower()
    module_norm = (module_filter or "").strip().lower()
    filtered = []

    for block in _permission_catalog():
        block_slug = slugify(block["module"])
        if module_norm and block_slug != module_norm:
            continue

        items = []
        for item in block["items"]:
            hay = f"{item['name']} {item['desc']} {item['code']} {block['module']}".lower()
            if q_norm and q_norm not in hay:
                continue
            cloned = dict(item)
            cloned["checked"] = item["code"] in current_codes
            items.append(cloned)

        if items:
            filtered.append({
                "module": block["module"],
                "module_slug": block_slug,
                "items": items,
                "checked_count": sum(1 for x in items if x["checked"]),
                "total_count": len(items),
            })

    return filtered


def _role_history_rows(limit: int = 60) -> list[dict]:
    rows = []
    logs = (
        AuditoriaLog.objects
        .select_related("usuario")
        .filter(Q(accion__startswith="ROLES |") | Q(accion__startswith="USUARIOS | CAMBIAR_ROL"))
        .order_by("-fecha")[:limit]
    )

    for log in logs:
        actor = "Sistema"
        if log.usuario:
            actor = log.usuario.get_full_name() or log.usuario.username or log.usuario.email

        accion_text = log.accion or ""
        rol = "-"
        detalle = accion_text
        badge = {"text": "Registro", "cls": "bg-slate-100 text-slate-700"}

        if accion_text.startswith("ROLES |"):
            parts = [p.strip() for p in accion_text.split("|")]
            if len(parts) >= 2:
                action_key = parts[1]
                if action_key == "GUARDAR_PERMISOS":
                    badge = {"text": "Permisos actualizados", "cls": "bg-emerald-100 text-emerald-700"}
                elif action_key == "SIN_CAMBIOS":
                    badge = {"text": "Sin cambios", "cls": "bg-slate-100 text-slate-700"}
                else:
                    badge = {"text": action_key.replace("_", " ").title(), "cls": "bg-blue-100 text-blue-700"}
            for part in parts:
                if part.strip().startswith("rol="):
                    rol = part.split("=", 1)[1].strip()
            if len(parts) > 2:
                detalle = " | ".join(parts[2:])

        elif accion_text.startswith("USUARIOS | CAMBIAR_ROL"):
            badge = {"text": "Rol de usuario", "cls": "bg-amber-100 text-amber-700"}
            if "rol=" in accion_text:
                rol = accion_text.split("rol=", 1)[1].strip().split()[0]

        rows.append({
            "fecha": log.fecha,
            "actor": actor,
            "rol": _role_label(rol),
            "accion_badge": badge,
            "detalle": detalle,
        })

    return rows


def _is_admin_user(u: User) -> bool:
    return getattr(u, "rol", None) == "ADMIN"


def _set_full_name(u: User, full_name: str) -> None:
    # Compatible: usamos first_name como "nombre completo"
    if hasattr(u, "first_name"):
        u.first_name = full_name


def _client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _audit(request: HttpRequest, accion: str, **kwargs) -> None:
    """Auditoría centralizada y tolerante a fallos."""
    registrar_auditoria(request=request, accion=accion, **kwargs)


def _resolve_model(*candidates):
    """
    Busca modelos de otros módulos sin acoplar el dashboard a imports duros.
    Cada candidato debe ser una tupla: (app_label, model_name).
    """
    for app_label, model_name in candidates:
        try:
            return apps.get_model(app_label, model_name)
        except LookupError:
            continue
    return None


def _safe_count(model, **filters) -> int:
    if model is None:
        return 0
    try:
        return model.objects.filter(**filters).count() if filters else model.objects.count()
    except Exception:
        return 0


def _has_field(model, field_name: str) -> bool:
    if model is None:
        return False
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _count_users_by_role(*roles: str) -> int:
    try:
        return User.objects.filter(rol__in=list(roles)).count()
    except Exception:
        return 0


def _build_dashboard_kpis() -> dict:
    """Construye KPIs clasificados para el panel del administrador."""
    Ponencia = _resolve_model(("ponente", "Ponencia"))
    Inscripcion = _resolve_model(
        ("participante", "Inscripcion"),
        ("coordinador", "Inscripcion"),
        ("evaluador", "Inscripcion"),
    )
    EvaluacionProyecto = _resolve_model(
        ("evaluador", "EvaluacionProyecto"),
        ("coordinador", "EvaluacionProyecto"),
    )
    EvaluacionAsignacion = _resolve_model(
        ("evaluador", "EvaluacionAsignacion"),
        ("coordinador", "EvaluacionAsignacion"),
    )
    Rubrica = _resolve_model(("evaluador", "Rubrica"), ("coordinador", "Rubrica"))
    Espacio = _resolve_model(("coordinador", "Espacio"), ("evaluador", "Espacio"))

    hoy = timezone.localdate()
    ahora = timezone.now()

    total_usuarios = User.objects.count()
    usuarios_activos = User.objects.filter(is_active=True).count()
    usuarios_inactivos = User.objects.filter(is_active=False).count()

    total_eventos = Evento.objects.count()
    eventos_borrador = _safe_count(Evento, estado="BORRADOR")
    eventos_publicados = _safe_count(Evento, estado="PUBLICADO")

    eventos_proximos = 0
    if _has_field(Evento, "fecha"):
        try:
            eventos_proximos = Evento.objects.filter(estado="PUBLICADO", fecha__gte=hoy).count()
        except Exception:
            eventos_proximos = 0

    total_inscripciones = _safe_count(Inscripcion)
    total_proyectos_eval = _safe_count(EvaluacionProyecto)
    total_asignaciones_eval = _safe_count(EvaluacionAsignacion)
    total_rubricas = _safe_count(Rubrica)
    total_espacios = _safe_count(Espacio)
    total_ponencias = _safe_count(Ponencia)

    total_logs = AuditoriaLog.objects.count()
    logs_hoy = AuditoriaLog.objects.filter(fecha__date=hoy).count()
    logs_24h = AuditoriaLog.objects.filter(fecha__gte=ahora - timedelta(hours=24)).count()

    operacion_total = (
        total_inscripciones
        + total_proyectos_eval
        + total_asignaciones_eval
        + total_rubricas
        + total_espacios
        + total_ponencias
    )

    summary_cards = [
        {
            "label": "Usuarios registrados",
            "value": total_usuarios,
            "help": "Base general de cuentas del sistema",
            "icon": "group",
            "url": reverse("administrador:usuarios"),
        },
        {
            "label": "Eventos publicados",
            "value": eventos_publicados,
            "help": "Eventos visibles y activos",
            "icon": "event_available",
            "url": reverse("administrador:eventos"),
        },
        {
            "label": "Operación registrada",
            "value": operacion_total,
            "help": "Inscripciones, evaluaciones, rúbricas, espacios y ponencias",
            "icon": "monitoring",
            "url": None,
        },
        {
            "label": "Movimientos en 24h",
            "value": logs_24h,
            "help": "Actividad reciente auditada del sistema",
            "icon": "history",
            "url": reverse("administrador:auditoria"),
        },
    ]

    kpi_groups = [
        {
            "title": "Usuarios y accesos",
            "description": "Control de cuentas, activación y distribución por rol.",
            "items": [
                {"label": "Total usuarios", "value": total_usuarios},
                {"label": "Usuarios activos", "value": usuarios_activos},
                {"label": "Usuarios inactivos", "value": usuarios_inactivos},
                {"label": "Administradores", "value": _count_users_by_role("ADMIN", "ADMINISTRADOR")},
                {"label": "Coordinadores", "value": _count_users_by_role("COOR", "COORDINADOR")},
                {"label": "Evaluadores", "value": _count_users_by_role("EVAL", "EVALUADOR")},
                {"label": "Ponentes", "value": _count_users_by_role("PON", "PONENTE")},
                {"label": "Participantes", "value": _count_users_by_role("PART", "PARTICIPANTE")},
            ],
        },
        {
            "title": "Eventos",
            "description": "Estado general de la gestión de eventos del sistema.",
            "items": [
                {"label": "Total eventos", "value": total_eventos},
                {"label": "En borrador", "value": eventos_borrador},
                {"label": "Publicados", "value": eventos_publicados},
                {"label": "Próximos", "value": eventos_proximos},
            ],
        },
        {
            "title": "Operación intermodular",
            "description": "Cargas operativas que impactan la administración general.",
            "items": [
                {"label": "Inscripciones", "value": total_inscripciones},
                {"label": "Proyectos a evaluar", "value": total_proyectos_eval},
                {"label": "Asignaciones de evaluación", "value": total_asignaciones_eval},
                {"label": "Rúbricas", "value": total_rubricas},
                {"label": "Espacios", "value": total_espacios},
                {"label": "Ponencias", "value": total_ponencias},
            ],
        },
        {
            "title": "Trazabilidad y control",
            "description": "Seguimiento técnico y administrativo de la actividad del sistema.",
            "items": [
                {"label": "Logs totales", "value": total_logs},
                {"label": "Logs de hoy", "value": logs_hoy},
                {"label": "Logs últimas 24h", "value": logs_24h},
            ],
        },
    ]

    actividad_labels = []
    actividad_values = []
    for offset in range(6, -1, -1):
        dia = hoy - timedelta(days=offset)
        actividad_labels.append(dia.strftime("%d/%m"))
        actividad_values.append(AuditoriaLog.objects.filter(fecha__date=dia).count())

    charts_data = {
        "usuarios_por_rol": {
            "title": "Distribución de usuarios por rol",
            "help": "Permite ver cómo está compuesta la base de usuarios del sistema.",
            "type": "doughnut",
            "labels": ["Administradores", "Coordinadores", "Evaluadores", "Ponentes", "Participantes"],
            "values": [
                _count_users_by_role("ADMIN", "ADMINISTRADOR"),
                _count_users_by_role("COOR", "COORDINADOR"),
                _count_users_by_role("EVAL", "EVALUADOR"),
                _count_users_by_role("PON", "PONENTE"),
                _count_users_by_role("PART", "PARTICIPANTE"),
            ],
            "colors": ["#1d4ed8", "#0f766e", "#7c3aed", "#ea580c", "#2563eb"],
            "total": total_usuarios,
            "unit": "usuarios",
        },
        "eventos_por_estado": {
            "title": "Estado actual de eventos",
            "help": "Separa los eventos entre borrador y publicados para facilitar el seguimiento.",
            "type": "doughnut",
            "labels": ["Borrador", "Publicado"],
            "values": [eventos_borrador, eventos_publicados],
            "colors": ["#f59e0b", "#16a34a"],
            "total": total_eventos,
            "unit": "eventos",
        },
        "operacion_intermodular": {
            "title": "Carga operativa por módulo",
            "help": "Compara el volumen operativo entre inscripciones, evaluación, rúbricas, espacios y ponencias.",
            "type": "bar",
            "labels": ["Inscripciones", "Proyectos", "Asignaciones", "Rúbricas", "Espacios", "Ponencias"],
            "values": [
                total_inscripciones,
                total_proyectos_eval,
                total_asignaciones_eval,
                total_rubricas,
                total_espacios,
                total_ponencias,
            ],
            "colors": ["#2563eb", "#7c3aed", "#0f766e", "#ea580c", "#dc2626", "#0891b2"],
            "total": operacion_total,
            "unit": "registros",
        },
        "actividad_ultimos_7_dias": {
            "title": "Actividad auditada de los últimos 7 días",
            "help": "Muestra cuántos movimientos quedaron registrados en la bitácora durante la última semana.",
            "type": "bar",
            "labels": actividad_labels,
            "values": actividad_values,
            "colors": ["#1d4ed8"] * len(actividad_values),
            "total": sum(actividad_values),
            "unit": "movimientos",
        },
    }

    chart_cards = [
        {"key": "usuarios_por_rol", "height": "h-[320px]"},
        {"key": "eventos_por_estado", "height": "h-[320px]"},
        {"key": "operacion_intermodular", "height": "h-[360px]"},
        {"key": "actividad_ultimos_7_dias", "height": "h-[360px]"},
    ]

    return {
        "summary_cards": summary_cards,
        "kpi_groups": kpi_groups,
        "charts_data": charts_data,
        "chart_cards": chart_cards,
        "total_usuarios": total_usuarios,
        "eventos_activos": eventos_publicados,
        "roles_configurados": len(_get_roles_disponibles()),
        "solicitudes_pendientes": 0,
    }


def _safe_parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def _audit_user_label(user) -> str:
    if user is None:
        return "Sistema"
    return user.get_full_name() or getattr(user, "username", "") or getattr(user, "email", "") or "Sistema"


def _normalize_audit_row(log) -> dict:
    modulo = (getattr(log, "modulo", "") or "").strip() or infer_audit_modulo(log.accion)
    accion_tipo = (getattr(log, "accion_tipo", "") or "").strip() or infer_audit_action_type(log.accion)
    resultado = (getattr(log, "resultado", "") or "").strip() or infer_audit_result(log.accion)
    usuario_label = _audit_user_label(getattr(log, "usuario", None))
    usuario_email = getattr(getattr(log, "usuario", None), "email", "") or ""
    detalles = getattr(log, "detalles", None) or {}
    return {
        "obj": log,
        "id": log.id,
        "fecha": log.fecha,
        "usuario_nombre": usuario_label,
        "usuario_email": usuario_email,
        "accion": log.accion,
        "modulo": modulo,
        "accion_tipo": accion_tipo,
        "resultado": resultado,
        "entidad": (getattr(log, "entidad", "") or "").strip(),
        "objeto_id": (getattr(log, "objeto_id", "") or "").strip(),
        "ip_origen": log.ip_origen or "",
        "user_agent": log.user_agent or "",
        "detalles": detalles,
    }


def _get_filtered_audit_rows(request: HttpRequest, *, limit: int | None = 300) -> tuple[list[dict], dict]:
    q = (request.GET.get("q") or "").strip()
    usuario_id = (request.GET.get("usuario") or "").strip()
    modulo_filter = (request.GET.get("modulo") or "").strip().upper()
    accion_filter = (request.GET.get("accion") or "").strip().upper()
    resultado_filter = (request.GET.get("resultado") or "").strip().upper()
    desde = (request.GET.get("desde") or "").strip()
    hasta = (request.GET.get("hasta") or "").strip()

    qs = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")

    if q:
        qs = qs.filter(
            Q(accion__icontains=q)
            | Q(usuario__username__icontains=q)
            | Q(usuario__email__icontains=q)
            | Q(usuario__first_name__icontains=q)
        )

    if usuario_id.isdigit():
        qs = qs.filter(usuario_id=int(usuario_id))

    desde_date = _safe_parse_date(desde)
    hasta_date = _safe_parse_date(hasta)
    if desde_date:
        qs = qs.filter(fecha__date__gte=desde_date)
    if hasta_date:
        qs = qs.filter(fecha__date__lte=hasta_date)

    raw_logs = list(qs[:5000])
    rows = []
    for log in raw_logs:
        row = _normalize_audit_row(log)
        if modulo_filter and row["modulo"] != modulo_filter:
            continue
        if accion_filter and row["accion_tipo"] != accion_filter:
            continue
        if resultado_filter and row["resultado"] != resultado_filter:
            continue
        rows.append(row)

    if limit is not None:
        rows = rows[:limit]

    filtros = {
        "q": q,
        "usuario_id": usuario_id,
        "modulo_filter": modulo_filter,
        "accion_filter": accion_filter,
        "resultado_filter": resultado_filter,
        "desde": desde,
        "hasta": hasta,
        "filters_active": any([q, usuario_id, modulo_filter, accion_filter, resultado_filter, desde, hasta]),
    }
    return rows, filtros


def _build_audit_analytics(rows: list[dict]) -> dict:
    today = timezone.localdate()
    now = timezone.now()
    last7 = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    by_day = Counter(row["fecha"].astimezone(timezone.get_current_timezone()).date() if timezone.is_aware(row["fecha"]) else row["fecha"].date() for row in rows)

    module_counts = Counter(row["modulo"] or "GENERAL" for row in rows)
    action_counts = Counter(f"{row['modulo']} · {row['accion_tipo']}" for row in rows)
    result_counts = Counter(row["resultado"] or "EXITOSO" for row in rows)
    user_counts = Counter(row["usuario_nombre"] for row in rows if row["usuario_nombre"] != "Sistema")

    frequent_changes = []
    total = len(rows) or 1
    for label, count in action_counts.most_common(8):
        frequent_changes.append({
            "label": label,
            "count": count,
            "percent": round((count * 100) / total, 1),
        })

    ultimo = rows[0] if rows else None
    logs_hoy = sum(1 for row in rows if timezone.localtime(row["fecha"]).date() == today)
    logs_24h = sum(1 for row in rows if row["fecha"] >= now - timedelta(hours=24))

    chart_data = {
        "modules": {
            "title": "Movimientos por módulo",
            "help": "Concentra en qué módulo se están generando más operaciones.",
            "labels": list(module_counts.keys()),
            "values": list(module_counts.values()),
        },
        "actions": {
            "title": "Cambios más frecuentes",
            "help": "Acciones recurrentes dentro de la bitácora auditada.",
            "labels": [item[0] for item in action_counts.most_common(8)],
            "values": [item[1] for item in action_counts.most_common(8)],
        },
        "results": {
            "title": "Resultado de operaciones",
            "help": "Compara movimientos exitosos, fallidos y advertencias.",
            "labels": list(result_counts.keys()),
            "values": list(result_counts.values()),
        },
        "days": {
            "title": "Actividad últimos 7 días",
            "help": "Mide la intensidad diaria de movimientos recientes.",
            "labels": [day.strftime("%d/%m") for day in last7],
            "values": [by_day.get(day, 0) for day in last7],
        },
    }

    return {
        "total_logs": len(rows),
        "logs_hoy": logs_hoy,
        "logs_24h": logs_24h,
        "ultimo_log": ultimo,
        "usuarios_activos_auditoria": len(user_counts),
        "modulo_mas_activo": module_counts.most_common(1)[0][0] if module_counts else "Sin datos",
        "accion_mas_frecuente": action_counts.most_common(1)[0][0] if action_counts else "Sin datos",
        "frequent_changes": frequent_changes,
        "chart_data": chart_data,
        "module_options": sorted(module_counts.keys()),
        "action_options": sorted({row["accion_tipo"] for row in rows if row["accion_tipo"]}),
        "result_options": [key for key in ["EXITOSO", "FALLIDO", "ADVERTENCIA"] if result_counts.get(key) or not rows],
    }


# =========================
# Dashboard
# =========================
@admin_required
def dashboard(request: HttpRequest):
    actividad = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:12]
    kpis = _build_dashboard_kpis()

    context = {
        "active": "dashboard",
        "actividad": actividad,
        "roles": _get_roles_disponibles(),
        **kpis,
    }
    return render(request, "administrador/dashboard/index.html", context)


# ==========================================================
# ✅ Crear usuario desde Dashboard (MODAL)  <--- ESTA FALTABA
# ==========================================================
@admin_required
def crear_usuario(request: HttpRequest):
    """
    Crea usuario desde el modal del Dashboard y envía enlace para setear contraseña.
    Requiere que exista la ruta principal:set_password (si no, igual crea el usuario).
    """
    if request.method != "POST":
        return redirect("administrador:dashboard")

    nombre = (request.POST.get("nombre") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    rol = (request.POST.get("rol") or "").strip().upper()

    roles_validos = _get_roles_disponibles()
    if not nombre or not email or rol not in roles_validos:
        messages.error(request, "Datos inválidos. Verifica nombre, correo y rol.")
        return redirect("administrador:dashboard")

    if User.objects.filter(email=email).exists():
        messages.error(request, "Ya existe un usuario con ese correo.")
        return redirect("administrador:dashboard")

    # username base: parte antes del @. Si ya existe, hacemos variante
    base_username = email.split("@")[0]
    username = base_username
    i = 1
    while User.objects.filter(username=username).exists():
        i += 1
        username = f"{base_username}{i}"

    # Crear
    u = User.objects.create(username=username, email=email, is_active=True)

    if hasattr(u, "rol"):
        u.rol = rol
    _set_full_name(u, nombre)

    # No usable password: obliga a set password
    u.set_unusable_password()
    u.save()
    _sync_user_role_group(u)

    _audit(request, f"USUARIOS | CREAR (DASHBOARD) | {u.username} ({u.email}) rol={rol}")

    # Intentar mandar mail con set_password
    try:
        from django.contrib.auth.tokens import default_token_generator

        uidb64 = urlsafe_base64_encode(force_bytes(u.pk))
        token = default_token_generator.make_token(u)
        setpass_path = reverse("principal:set_password", kwargs={"uidb64": uidb64, "token": token})
        setpass_url = request.build_absolute_uri(setpass_path)

        subject = "SIGEP | Establece tu contraseña"
        body = (
            f"Hola {nombre},\n\n"
            f"Se ha creado tu cuenta en SIGEP con el rol: {rol}.\n"
            f"Para establecer tu contraseña, entra al siguiente enlace:\n\n"
            f"{setpass_url}\n\n"
            f"— SIGEP"
        )

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        send_mail(subject, body, from_email, [email], fail_silently=False)
        messages.success(request, f"Usuario creado. Se envió un correo a {email} para establecer contraseña.")
    except Exception:
        # Si aún no tienes implementada la vista principal:set_password o no hay correo configurado
        messages.warning(
            request,
            "Usuario creado, pero no fue posible enviar el correo para establecer contraseña. "
            "Verifica la configuración de correo y/o la ruta principal:set_password."
        )

    return redirect("administrador:dashboard")


# =========================
# Usuarios
# =========================
@admin_required
def usuarios(request: HttpRequest):
    roles = _get_roles_disponibles()

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        user_id = request.POST.get("user_id")

        if not user_id:
            messages.error(request, "Falta user_id.")
            return redirect("administrador:usuarios")

        u = get_object_or_404(User, pk=user_id)

        if accion == "toggle":
            if u.pk == request.user.pk:
                messages.warning(request, "No puedes desactivar tu propia cuenta desde aquí.")
                return redirect("administrador:usuarios")

            if u.is_active and _is_admin_user(u):
                admins_activos = User.objects.filter(is_active=True, rol="ADMIN").count()
                if admins_activos <= 1:
                    messages.error(request, "No puedes desactivar al último administrador activo.")
                    return redirect("administrador:usuarios")

            u.is_active = not u.is_active
            u.save(update_fields=["is_active"])
            _audit(request, f"USUARIOS | TOGGLE | {u.username} activo={u.is_active}")
            messages.success(request, f"Estado actualizado para {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "rol":
            nuevo_rol = (request.POST.get("rol") or "").strip().upper()
            if nuevo_rol not in roles:
                messages.error(request, "Rol inválido.")
                return redirect("administrador:usuarios")

            if u.pk == request.user.pk and nuevo_rol != "ADMIN":
                messages.warning(request, "Por seguridad, no puedes quitarte el rol ADMIN a ti mismo.")
                return redirect("administrador:usuarios")

            u.rol = nuevo_rol
            u.save(update_fields=["rol"])
            _sync_user_role_group(u)
            _audit(request, f"USUARIOS | CAMBIAR_ROL | {u.username} rol={nuevo_rol}")
            messages.success(request, f"Rol actualizado a {nuevo_rol} para {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "editar":
            nombre = (request.POST.get("nombre") or "").strip()
            email = (request.POST.get("email") or "").strip().lower()
            username = (request.POST.get("username") or "").strip()
            nuevo_rol = (request.POST.get("rol") or "").strip().upper()

            if not nombre or not email or not username:
                messages.error(request, "Nombre, correo y username son obligatorios.")
                return redirect("administrador:usuarios")

            if nuevo_rol and nuevo_rol not in roles:
                messages.error(request, "Rol inválido.")
                return redirect("administrador:usuarios")

            if User.objects.filter(email=email).exclude(pk=u.pk).exists():
                messages.error(request, "Ese correo ya está en uso.")
                return redirect("administrador:usuarios")

            if User.objects.filter(username=username).exclude(pk=u.pk).exists():
                messages.error(request, "Ese username ya está en uso.")
                return redirect("administrador:usuarios")

            if u.pk == request.user.pk and nuevo_rol and nuevo_rol != "ADMIN":
                messages.warning(request, "Por seguridad, no puedes quitarte el rol ADMIN a ti mismo.")
                return redirect("administrador:usuarios")

            u.email = email
            u.username = username
            if hasattr(u, "rol") and nuevo_rol:
                u.rol = nuevo_rol
            _set_full_name(u, nombre)

            u.save()
            _sync_user_role_group(u)
            _audit(request, f"USUARIOS | EDITAR | {u.username} ({u.email})")
            messages.success(request, f"Usuario actualizado: {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "eliminar":
            if u.pk == request.user.pk:
                messages.error(request, "No puedes eliminar tu propia cuenta.")
                return redirect("administrador:usuarios")

            if _is_admin_user(u):
                admins_totales = User.objects.filter(rol="ADMIN").count()
                if admins_totales <= 1:
                    messages.error(request, "No puedes eliminar al último administrador del sistema.")
                    return redirect("administrador:usuarios")

            _audit(request, f"USUARIOS | ELIMINAR | {u.username} ({u.email})")
            u.delete()
            messages.success(request, "Usuario eliminado correctamente.")
            return redirect("administrador:usuarios")

        messages.error(request, "Acción no válida.")
        return redirect("administrador:usuarios")

    q = (request.GET.get("q") or "").strip()
    rol_filter = (request.GET.get("rol") or "").strip().upper()
    estado = (request.GET.get("estado") or "").strip().lower()

    usuarios_qs = User.objects.all().order_by("id")

    if q:
        usuarios_qs = usuarios_qs.filter(
            Q(username__icontains=q) |
            Q(email__icontains=q) |
            Q(first_name__icontains=q)
        )

    if rol_filter in roles:
        usuarios_qs = usuarios_qs.filter(rol=rol_filter)

    if estado == "activos":
        usuarios_qs = usuarios_qs.filter(is_active=True)
    elif estado == "inactivos":
        usuarios_qs = usuarios_qs.filter(is_active=False)

    context = {
        "active": "usuarios",
        "usuarios": usuarios_qs,
        "roles": roles,
        "q": q,
        "rol_filter": rol_filter,
        "estado": estado,
    }
    return render(request, "administrador/usuarios/usuarios.html", context)


def _normalize_event_payload(request: HttpRequest):
    errors = []
    titulo = (request.POST.get("titulo") or "").strip()
    descripcion = (request.POST.get("descripcion") or "").strip()
    fecha_raw = (request.POST.get("fecha") or "").strip()
    lugar = (request.POST.get("lugar") or "").strip()
    estado = ((request.POST.get("estado") or "BORRADOR").strip().upper())

    try:
        cupo = int((request.POST.get("cupo") or "0").strip())
    except Exception:
        cupo = -1

    fecha = None
    if not titulo:
        errors.append("El título del evento es obligatorio.")
    if not descripcion:
        errors.append("La descripción del evento es obligatoria.")
    if not lugar:
        errors.append("El lugar del evento es obligatorio.")
    if cupo < 0:
        errors.append("El cupo debe ser un número entero mayor o igual a cero.")
    if estado not in {"BORRADOR", "PUBLICADO", "CERRADO"}:
        estado = "BORRADOR"

    try:
        fecha = datetime.strptime(fecha_raw, "%Y-%m-%d").date()
    except Exception:
        errors.append("La fecha del evento no es válida.")

    return {
        "titulo": titulo,
        "descripcion": descripcion,
        "fecha": fecha,
        "lugar": lugar,
        "cupo": cupo if cupo >= 0 else 0,
        "estado": estado,
    }, errors


# =========================
# Roles y Permisos
# =========================
@admin_required
def roles(request: HttpRequest):
    _ensure_sigep_permission_registry()

    roles_ui = _roles_ui()
    valid_roles = {code for code, _ in roles_ui}
    selected_role = ((request.GET.get("role") or request.POST.get("role") or "COOR").strip().upper())
    if selected_role not in valid_roles:
        selected_role = "COOR"

    q = (request.GET.get("q") or "").strip()
    module_filter = (request.GET.get("module") or "").strip().lower()
    selected_group = Group.objects.get(name=_role_group_name(selected_role))
    ct = _permission_content_type()
    all_codes = _all_permission_codes()
    current_codes = set(selected_group.permissions.filter(content_type=ct).values_list("codename", flat=True))

    if selected_role == "ADMIN" and current_codes != all_codes:
        selected_group.permissions.set(Permission.objects.filter(content_type=ct, codename__in=all_codes))
        current_codes = set(all_codes)

    if request.method == "POST":
        submitted_role = ((request.POST.get("role") or selected_role).strip().upper())
        if submitted_role not in valid_roles:
            messages.error(request, "Rol inválido.")
            return redirect("administrador:roles")

        selected_role = submitted_role
        selected_group = Group.objects.get(name=_role_group_name(selected_role))
        current_codes = set(selected_group.permissions.filter(content_type=ct).values_list("codename", flat=True))

        desired_codes = set(request.POST.getlist("perms")) & all_codes
        if selected_role == "ADMIN":
            desired_codes = set(all_codes)

        added = sorted(desired_codes - current_codes)
        removed = sorted(current_codes - desired_codes)

        selected_group.permissions.set(Permission.objects.filter(content_type=ct, codename__in=desired_codes))
        synced_users = _sync_role_members(selected_role)

        query_args = {"role": selected_role}
        if q:
            query_args["q"] = q
        if module_filter:
            query_args["module"] = module_filter

        if added or removed:
            added_text = ", ".join(added[:8]) if added else "ninguno"
            removed_text = ", ".join(removed[:8]) if removed else "ninguno"
            _audit(
                request,
                f"ROLES | GUARDAR_PERMISOS | rol={selected_role} | total={len(desired_codes)} | "
                f"anadidos={added_text} | removidos={removed_text} | usuarios_sincronizados={synced_users}"
            )
            messages.success(request, f"Permisos actualizados para el rol {_role_label(selected_role)}.")
        else:
            _audit(request, f"ROLES | SIN_CAMBIOS | rol={selected_role} | total={len(desired_codes)}")
            messages.info(request, "No se detectaron cambios en los permisos del rol seleccionado.")

        return redirect(f"{reverse('administrador:roles')}?{urlencode(query_args)}")

    catalog = _filter_permission_catalog(current_codes, q, module_filter)
    module_options = [
        {"slug": slugify(block["module"]), "label": block["module"]}
        for block in _permission_catalog()
    ]

    stats_total_permissions = len(all_codes)
    stats_assigned_permissions = len(current_codes if selected_role != "ADMIN" else all_codes)
    stats_modules_visible = len(catalog)
    stats_users_in_role = User.objects.filter(rol=selected_role).count()

    context = {
        "active": "roles",
        "roles": _get_roles_disponibles(),
        "roles_ui": roles_ui,
        "selected_role": selected_role,
        "selected_role_label": _role_label(selected_role),
        "current_codes": current_codes if selected_role != "ADMIN" else all_codes,
        "catalog": catalog,
        "module_options": module_options,
        "module_filter": module_filter,
        "q": q,
        "history_rows": _role_history_rows(),
        "stats_total_permissions": stats_total_permissions,
        "stats_assigned_permissions": stats_assigned_permissions,
        "stats_modules_visible": stats_modules_visible,
        "stats_users_in_role": stats_users_in_role,
        "is_admin_role": selected_role == "ADMIN",
    }
    return render(request, "administrador/roles/roles_permisos.html", context)


# =========================
# Eventos
# =========================
@admin_required
def eventos(request: HttpRequest):
    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip().lower()
        evento_id = request.POST.get("evento_id")

        if accion in {"crear", "editar"}:
            payload, errors = _normalize_event_payload(request)
            if errors:
                for err in errors:
                    messages.error(request, err)
                return redirect("administrador:eventos")

            duplicate_qs = Evento.objects.filter(titulo__iexact=payload["titulo"], fecha=payload["fecha"])
            if accion == "editar" and evento_id:
                duplicate_qs = duplicate_qs.exclude(pk=evento_id)
            if duplicate_qs.exists():
                messages.error(request, "Ya existe un evento con el mismo título y fecha.")
                return redirect("administrador:eventos")

            if accion == "crear":
                evento = Evento(
                    titulo=payload["titulo"],
                    descripcion=payload["descripcion"],
                    fecha=payload["fecha"],
                    lugar=payload["lugar"],
                    cupo=payload["cupo"],
                    estado=payload["estado"],
                )
                if hasattr(evento, "creado_por_id"):
                    evento.creado_por = request.user
                evento.save()
                _audit(request, f"EVENTOS | CREAR | {evento.titulo} | estado={evento.estado}")
                messages.success(request, "Evento creado correctamente.")
                return redirect("administrador:eventos")

            evento = get_object_or_404(Evento, pk=evento_id)
            evento.titulo = payload["titulo"]
            evento.descripcion = payload["descripcion"]
            evento.fecha = payload["fecha"]
            evento.lugar = payload["lugar"]
            evento.cupo = payload["cupo"]
            evento.estado = payload["estado"]
            evento.save()
            _audit(request, f"EVENTOS | EDITAR | {evento.titulo} | estado={evento.estado}")
            messages.success(request, "Evento actualizado correctamente.")
            return redirect("administrador:eventos")

        if accion == "toggle_publicacion":
            evento = get_object_or_404(Evento, pk=evento_id)
            evento.estado = "BORRADOR" if evento.estado == "PUBLICADO" else "PUBLICADO"
            evento.save(update_fields=["estado", "actualizado_en"])
            _audit(request, f"EVENTOS | CAMBIAR_ESTADO | {evento.titulo} | estado={evento.estado}")
            messages.success(request, f"El evento ahora está en estado {evento.estado}.")
            return redirect("administrador:eventos")

        if accion == "eliminar":
            evento = get_object_or_404(Evento, pk=evento_id)
            titulo = evento.titulo
            try:
                evento.delete()
                _audit(request, f"EVENTOS | ELIMINAR | {titulo}")
                messages.success(request, "Evento eliminado correctamente.")
            except Exception:
                messages.error(request, "No fue posible eliminar el evento porque tiene información relacionada.")
            return redirect("administrador:eventos")

        messages.error(request, "Acción no válida para eventos.")
        return redirect("administrador:eventos")

    q = (request.GET.get("q") or "").strip()
    estado = (request.GET.get("estado") or "").strip().upper()

    qs = Evento.objects.all().order_by("-fecha", "-id")
    if q:
        qs = qs.filter(Q(titulo__icontains=q) | Q(descripcion__icontains=q) | Q(lugar__icontains=q))
    if estado in ("BORRADOR", "PUBLICADO", "CERRADO"):
        qs = qs.filter(estado=estado)

    return render(
        request,
        "administrador/eventos/eventos.html",
        {"active": "eventos", "eventos": qs, "q": q, "estado": estado},
    )


# =========================
# Auditoría
# =========================
@admin_required
def auditoria(request: HttpRequest):
    rows, filtros = _get_filtered_audit_rows(request, limit=300)
    analytics = _build_audit_analytics(rows)
    usuarios_filtro = User.objects.order_by("first_name", "email")[:300]

    context = {
        "active": "auditoria",
        "logs": rows,
        "usuarios_filtro": usuarios_filtro,
        **filtros,
        **analytics,
    }
    return render(request, "administrador/auditoria/auditoria.html", context)


@admin_required
def auditoria_export_csv(request: HttpRequest):
    rows, filtros = _get_filtered_audit_rows(request, limit=None)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="auditoria_sigep.csv"'
    writer = csv.writer(response)

    writer.writerow([
        "id", "fecha", "usuario", "email", "modulo", "accion_tipo", "resultado",
        "accion", "entidad", "objeto_id", "ip_origen", "user_agent"
    ])
    for row in rows:
        writer.writerow([
            row["id"],
            timezone.localtime(row["fecha"]).strftime("%Y-%m-%d %H:%M:%S"),
            row["usuario_nombre"],
            row["usuario_email"],
            row["modulo"],
            row["accion_tipo"],
            row["resultado"],
            row["accion"],
            row["entidad"],
            row["objeto_id"],
            row["ip_origen"],
            row["user_agent"],
        ])

    _audit(request, "AUDITORIA | EXPORTAR_CSV | auditoria_sigep.csv", modulo="AUDITORIA", accion_tipo="EXPORTAR", entidad="AuditoriaLog", resultado="EXITOSO", detalles=filtros)
    return response


# =========================
# Reporte general CSV
# =========================
@admin_required
def reporte_csv(request: HttpRequest):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="reporte_sigep.csv"'
    writer = csv.writer(response)

    writer.writerow(["SIGEP | REPORTE GENERAL"])
    writer.writerow(["Generado", timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    writer.writerow(["== USUARIOS =="])
    writer.writerow(["id", "username", "email", "rol", "activo", "date_joined"])
    for u in User.objects.all().order_by("id"):
        writer.writerow([
            u.id,
            getattr(u, "username", ""),
            getattr(u, "email", ""),
            getattr(u, "rol", ""),
            "SI" if getattr(u, "is_active", False) else "NO",
            getattr(u, "date_joined", ""),
        ])
    writer.writerow([])

    writer.writerow(["== EVENTOS =="])
    writer.writerow(["id", "titulo", "fecha", "lugar", "cupo", "estado", "creado_en"])
    for e in Evento.objects.all().order_by("id"):
        writer.writerow([
            e.id,
            e.titulo,
            e.fecha,
            e.lugar,
            e.cupo,
            e.estado,
            timezone.localtime(e.creado_en).strftime("%Y-%m-%d %H:%M:%S") if getattr(e, "creado_en", None) else "",
        ])
    writer.writerow([])

    writer.writerow(["== AUDITORÍA (últimos 500) =="])
    writer.writerow(["id", "fecha", "usuario", "email", "accion", "ip_origen"])
    logs = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:500]
    for l in logs:
        writer.writerow([
            l.id,
            timezone.localtime(l.fecha).strftime("%Y-%m-%d %H:%M:%S"),
            (l.usuario.get_full_name() if l.usuario else "Sistema"),
            (l.usuario.email if l.usuario else ""),
            l.accion,
            l.ip_origen or "",
        ])

    _audit(request, "REPORTE | EXPORTAR_CSV | reporte_sigep.csv")
    return response


# =========================
# Configuración
# =========================
@admin_required
def configuracion(request: HttpRequest):
    allowed_keys = {
        "sistema_nombre": {"default": "SIGEP"},
        "sistema_correo_soporte": {"default": ""},
        "sistema_telefono_soporte": {"default": ""},

        "seguridad_max_intentos_login": {"default": "5"},
        "seguridad_bloqueo_minutos": {"default": "15"},
        "seguridad_requiere_2fa": {"default": "0"},

        "notif_email_habilitado": {"default": "1"},
        "notif_email_remitente": {"default": getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""},
        "notif_email_asunto_base": {"default": "SIGEP | Notificación"},

        "mantenimiento_modo": {"default": "0"},
        "mantenimiento_mensaje": {"default": "Sistema en mantenimiento. Intenta más tarde."},
    }

    boolean_keys = {"seguridad_requiere_2fa", "notif_email_habilitado", "mantenimiento_modo"}

    def get_config_value(key: str) -> str:
        obj = ConfiguracionSistema.objects.filter(clave=key).first()
        if obj and obj.valor is not None:
            return obj.valor
        return allowed_keys[key]["default"]

    def set_config_value(key: str, value: str) -> None:
        ConfiguracionSistema.objects.update_or_create(
            clave=key,
            defaults={"valor": value, "actualizado_por": request.user},
        )

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        if accion == "cancelar":
            return redirect("administrador:configuracion")

        payload = {}
        for key in allowed_keys.keys():
            if key in boolean_keys:
                payload[key] = "1" if request.POST.get(key) in ("on", "1", "true", "True") else "0"
            else:
                payload[key] = (request.POST.get(key) or "").strip()

        def _to_int(s: str, fallback: int) -> int:
            try:
                return int(s)
            except Exception:
                return fallback

        payload["seguridad_max_intentos_login"] = str(max(1, _to_int(payload["seguridad_max_intentos_login"], 5)))
        payload["seguridad_bloqueo_minutos"] = str(max(1, _to_int(payload["seguridad_bloqueo_minutos"], 15)))

        with transaction.atomic():
            for k, v in payload.items():
                set_config_value(k, v)

        _audit(request, "CONFIGURACIÓN | GUARDAR | Se actualizaron parámetros del sistema")
        messages.success(request, "Configuración guardada correctamente.")
        return redirect("administrador:configuracion")

    values = {k: get_config_value(k) for k in allowed_keys.keys()}
    bools = {k: (values[k] == "1") for k in boolean_keys}

    historial = AuditoriaLog.objects.select_related("usuario").filter(
        accion__icontains="CONFIGURACIÓN"
    ).order_by("-fecha")[:12]

    context = {
        "active": "configuracion",
        "cfg": values,
        "cfg_bool": bools,
        "historial": historial,
        "usuario_actual": request.user,
        "rol_label": request.user.get_rol_display() if hasattr(request.user, "get_rol_display") else getattr(request.user, "rol", "Administrador"),
    }
    return render(request, "administrador/configuracion/configuracion.html", context)



# =========================
# Perfil admin: username / foto
# =========================
@admin_required
def perfil_actualizar(request: HttpRequest):
    if request.method != "POST":
        return redirect("administrador:configuracion")

    accion = (request.POST.get("accion") or "actualizar_perfil").strip()

    if accion not in {"actualizar_perfil", "cambiar_username", "subir_foto"}:
        messages.error(request, "Acción inválida.")
        return redirect("administrador:configuracion")

    allowed_content_types = {"image/png", "image/jpeg"}
    allowed_exts = {".png", ".jpg", ".jpeg"}
    max_size = 5 * 1024 * 1024

    username = (request.POST.get("username") or request.user.username or "").strip()
    email = (request.POST.get("email") or getattr(request.user, "email", "") or "").strip().lower()
    first_name = (request.POST.get("first_name") or getattr(request.user, "first_name", "") or "").strip()
    last_name = (request.POST.get("last_name") or getattr(request.user, "last_name", "") or "").strip()
    telefono = (request.POST.get("telefono") or getattr(request.user, "telefono", "") or "").strip()
    foto = request.FILES.get("foto")

    if accion == "cambiar_username":
        email = getattr(request.user, "email", "") or email
    if accion == "subir_foto":
        username = request.user.username
        email = getattr(request.user, "email", "") or email
        first_name = getattr(request.user, "first_name", "")
        last_name = getattr(request.user, "last_name", "")
        telefono = getattr(request.user, "telefono", "") if hasattr(request.user, "telefono") else telefono

    if not username:
        messages.error(request, "El nombre de usuario no puede ir vacío.")
        return redirect("administrador:configuracion")

    if User.objects.filter(username__iexact=username).exclude(pk=request.user.pk).exists():
        messages.error(request, "Ese nombre de usuario ya está en uso.")
        return redirect("administrador:configuracion")

    if not email:
        messages.error(request, "El correo electrónico es obligatorio.")
        return redirect("administrador:configuracion")

    if hasattr(User, "email") and User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
        messages.error(request, "Ese correo electrónico ya está registrado.")
        return redirect("administrador:configuracion")

    update_fields = []

    if request.user.username != username:
        request.user.username = username
        update_fields.append("username")

    if getattr(request.user, "email", "") != email:
        request.user.email = email
        update_fields.append("email")

    if getattr(request.user, "first_name", "") != first_name:
        request.user.first_name = first_name
        update_fields.append("first_name")

    if getattr(request.user, "last_name", "") != last_name:
        request.user.last_name = last_name
        update_fields.append("last_name")

    if hasattr(request.user, "telefono") and getattr(request.user, "telefono", "") != telefono:
        request.user.telefono = telefono
        update_fields.append("telefono")

    if foto:
        ext = os.path.splitext(foto.name)[1].lower()
        content_type = getattr(foto, "content_type", "") or ""
        if ext not in allowed_exts or content_type not in allowed_content_types:
            messages.error(request, "La foto debe estar en formato PNG o JPG/JPEG.")
            return redirect("administrador:configuracion")
        if foto.size > max_size:
            messages.error(request, "La foto no debe superar los 5 MB.")
            return redirect("administrador:configuracion")
        if not hasattr(request.user, "foto"):
            messages.error(request, "Tu modelo de usuario aún no tiene el campo 'foto'.")
            return redirect("administrador:configuracion")

        request.user.foto = foto
        update_fields.append("foto")

    if not update_fields:
        messages.info(request, "No se detectaron cambios para guardar.")
        return redirect("administrador:configuracion")

    request.user.save(update_fields=sorted(set(update_fields)))

    acciones = []
    if "username" in update_fields:
        acciones.append("username")
    if "email" in update_fields:
        acciones.append("correo")
    if "first_name" in update_fields or "last_name" in update_fields:
        acciones.append("nombre")
    if "telefono" in update_fields:
        acciones.append("teléfono")
    if "foto" in update_fields:
        acciones.append("foto")

    _audit(request, f"PERFIL | ACTUALIZAR | Campos: {', '.join(acciones)}")
    messages.success(request, "Datos del perfil actualizados correctamente.")
    return redirect("administrador:configuracion")


# =========================
# Perfil admin: password
# =========================
@admin_required
def perfil_cambiar_password(request: HttpRequest):
    if request.method != "POST":
        return redirect("administrador:configuracion")

    current = request.POST.get("current_password") or ""
    p1 = request.POST.get("new_password") or ""
    p2 = request.POST.get("confirm_password") or ""

    if not request.user.check_password(current):
        messages.error(request, "Tu contraseña actual es incorrecta.")
        return redirect("administrador:configuracion")

    if p1 != p2:
        messages.error(request, "La nueva contraseña y su confirmación no coinciden.")
        return redirect("administrador:configuracion")

    try:
        validate_password(p1, user=request.user)
    except ValidationError as e:
        messages.error(request, "Contraseña no válida: " + " ".join(e.messages))
        return redirect("administrador:configuracion")

    request.user.set_password(p1)
    request.user.save()
    update_session_auth_hash(request, request.user)

    _audit(request, "PERFIL | CAMBIAR_PASSWORD")
    messages.success(request, "Contraseña actualizada correctamente.")
    return redirect("administrador:configuracion")


# =========================
# Salir
# =========================
@login_required
def salir(request: HttpRequest):
    _audit(request, "AUTENTICACION | LOGOUT", modulo="AUTENTICACION", accion_tipo="LOGOUT", entidad="Sesion", resultado="EXITOSO")
    logout(request)
    return redirect("principal:login")