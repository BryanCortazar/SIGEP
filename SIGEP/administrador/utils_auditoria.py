from __future__ import annotations

import unicodedata
from typing import Any

from django.http import HttpRequest

from .models import AuditoriaLog


def _normalize_text(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").upper()


def infer_audit_modulo(action_text: str) -> str:
    head = _normalize_text((action_text or "").split("|", 1)[0])
    mapping = {
        "AUTENTICACION": "AUTENTICACION",
        "USUARIOS": "USUARIOS",
        "ROLES": "ROLES",
        "EVENTOS": "EVENTOS",
        "AUDITORIA": "AUDITORIA",
        "REPORTE": "REPORTE",
        "CONFIGURACION": "CONFIGURACION",
        "PERFIL": "PERFIL",
        "PONENCIAS": "PONENCIAS",
        "PONENTE": "PONENCIAS",
        "COORDINADOR": "COORDINADOR",
        "EVALUADORES": "EVALUACION",
        "EVALUACION": "EVALUACION",
        "INSCRIPCIONES": "INSCRIPCIONES",
        "CRONOGRAMA": "CRONOGRAMA",
    }
    return mapping.get(head, head or "GENERAL")


def infer_audit_action_type(action_text: str) -> str:
    parts = [p.strip() for p in (action_text or "").split("|") if p.strip()]
    if len(parts) >= 2:
        return _normalize_text(parts[1])
    return "ACCION"


def infer_audit_result(action_text: str) -> str:
    normalized = _normalize_text(action_text)
    if any(token in normalized for token in ["ERROR", "FALLIDO", "DENEGADO", "INVALID", "NO FUE POSIBLE"]):
        return AuditoriaLog.Resultado.FALLIDO
    if any(token in normalized for token in ["ADVERTENCIA", "WARNING"]):
        return AuditoriaLog.Resultado.ADVERTENCIA
    return AuditoriaLog.Resultado.EXITOSO


def client_ip_from_request(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def registrar_auditoria(
    *,
    request: HttpRequest | None = None,
    usuario=None,
    accion: str = "",
    modulo: str = "",
    accion_tipo: str = "",
    entidad: str = "",
    objeto_id: Any = "",
    resultado: str = "",
    detalles: dict[str, Any] | None = None,
) -> AuditoriaLog | None:
    detalles = dict(detalles or {})

    modulo = _normalize_text(modulo) or infer_audit_modulo(accion)
    accion_tipo = _normalize_text(accion_tipo) or infer_audit_action_type(accion)
    resultado = _normalize_text(resultado) or infer_audit_result(accion)
    entidad = (entidad or "").strip()
    objeto_id = "" if objeto_id is None else str(objeto_id)

    if not accion:
        parts = [modulo or "GENERAL", accion_tipo or "ACCION"]
        if entidad:
            parts.append(entidad)
        if objeto_id:
            parts.append(f"id={objeto_id}")
        accion = " | ".join(parts)

    if request is not None:
        detalles.setdefault("path", getattr(request, "path", ""))
        detalles.setdefault("method", getattr(request, "method", ""))

    actor = usuario
    if actor is None and request is not None and getattr(request, "user", None) is not None:
        actor = request.user if request.user.is_authenticated else None

    try:
        return AuditoriaLog.objects.create(
            usuario=actor,
            accion=(accion or "")[:255],
            modulo=(modulo or "")[:80],
            accion_tipo=(accion_tipo or "")[:80],
            entidad=(entidad or "")[:120],
            objeto_id=(objeto_id or "")[:60],
            resultado=resultado if resultado in dict(AuditoriaLog.Resultado.choices) else AuditoriaLog.Resultado.EXITOSO,
            detalles=detalles,
            ip_origen=client_ip_from_request(request),
            user_agent=((request.META.get("HTTP_USER_AGENT") or "")[:2000] if request is not None else ""),
        )
    except Exception:
        return None
