from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import (
    PerfilUsuario,
    EvaluacionAsignacion,
    EvaluacionEntrega,
    EvaluacionRespuestaCriterio,
    Rubrica as RubricaLocal,
)


def _get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _entrega_estado_label(entrega: EvaluacionEntrega | None) -> str:
    if not entrega:
        return "pendiente"
    if entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA:
        return "enviada"
    return "en_revision"



def _ponencia_para_proyecto(proyecto):
    """
    Intenta resolver la ponencia real enlazada al objeto puente EvaluacionProyecto.
    Este es el punto de comunicación con el módulo de ponente.
    """
    Ponencia = _get_model("ponente", "Ponencia")
    if not Ponencia or not getattr(proyecto, "pk", None):
        return None

    try:
        return (
            Ponencia.objects
            .select_related("evento", "ponente")
            .filter(evaluacion_proyecto_id=proyecto.id)
            .first()
        )
    except Exception:
        return None



def _responsable_display(proyecto, ponencia=None) -> str:
    if ponencia is not None:
        ponente = getattr(ponencia, "ponente", None)
        if ponente is not None:
            full_name = f"{getattr(ponente, 'first_name', '')} {getattr(ponente, 'last_name', '')}".strip()
            if full_name:
                return full_name
            username = getattr(ponente, "username", "")
            if username:
                return username
            return str(ponente)

    raw = getattr(proyecto, "ponente", None) or getattr(proyecto, "autor", None) or ""
    return str(raw).strip() or "—"



def _tipo_display(proyecto, ponencia=None) -> str:
    if ponencia is not None:
        return "Ponencia"
    raw = getattr(proyecto, "tipo", None)
    return str(raw).strip() if raw else "Proyecto"



def _evento_display(proyecto, ponencia=None) -> str:
    evento = getattr(ponencia, "evento", None) if ponencia is not None else getattr(proyecto, "evento", None)
    if evento is None:
        return "—"
    return getattr(evento, "nombre", None) or getattr(evento, "titulo", None) or str(evento)



def _rubrica_para_proyecto(proyecto):
    """
    Busca primero la rúbrica generada desde el módulo coordinador.
    Si no existe, cae en la rúbrica local del evaluador como compatibilidad.
    Soporta dos vínculos:
      - por proyecto puente (EvaluacionProyecto)
      - por ponencia real enlazada a ese proyecto puente
    """
    ponencia = _ponencia_para_proyecto(proyecto)

    modelos = []
    RubricaCoord = _get_model("coordinador", "Rubrica")
    if RubricaCoord is not None:
        modelos.append(RubricaCoord)
    modelos.append(RubricaLocal)

    for RubricaModel in modelos:
        try:
            field_names = {f.name for f in RubricaModel._meta.get_fields()}
        except Exception:
            field_names = set()

        filtros = Q()
        activo = False
        if "proyecto" in field_names:
            filtros |= Q(proyecto=proyecto)
            activo = True
        if ponencia is not None and "ponencia" in field_names:
            filtros |= Q(ponencia=ponencia)
            activo = True
        if not activo:
            continue

        qs = RubricaModel.objects.filter(filtros).order_by("-actualizado_en", "-id")
        try:
            qs = qs.prefetch_related("criterios")
        except Exception:
            pass

        if hasattr(RubricaModel, "ESTADO_ACTIVA") and "estado" in field_names:
            rubrica = qs.filter(estado=RubricaModel.ESTADO_ACTIVA).first() or qs.first()
        else:
            rubrica = qs.first()

        if rubrica is not None:
            return rubrica, ponencia

    return None, ponencia



def _criterios_para_rubrica(rubrica):
    if rubrica is None:
        return []
    try:
        return list(rubrica.criterios.all().order_by("orden", "id"))
    except Exception:
        return list(rubrica.criterios.all()) if hasattr(rubrica, "criterios") else []



