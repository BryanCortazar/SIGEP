from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import EvaluadorCuentaForm, EvaluadorPerfilForm
from .models import (
    EvaluacionAsignacion,
    EvaluacionEntrega,
    EvaluacionRespuestaCriterio,
    PerfilUsuario,
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
    Ponencia = _get_model("ponente", "Ponencia")
    if not Ponencia or not getattr(proyecto, "pk", None):
        return None
    try:
        return (
            Ponencia.objects.select_related("evento", "ponente")
            .filter(evaluacion_proyecto_id=proyecto.id)
            .first()
        )
    except Exception:
        return None


def _responsable_display(proyecto, ponencia=None) -> str:
    if ponencia is not None:
        usuario = getattr(ponencia, "ponente", None)
        if usuario is not None:
            full_name = f"{getattr(usuario, 'first_name', '')} {getattr(usuario, 'last_name', '')}".strip()
            return full_name or getattr(usuario, "username", "") or str(usuario)

    raw = (
        getattr(proyecto, "nombre_participante", None)
        or getattr(proyecto, "ponente", None)
        or getattr(proyecto, "autor", None)
        or ""
    )
    return str(raw).strip() or "—"


def _titulo_display(proyecto, ponencia=None) -> str:
    return (
        getattr(ponencia, "titulo", None)
        or getattr(proyecto, "nombre_proyecto", None)
        or getattr(proyecto, "titulo", None)
        or getattr(proyecto, "nombre", None)
        or f"Registro #{getattr(proyecto, 'id', '—')}"
    )


def _codigo_display(proyecto, ponencia=None) -> str:
    return (
        getattr(ponencia, "folio", None)
        or getattr(proyecto, "codigo", None)
        or getattr(proyecto, "folio", None)
        or getattr(proyecto, "clave", None)
        or str(getattr(proyecto, "id", "—"))
    )


def _tipo_display(proyecto, ponencia=None) -> str:
    return "Ponencia" if ponencia is not None else "Proyecto"


def _evento_display(proyecto, ponencia=None) -> str:
    evento = getattr(ponencia, "evento", None) if ponencia is not None else getattr(proyecto, "evento", None)
    if evento is None:
        return "—"
    return getattr(evento, "titulo", None) or getattr(evento, "nombre", None) or str(evento)


def _display_space(espacio) -> str:
    if espacio is None:
        return "—"
    nombre = getattr(espacio, "nombre", None)
    return str(nombre or espacio).strip() or "—"


def _guess_mode(espacio_texto: str) -> str:
    raw = (espacio_texto or "").lower()
    return "Virtual" if any(token in raw for token in ["virtual", "zoom", "meet", "teams", "enlace"]) else "Presencial"


def _rubrica_para_proyecto(proyecto):
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
        EvaluacionRespuestaCriterio.objects.filter(entrega=entrega)
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
        criterio.ponderacion_display = getattr(criterio, "ponderacion", None) or getattr(criterio, "peso", None) or 1


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
    proyecto.titulo_display = _titulo_display(proyecto, ponencia)
    proyecto.codigo_display = _codigo_display(proyecto, ponencia)
    return proyecto


def _fecha_para_asignacion(proyecto, ponencia=None):
    fecha = getattr(ponencia, "fecha_programada", None)
    if fecha is not None:
        return fecha
    evento = getattr(ponencia, "evento", None) if ponencia is not None else getattr(proyecto, "evento", None)
    return getattr(proyecto, "fecha_programada", None) or getattr(evento, "fecha", None)


def _build_schedule_row(asignacion, *, rubrica=None, ponencia=None, entrega=None):
    proyecto = asignacion.proyecto
    titulo = _titulo_display(proyecto, ponencia)
    codigo = _codigo_display(proyecto, ponencia)
    fecha = _fecha_para_asignacion(proyecto, ponencia)
    hora_inicio = getattr(ponencia, "hora_inicio", None) or getattr(proyecto, "hora_inicio", None) or getattr(proyecto, "inicio", None)
    hora_fin = getattr(ponencia, "hora_fin", None) or getattr(proyecto, "hora_fin", None) or getattr(proyecto, "fin", None)
    espacio_raw = getattr(ponencia, "espacio_asignado", None) or getattr(proyecto, "espacio_asignado", None) or getattr(proyecto, "lugar", None) or getattr(proyecto, "espacio", None)
    espacio = _display_space(espacio_raw)
    estado_eval = _entrega_estado_label(entrega)
    if estado_eval == "enviada":
        estado = "Evaluación enviada"
    elif fecha and hora_inicio and hora_fin and espacio != "—":
        estado = "Programado"
    else:
        estado = "Pendiente de programación"

    notas = []
    if rubrica is not None:
        notas.append(f"Rúbrica: {getattr(rubrica, 'titulo', 'Asignada')}")
    else:
        notas.append("Aún no se asigna rúbrica.")
    if estado_eval == "en_revision":
        notas.append("Hay un borrador guardado.")
    elif estado_eval == "enviada":
        notas.append("La evaluación ya fue enviada.")

    return {
        "id": asignacion.id,
        "tipo": _tipo_display(proyecto, ponencia),
        "evento": _evento_display(proyecto, ponencia),
        "titulo": titulo,
        "codigo": codigo,
        "responsable": _responsable_display(proyecto, ponencia),
        "fecha": fecha,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "espacio": espacio,
        "modo": _guess_mode(espacio),
        "estado": estado,
        "notas": " ".join(notas),
        "rubrica": getattr(rubrica, "titulo", "") if rubrica is not None else "",
        "rubrica_asignada": rubrica is not None,
        "proyecto": proyecto,
        "asignacion": asignacion,
        "ponencia": ponencia,
    }


def _get_assignment_or_404(user, proyecto_id):
    return get_object_or_404(
        EvaluacionAsignacion.objects.select_related("proyecto", "evaluador"),
        proyecto_id=proyecto_id,
        evaluador=user,
    )


@login_required
def panel(request):
    asignaciones = list(
        EvaluacionAsignacion.objects.select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio", "proyecto__fin", "-creado_en")
    )

    pendientes = []
    completadas_count = 0
    nuevas_count = 0
    proxima_sesion = None

    hoy = timezone.localdate()
    ahora = timezone.localtime().time()

    for asignacion in asignaciones:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)
        pendientes.append(proyecto)

        if proyecto.estado == "enviada":
            completadas_count += 1
        else:
            nuevas_count += 1

        fecha = _fecha_para_asignacion(proyecto, ponencia)
        hora_inicio = getattr(ponencia, "hora_inicio", None) or getattr(proyecto, "hora_inicio", None) or getattr(proyecto, "inicio", None)
        if fecha and hora_inicio:
            es_futuro = fecha > hoy or (fecha == hoy and hora_inicio >= ahora)
            if es_futuro:
                item = SimpleNamespace(inicio=hora_inicio, lugar=_display_space(getattr(ponencia, "espacio_asignado", None) or getattr(proyecto, "espacio_asignado", None) or getattr(proyecto, "lugar", None)), fecha=fecha)
                if proxima_sesion is None or (item.fecha, item.inicio) < (proxima_sesion.fecha, proxima_sesion.inicio):
                    proxima_sesion = item

    pendientes_count = sum(1 for p in pendientes if p.estado != "enviada")

    return render(
        request,
        "dashboard/panel.html",
        {
            "active": "panel",
            "pendientes_count": pendientes_count,
            "completadas_count": completadas_count,
            "nuevas_count": nuevas_count,
            "proxima_sesion": proxima_sesion,
            "proyectos_pendientes": [p for p in pendientes if p.estado != "enviada"][:6],
        },
    )


