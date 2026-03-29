from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.apps import apps
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render

from administrador.models import Evento
from evaluador.models import EvaluacionEntrega, EvaluacionProyecto


def _safe_event_name(evento: Evento | None) -> str:
    if evento is None:
        return "Evento"
    return getattr(evento, "nombre", "") or getattr(evento, "titulo", "") or "Evento"


def _published_events_qs():
    try:
        return Evento.objects.filter(estado="PUBLICADO").order_by("-fecha", "-id")
    except Exception:
        return Evento.objects.all().order_by("-fecha", "-id")


def _resolve_evento(request: HttpRequest) -> Evento | None:
    eventos = _published_events_qs()
    evento_id = (request.GET.get("evento") or "").strip()
    if evento_id.isdigit():
        selected = eventos.filter(pk=int(evento_id)).first()
        if selected is not None:
            return selected
    return eventos.first()


def _resolve_related_source(ev_proyecto: EvaluacionProyecto) -> dict[str, Any]:
    payload = {
        "tipo": "Proyecto",
        "responsable": ev_proyecto.ponente or "Sin responsable",
        "codigo": f"EV-{ev_proyecto.pk}",
        "categoria": "General",
    }

    try:
        Ponencia = apps.get_model("ponente", "Ponencia")
    except Exception:
        Ponencia = None

    if Ponencia is not None:
        pon = (
            Ponencia.objects.filter(evaluacion_proyecto_id=ev_proyecto.pk)
            .select_related("evento", "ponente")
            .first()
        )
        if pon is not None:
            responsable = ""
            ponente_user = getattr(pon, "ponente", None)
            if ponente_user is not None:
                try:
                    responsable = ponente_user.get_full_name().strip()
                except Exception:
                    responsable = ""
                responsable = responsable or getattr(ponente_user, "username", "") or getattr(ponente_user, "email", "")
            payload.update({
                "tipo": "Ponencia",
                "responsable": responsable or ev_proyecto.ponente or "Ponente",
                "codigo": f"PON-{pon.pk}",
                "categoria": getattr(pon, "area_tematica", "") or "Ponencia",
            })
            return payload

    try:
        ProyectoParticipante = apps.get_model("participante", "ProyectoParticipante")
    except Exception:
        ProyectoParticipante = None

    if ProyectoParticipante is not None:
        proy = (
            ProyectoParticipante.objects.filter(evaluacion_proyecto_id=ev_proyecto.pk)
            .select_related("evento", "participante")
            .first()
        )
        if proy is not None:
            responsable = getattr(proy, "nombre_participante", "") or ""
            participante_user = getattr(proy, "participante", None)
            if not responsable and participante_user is not None:
                try:
                    responsable = participante_user.get_full_name().strip()
                except Exception:
                    responsable = ""
                responsable = responsable or getattr(participante_user, "username", "") or getattr(participante_user, "email", "")
            categoria = ""
            try:
                categoria = proy.get_categoria_display()
            except Exception:
                categoria = getattr(proy, "categoria", "") or ""
            payload.update({
                "tipo": "Proyecto",
                "responsable": responsable or ev_proyecto.ponente or "Participante",
                "codigo": f"PRO-{proy.pk}",
                "categoria": categoria or "Proyecto",
            })
            return payload

    return payload


def _build_ranking_payload(evento: Evento | None) -> dict[str, Any]:
    if evento is None:
        return {
            "evento": None,
            "eventos": [],
            "ranking": [],
            "featured": None,
            "top3": [],
            "kpis": {
                "trabajos": 0,
                "evaluaciones_recibidas": 0,
                "evaluaciones_esperadas": 0,
                "avance_global": 0,
                "calificados": 0,
            },
            "chart_labels": [],
            "chart_values": [],
        }

    proyectos_qs = (
        EvaluacionProyecto.objects.filter(evento=evento)
        .prefetch_related("asignaciones__entrega")
        .order_by("inicio", "fin", "id")
    )

    ranking = []
    total_esperadas = 0
    total_recibidas = 0

    for ev in proyectos_qs:
        source = _resolve_related_source(ev)
        asignaciones = list(ev.asignaciones.all())
        esperadas = len(asignaciones)
        entregas = []
        for asignacion in asignaciones:
            entrega = getattr(asignacion, "entrega", None)
            if entrega and entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA:
                entregas.append(entrega)

        recibidas = len(entregas)
        total_esperadas += esperadas
        total_recibidas += recibidas

        promedio = None
        if entregas:
            promedio_raw = sum(Decimal(str(e.calificacion or 0)) for e in entregas) / Decimal(len(entregas))
            promedio = promedio_raw.quantize(Decimal("0.01"))

        avance = int((recibidas / esperadas) * 100) if esperadas else 0
        ranking.append({
            "id": ev.pk,
            "titulo": ev.titulo,
            "responsable": source["responsable"],
            "tipo": source["tipo"],
            "categoria": source["categoria"],
            "codigo": source["codigo"],
            "promedio": promedio,
            "promedio_display": f"{promedio:.2f}" if promedio is not None else "—",
            "evaluaciones_recibidas": recibidas,
            "evaluaciones_esperadas": esperadas,
            "avance": avance,
            "lugar": ev.lugar or "Por definir",
            "inicio": ev.inicio.strftime("%H:%M") if getattr(ev, "inicio", None) else "—",
            "fin": ev.fin.strftime("%H:%M") if getattr(ev, "fin", None) else "—",
        })

    ranking.sort(
        key=lambda x: (
            x["promedio"] is None,
            -(x["promedio"] or Decimal("0.00")),
            -x["evaluaciones_recibidas"],
            x["titulo"].lower(),
        )
    )

    for idx, item in enumerate(ranking, start=1):
        item["posicion"] = idx

    calificados = sum(1 for item in ranking if item["promedio"] is not None)
    avance_global = int((total_recibidas / total_esperadas) * 100) if total_esperadas else 0
    featured = ranking[0] if ranking else None

    return {
        "evento": {
            "id": evento.id,
            "nombre": _safe_event_name(evento),
            "fecha": evento.fecha,
            "estado": getattr(evento, "estado", ""),
            "lugar": getattr(evento, "lugar", ""),
        },
        "eventos": [
            {"id": e.id, "nombre": _safe_event_name(e), "fecha": getattr(e, "fecha", None)}
            for e in _published_events_qs()
        ],
        "ranking": ranking,
        "featured": featured,
        "top3": ranking[:3],
        "kpis": {
            "trabajos": len(ranking),
            "evaluaciones_recibidas": total_recibidas,
            "evaluaciones_esperadas": total_esperadas,
            "avance_global": avance_global,
            "calificados": calificados,
        },
        "chart_labels": [item["titulo"][:32] for item in ranking[:10]],
        "chart_values": [float(item["promedio"]) if item["promedio"] is not None else 0.0 for item in ranking[:10]],
    }


def index(request: HttpRequest) -> HttpResponse:
    evento = _resolve_evento(request)
    payload = _build_ranking_payload(evento)
    return render(request, "home/index.html", payload)


def ranking_data(request: HttpRequest) -> JsonResponse:
    evento = _resolve_evento(request)
    payload = _build_ranking_payload(evento)
    if payload["evento"] and payload["evento"]["fecha"] is not None:
        payload["evento"]["fecha"] = payload["evento"]["fecha"].isoformat()
    for item in payload["eventos"]:
        if item["fecha"] is not None:
            item["fecha"] = item["fecha"].isoformat()
    return JsonResponse(payload, safe=True)