def _respuestas_maps(entrega: EvaluacionEntrega):
    respuestas = (
        EvaluacionRespuestaCriterio.objects
        .filter(entrega=entrega)
        .select_related("criterio")
        .order_by("criterio__orden", "criterio_id")
    )
    valores = {r.criterio_id: str(r.valor) for r in respuestas}
    observaciones = {r.criterio_id: r.observacion for r in respuestas}
    return valores, observaciones



def _inyectar_respuestas_en_criterios(criterios, valores, observaciones):
    for criterio in criterios:
        criterio.valor_actual = valores.get(criterio.id, "")
        criterio.observacion_actual = observaciones.get(criterio.id, "")



def _calcular_total(valores: list[int]) -> Decimal:
    if not valores:
        return Decimal("0.0")
    return Decimal(str(sum(valores))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)



def _decorar_proyecto_para_vista(proyecto, entrega=None, rubrica=None, ponencia=None):
    proyecto.estado = _entrega_estado_label(entrega)
    proyecto.tipo_display = _tipo_display(proyecto, ponencia)
    proyecto.responsable_display = _responsable_display(proyecto, ponencia)
    proyecto.evento_display = _evento_display(proyecto, ponencia)
    proyecto.rubrica_asignada = rubrica is not None
    proyecto.rubrica_titulo = getattr(rubrica, "titulo", "") if rubrica is not None else ""
    proyecto.ponencia_relacionada = ponencia
    return proyecto



def _display_space(espacio) -> str:
    if espacio is None:
        return "—"
    nombre = getattr(espacio, "nombre", None)
    if nombre:
        return str(nombre)
    texto = str(espacio).strip()
    return texto or "—"



def _guess_mode(espacio_texto: str) -> str:
    raw = (espacio_texto or "").lower()
    return "Virtual" if any(token in raw for token in ["virtual", "zoom", "meet", "teams", "enlace"]) else "Presencial"



def _build_schedule_row(asignacion, *, rubrica=None, ponencia=None, entrega=None):
    proyecto = asignacion.proyecto
    tipo = _tipo_display(proyecto, ponencia)
    evento_text = _evento_display(proyecto, ponencia)
    responsable = _responsable_display(proyecto, ponencia)

    titulo = (
        getattr(ponencia, "titulo", None)
        or getattr(proyecto, "titulo", None)
        or getattr(proyecto, "nombre", None)
        or f"Registro #{proyecto.id}"
    )
    codigo = (
        getattr(ponencia, "folio", None)
        or getattr(proyecto, "codigo", None)
        or getattr(proyecto, "id", None)
        or "—"
    )

    fecha = getattr(ponencia, "fecha_programada", None)
    if fecha is None:
        evento = getattr(ponencia, "evento", None) if ponencia is not None else getattr(proyecto, "evento", None)
        fecha = getattr(evento, "fecha", None)

    hora_inicio = getattr(ponencia, "hora_inicio", None) or getattr(proyecto, "inicio", None)
    hora_fin = getattr(ponencia, "hora_fin", None) or getattr(proyecto, "fin", None)

    espacio_raw = getattr(ponencia, "espacio_asignado", None)
    if not espacio_raw:
        espacio_raw = getattr(proyecto, "lugar", None) or getattr(proyecto, "espacio", None)
    espacio = _display_space(espacio_raw)

    estado_programacion = getattr(ponencia, "estado_programacion", None)
    estado_eval = _entrega_estado_label(entrega)
    if estado_eval == "enviada":
        estado = "Evaluación enviada"
    elif estado_programacion:
        estado = str(estado_programacion).replace("_", " ").title()
    elif hora_inicio and hora_fin and espacio != "—":
        estado = "Programado"
    else:
        estado = "Pendiente de programación"

    notas = []
    if rubrica is not None:
        notas.append(f"Rúbrica: {getattr(rubrica, 'titulo', 'Asignada')}")
    if estado_eval == "en_revision":
        notas.append("Tienes un borrador de evaluación guardado.")
    elif estado_eval == "enviada":
        notas.append("La evaluación ya fue enviada.")
    elif rubrica is None:
        notas.append("Aún no se ha asignado una rúbrica para este registro.")

    if espacio == "—" or not hora_inicio or not hora_fin:
        notas.append("La coordinación aún no completa la programación final.")

    return {
        "id": asignacion.id,
        "tipo": tipo,
        "evento": evento_text,
        "titulo": titulo,
        "codigo": codigo,
        "responsable": responsable,
        "fecha": fecha,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "espacio": espacio,
        "modo": _guess_mode(espacio),
        "estado": estado,
        "notas": " ".join(notas) if notas else "Sin observaciones.",
        "rubrica": getattr(rubrica, "titulo", "") if rubrica is not None else "",
        "rubrica_asignada": rubrica is not None,
        "proyecto": proyecto,
        "asignacion": asignacion,
        "ponencia": ponencia,
    }