@login_required
def proyectos_asignados(request):
    filtro_estado = request.GET.get("estado", "todos")
    asignaciones = list(
        EvaluacionAsignacion.objects.select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio", "proyecto__fin", "-creado_en")
    )

    proyectos = []
    for asignacion in asignaciones:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia)
        proyectos.append(proyecto)

    if filtro_estado == "pendientes":
        proyectos = [p for p in proyectos if p.estado != "enviada"]
    elif filtro_estado == "completadas":
        proyectos = [p for p in proyectos if p.estado == "enviada"]

    total = len(proyectos)
    return render(
        request,
        "asignados/proyectos_asignados.html",
        {
            "active": "proyectos",
            "proyectos": proyectos,
            "filtro_estado": filtro_estado,
            "page_start": 1 if total else 0,
            "page_end": total,
            "has_prev": False,
            "has_next": False,
        },
    )


@login_required
def formulario(request, proyecto_id):
    asignacion = _get_assignment_or_404(request.user, proyecto_id)
    proyecto = asignacion.proyecto
    rubrica, ponencia = _rubrica_para_proyecto(proyecto)
    criterios = _criterios_para_rubrica(rubrica)
    entrega, _ = EvaluacionEntrega.objects.get_or_create(asignacion=asignacion)

    valores_map, observ_map = _respuestas_maps(entrega)
    _inyectar_respuestas_en_criterios(criterios, valores_map, observ_map)

    if request.method == "POST":
        accion = request.POST.get("accion", "guardar")
        valores_numericos: list[int] = []
        incompletos = []

        for criterio in criterios:
            valor_raw = (request.POST.get(f"valor_{criterio.id}") or "").strip()
            observacion = (request.POST.get(f"observacion_{criterio.id}") or "").strip()

            if accion == "enviar" and not valor_raw:
                incompletos.append(getattr(criterio, "nombre", None) or getattr(criterio, "titulo", None) or f"Criterio {criterio.id}")
                continue

            if valor_raw:
                try:
                    valor_int = int(valor_raw)
                except ValueError:
                    valor_int = 0
                valores_numericos.append(valor_int)
                EvaluacionRespuestaCriterio.objects.update_or_create(
                    entrega=entrega,
                    criterio=criterio,
                    defaults={"valor": valor_int, "observacion": observacion},
                )
            else:
                EvaluacionRespuestaCriterio.objects.filter(entrega=entrega, criterio=criterio).delete()

        if accion == "enviar" and incompletos:
            messages.error(request, "Debes capturar todos los criterios antes de enviar la evaluación.")
            valores_map, observ_map = _respuestas_maps(entrega)
            _inyectar_respuestas_en_criterios(criterios, valores_map, observ_map)
        else:
            entrega.observaciones_generales = (request.POST.get("observaciones_generales") or "").strip()
            entrega.calificacion = _calcular_total(valores_numericos)
            if accion == "enviar":
                entrega.estado = EvaluacionEntrega.ESTADO_ENVIADA
                entrega.fecha_envio = timezone.now()
                messages.success(request, "La evaluación fue enviada correctamente.")
            else:
                entrega.estado = EvaluacionEntrega.ESTADO_BORRADOR
                messages.success(request, "Se guardó el borrador de la evaluación.")
            entrega.save()
            return redirect("evaluador:proyectos")

    valores_map, observ_map = _respuestas_maps(entrega)
    _inyectar_respuestas_en_criterios(criterios, valores_map, observ_map)

    return render(
        request,
        "evaluacion/formulario.html",
        {
            "active": "formulario",
            "asignacion": asignacion,
            "proyecto": _decorar_proyecto_para_vista(proyecto, entrega=entrega, rubrica=rubrica, ponencia=ponencia),
            "ponencia": ponencia,
            "rubrica": rubrica,
            "criterios": criterios,
            "entrega": entrega,
            "responsable": _responsable_display(proyecto, ponencia),
            "evento_nombre": _evento_display(proyecto, ponencia),
            "titulo_registro": _titulo_display(proyecto, ponencia),
            "codigo_registro": _codigo_display(proyecto, ponencia),
            "fecha_registro": _fecha_para_asignacion(proyecto, ponencia),
            "hora_inicio": getattr(ponencia, "hora_inicio", None) or getattr(proyecto, "hora_inicio", None) or getattr(proyecto, "inicio", None),
            "hora_fin": getattr(ponencia, "hora_fin", None) or getattr(proyecto, "hora_fin", None) or getattr(proyecto, "fin", None),
            "espacio": _display_space(getattr(ponencia, "espacio_asignado", None) or getattr(proyecto, "espacio_asignado", None) or getattr(proyecto, "lugar", None)),
        },
    )


@login_required
def mi_horario(request):
    asignaciones_base = list(
        EvaluacionAsignacion.objects.select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio", "proyecto__fin", "-creado_en")
    )

    asignaciones = []
    con_rubrica = 0
    programadas = 0
    pendientes = 0
    for asignacion in asignaciones_base:
        proyecto = asignacion.proyecto
        entrega = getattr(asignacion, "entrega", None)
        rubrica, ponencia = _rubrica_para_proyecto(proyecto)
        row = _build_schedule_row(asignacion, rubrica=rubrica, ponencia=ponencia, entrega=entrega)
        asignaciones.append(row)
        if row["rubrica_asignada"]:
            con_rubrica += 1
        if row["estado"] != "Pendiente de programación":
            programadas += 1
        else:
            pendientes += 1

    return render(
        request,
        "horario/horario.html",
        {
            "active": "horario",
            "asignaciones": asignaciones,
            "kpi_total": len(asignaciones),
            "kpi_programadas": programadas,
            "kpi_con_rubrica": con_rubrica,
            "kpi_pendientes": pendientes,
            "page_start": 1 if asignaciones else 0,
            "page_end": len(asignaciones),
            "has_prev": False,
            "has_next": False,
        },
    )


@login_required
def historial(request):
    entregas_qs = (
        EvaluacionEntrega.objects.select_related("asignacion", "asignacion__proyecto", "asignacion__evaluador")
        .filter(asignacion__evaluador=request.user, estado=EvaluacionEntrega.ESTADO_ENVIADA)
        .order_by("-fecha_envio", "-actualizado_en")
    )

    entregas = []
    for entrega in entregas_qs:
        proyecto = entrega.asignacion.proyecto
        ponencia = _ponencia_para_proyecto(proyecto)
        proyecto_info = SimpleNamespace(
            titulo=_titulo_display(proyecto, ponencia),
            nombre=_titulo_display(proyecto, ponencia),
            ponente=_responsable_display(proyecto, ponencia),
            autor=_responsable_display(proyecto, ponencia),
            evento=_evento_display(proyecto, ponencia),
        )
        entregas.append(
            SimpleNamespace(
                id=entrega.id,
                pk=entrega.pk,
                proyecto=proyecto_info,
                titulo=proyecto_info.titulo,
                ponente=proyecto_info.ponente,
                fecha_envio=entrega.fecha_envio,
                fecha=entrega.fecha_envio,
                calificacion=entrega.calificacion,
                observaciones_generales=entrega.observaciones_generales,
            )
        )

    promedio = entregas_qs.aggregate(promedio=Avg("calificacion"))["promedio"]
    pendientes = EvaluacionAsignacion.objects.filter(evaluador=request.user).exclude(entrega__estado=EvaluacionEntrega.ESTADO_ENVIADA).count()

    return render(
        request,
        "historial/historial.html",
        {
            "active": "historial",
            "entregas": entregas,
            "kpi_total": len(entregas),
            "kpi_promedio": Decimal(str(promedio)).quantize(Decimal("0.1")) if promedio is not None else None,
            "kpi_pendientes": pendientes,
        },
    )