@login_required
def panel(request):
    asignaciones = list(
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio")
    )

    for asignacion in asignaciones:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)

    entregas_enviadas = (
        EvaluacionEntrega.objects
        .select_related("asignacion", "asignacion__proyecto")
        .filter(asignacion__evaluador=request.user, estado=EvaluacionEntrega.ESTADO_ENVIADA)
        .order_by("-fecha_envio")
    )

    total_asignadas = len(asignaciones)
    total_enviadas = entregas_enviadas.count()
    total_pendientes = max(total_asignadas - total_enviadas, 0)

    return render(request, "dashboard/panel.html", {
        "active": "panel",
        "asignaciones": asignaciones[:5],
        "entregas": entregas_enviadas[:5],
        "kpi_total_asignadas": total_asignadas,
        "kpi_total_enviadas": total_enviadas,
        "kpi_total_pendientes": total_pendientes,
    })


@login_required
def proyectos_asignados(request):
    asignaciones = list(
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("-creado_en")
    )

    proyectos = []
    for asignacion in asignaciones:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)
        proyectos.append(proyecto)

    return render(request, "asignados/proyectos_asignados.html", {
        "active": "proyectos",
        "proyectos": proyectos,
        "total": len(proyectos),
        "page_start": 1 if proyectos else 0,
        "page_end": len(proyectos),
        "has_prev": False,
        "has_next": False,
    })


@login_required
def formulario(request, proyecto_id: int):
    asignacion = get_object_or_404(
        EvaluacionAsignacion.objects.select_related("proyecto"),
        evaluador=request.user,
        proyecto_id=proyecto_id,
    )
    proyecto = asignacion.proyecto
    entrega, _ = EvaluacionEntrega.objects.get_or_create(asignacion=asignacion)

    rubrica, ponencia = _rubrica_para_proyecto(proyecto)
    criterios = _criterios_para_rubrica(rubrica)
    valores, observaciones = _respuestas_maps(entrega)
    _inyectar_respuestas_en_criterios(criterios, valores, observaciones)

    tipo_display = _tipo_display(proyecto, ponencia)
    responsable_display = _responsable_display(proyecto, ponencia)
    evento_display = _evento_display(proyecto, ponencia)

    if request.method == "POST":
        if entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA:
            messages.warning(request, "Esta evaluación ya fue enviada y no puede modificarse.")
            return redirect("evaluador:formulario", proyecto_id=proyecto.id)

        if not rubrica or not criterios:
            messages.error(request, "No hay una rúbrica asignada por coordinación para este registro evaluable.")
            return redirect("evaluador:proyectos")

        accion = request.POST.get("accion", "guardar").strip().lower()
        observaciones_generales = request.POST.get("observaciones_generales", "").strip()

        capturados: list[int] = []
        faltantes = False

        for criterio in criterios:
            raw = (request.POST.get(f"criterio_{criterio.id}") or "").strip()
            obs = request.POST.get(f"obs_{criterio.id}", "").strip()

            if not raw:
                if accion == "enviar":
                    faltantes = True
                continue

            try:
                valor = int(raw)
            except Exception:
                if accion == "enviar":
                    faltantes = True
                continue

            if valor < 1 or valor > 5:
                if accion == "enviar":
                    faltantes = True
                continue

            EvaluacionRespuestaCriterio.objects.update_or_create(
                entrega=entrega,
                criterio_id=criterio.id,
                defaults={"valor": valor, "observacion": obs},
            )
            capturados.append(valor)

        criterio_ids = [c.id for c in criterios]
        EvaluacionRespuestaCriterio.objects.filter(entrega=entrega).exclude(criterio_id__in=criterio_ids).delete()

        if accion == "enviar" and faltantes:
            messages.error(request, "Debes calificar todos los criterios antes de enviar la evaluación.")
            valores, observaciones = _respuestas_maps(entrega)
            _inyectar_respuestas_en_criterios(criterios, valores, observaciones)
            return render(request, "evaluacion/formulario.html", {
                "active": "formulario",
                "proyecto": proyecto,
                "ponencia": ponencia,
                "asignacion": asignacion,
                "entrega": entrega,
                "rubrica": rubrica,
                "criterios": criterios,
                "obs_general": observaciones_generales,
                "solo_lectura": False,
                "total_maximo": len(criterios) * 5,
                "tipo_display": tipo_display,
                "responsable_display": responsable_display,
                "evento_display": evento_display,
            })

        entrega.calificacion = _calcular_total(capturados)
        entrega.observaciones_generales = observaciones_generales

        if accion == "enviar":
            entrega.estado = EvaluacionEntrega.ESTADO_ENVIADA
            entrega.fecha_envio = timezone.now()
            entrega.save(update_fields=["calificacion", "observaciones_generales", "estado", "fecha_envio", "actualizado_en"])
            messages.success(request, "Evaluación enviada correctamente.")
            return redirect("evaluador:historial")

        entrega.estado = EvaluacionEntrega.ESTADO_BORRADOR
        entrega.save(update_fields=["calificacion", "observaciones_generales", "estado", "actualizado_en"])
        messages.success(request, "Borrador guardado correctamente.")
        return redirect("evaluador:formulario", proyecto_id=proyecto.id)

    return render(request, "evaluacion/formulario.html", {
        "active": "formulario",
        "proyecto": proyecto,
        "ponencia": ponencia,
        "asignacion": asignacion,
        "entrega": entrega,
        "rubrica": rubrica,
        "criterios": criterios,
        "obs_general": entrega.observaciones_generales or "",
        "solo_lectura": entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA,
        "total_maximo": len(criterios) * 5,
        "tipo_display": tipo_display,
        "responsable_display": responsable_display,
        "evento_display": evento_display,
    })


@login_required
def mi_horario(request):
    asignaciones = list(
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio", "proyecto__fin", "id")
    )

    rows = []
    for asignacion in asignaciones:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)
        rows.append(_build_schedule_row(asignacion, rubrica=rubrica, ponencia=ponencia, entrega=entrega))

    total = len(rows)
    pendientes_programacion = sum(1 for row in rows if row["estado"] == "Pendiente de programación")
    con_rubrica = sum(1 for row in rows if row["rubrica_asignada"])
    programadas = total - pendientes_programacion

    return render(request, "horario/horario.html", {
        "active": "horario",
        "asignaciones": rows,
        "page_start": 1 if rows else 0,
        "page_end": total,
        "has_prev": False,
        "has_next": False,
        "kpi_total": total,
        "kpi_programadas": programadas,
        "kpi_con_rubrica": con_rubrica,
        "kpi_pendientes": pendientes_programacion,
    })