@login_required
def configuracion(request):
    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        cuenta_form = EvaluadorCuentaForm(request.user, request.POST)
        perfil_form = EvaluadorPerfilForm(request.POST, request.FILES, instance=perfil)
        if cuenta_form.is_valid() and perfil_form.is_valid():
            request.user.first_name = cuenta_form.cleaned_data["nombres"]
            request.user.last_name = cuenta_form.cleaned_data["apellidos"]
            request.user.email = cuenta_form.cleaned_data["correo"]
            request.user.save(update_fields=["first_name", "last_name", "email"])

            perfil = perfil_form.save()

            nueva = cuenta_form.cleaned_data.get("password_nueva")
            if nueva:
                request.user.set_password(nueva)
                request.user.save(update_fields=["password"])
                update_session_auth_hash(request, request.user)

            messages.success(request, "La configuración del perfil se actualizó correctamente.")
            return redirect("evaluador:configuracion")
        messages.error(request, "No fue posible guardar la configuración. Verifica los datos capturados.")
    else:
        cuenta_form = EvaluadorCuentaForm(request.user)
        perfil_form = EvaluadorPerfilForm(instance=perfil)

    return render(
        request,
        "configuracion/configuracion.html",
        {
            "active": "configuracion",
            "perfil": perfil,
            "cuenta_form": cuenta_form,
            "perfil_form": perfil_form,
        },
    )