@login_required
def historial(request):
    entregas = list(
        EvaluacionEntrega.objects
        .select_related("asignacion", "asignacion__proyecto")
        .filter(asignacion__evaluador=request.user)
        .order_by("-fecha_envio", "-actualizado_en")
    )

    for entrega in entregas:
        proyecto = entrega.asignacion.proyecto
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)

    kpi_total = len(entregas)
    vals = [float(e.calificacion or 0) for e in entregas]
    kpi_promedio = round(sum(vals) / len(vals), 1) if vals else None
    kpi_pendientes = EvaluacionEntrega.objects.filter(
        asignacion__evaluador=request.user,
        estado=EvaluacionEntrega.ESTADO_BORRADOR,
    ).count()

    return render(request, "historial/historial.html", {
        "active": "historial",
        "entregas": entregas[:50],
        "kpi_total": kpi_total,
        "kpi_promedio": kpi_promedio,
        "kpi_pendientes": kpi_pendientes,
    })


@login_required
def configuracion(request):
    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        request.user.first_name = request.POST.get("nombres", "").strip()
        request.user.last_name = request.POST.get("apellidos", "").strip()
        request.user.email = request.POST.get("correo", "").strip()
        request.user.save()

        perfil.institucion = request.POST.get("institucion", "").strip()
        perfil.puesto = request.POST.get("puesto", "").strip()
        perfil.telefono = request.POST.get("telefono", "").strip()
        perfil.bio = request.POST.get("bio", "").strip()

        if "avatar" in request.FILES:
            perfil.avatar = request.FILES["avatar"]
        if "cv" in request.FILES:
            perfil.cv = request.FILES["cv"]

        perfil.save()

        pwd_actual = request.POST.get("password_actual", "")
        pwd_nueva = request.POST.get("password_nueva", "")
        pwd_conf = request.POST.get("password_confirmacion", "")

        if pwd_actual or pwd_nueva or pwd_conf:
            if not (pwd_actual and pwd_nueva and pwd_conf):
                messages.error(request, "Para cambiar la contraseña, completa los 3 campos.")
                return redirect("evaluador:configuracion")
            if pwd_nueva != pwd_conf:
                messages.error(request, "La nueva contraseña y su confirmación no coinciden.")
                return redirect("evaluador:configuracion")
            if not request.user.check_password(pwd_actual):
                messages.error(request, "La contraseña actual es incorrecta.")
                return redirect("evaluador:configuracion")

            request.user.set_password(pwd_nueva)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Contraseña actualizada correctamente.")

        messages.success(request, "Perfil actualizado correctamente.")
        return redirect("evaluador:configuracion")

    class _V:
        def __init__(self, v):
            self.value = v

    cuenta_form = type("CuentaForm", (), {})()
    cuenta_form.nombres = _V(request.user.first_name)
    cuenta_form.apellidos = _V(request.user.last_name)
    cuenta_form.correo = _V(request.user.email)
    cuenta_form.password_actual = _V("")
    cuenta_form.password_nueva = _V("")
    cuenta_form.password_confirmacion = _V("")
    cuenta_form.nombres.errors = []
    cuenta_form.apellidos.errors = []
    cuenta_form.correo.errors = []
    cuenta_form.password_actual.errors = []
    cuenta_form.password_nueva.errors = []
    cuenta_form.password_confirmacion.errors = []

    perfil_form = type("PerfilForm", (), {})()
    perfil_form.institucion = _V(perfil.institucion)
    perfil_form.puesto = _V(perfil.puesto)
    perfil_form.telefono = _V(perfil.telefono)
    perfil_form.bio = _V(perfil.bio)
    perfil_form.avatar = _V("")
    perfil_form.cv = _V("")
    perfil_form.institucion.errors = []
    perfil_form.puesto.errors = []
    perfil_form.telefono.errors = []
    perfil_form.bio.errors = []
    perfil_form.avatar.errors = []
    perfil_form.cv.errors = []

    return render(request, "configuracion/configuracion.html", {
        "active": "configuracion",
        "perfil": perfil,
        "cuenta_form": cuenta_form,
        "perfil_form": perfil_form,
    })
