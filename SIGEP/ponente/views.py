from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, models, transaction
from django.db.models import Avg, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.utils import timezone
from xhtml2pdf import pisa

from administrador.models import Evento
from administrador.utils_auditoria import registrar_auditoria
from coordinador.models import Inscripcion
from evaluador.models import EvaluacionAsignacion, EvaluacionEntrega

from .forms import (
    GestionParticipacionForm,
    PonenciaForm,
    SeleccionEventoForm,
    PonenteCuentaForm,
    PonentePerfilForm,
)
from .models import Ponencia


def _generar_folio_constancia(ponencia: Ponencia) -> str:
    fecha = timezone.localdate().strftime("%Y%m%d")
    return f"CONST-PON-{fecha}-{ponencia.id:06d}"


def _resolve_ponente_profile(user):
    """
    Intenta localizar un objeto de perfil del ponente sin depender
    de un único nombre de relación.
    """
    candidate_attrs = [
        "perfilponente",
        "perfil_ponente",
        "ponenteperfil",
        "perfil",
    ]

    for attr in candidate_attrs:
        obj = getattr(user, attr, None)
        if obj is not None:
            return obj

    return SimpleNamespace(
        institucion=getattr(user, "institucion", ""),
        especialidad=getattr(user, "especialidad", ""),
        telefono=getattr(user, "telefono", ""),
        bio=getattr(user, "bio", ""),
        avatar=getattr(user, "avatar", None),
        cv=getattr(user, "cv", None),
    )


def _asegurar_inscripcion_ponente(evento: Evento, usuario) -> Inscripcion:
    """
    Garantiza que el usuario quede inscrito en el evento como PONENTE
    cuando registra o actualiza una ponencia.
    """
    inscripcion, _ = Inscripcion.objects.get_or_create(
        evento=evento,
        usuario=usuario,
        defaults={"rol": Inscripcion.ROL_PONENTE},
    )
    if inscripcion.rol != Inscripcion.ROL_PONENTE:
        inscripcion.rol = Inscripcion.ROL_PONENTE
        inscripcion.save(update_fields=["rol", "actualizado_en"])
    return inscripcion


def _limpiar_inscripcion_ponente_si_corresponde(evento: Evento, usuario) -> None:
    """
    Si el usuario ya no tiene ponencias en el evento, elimina su inscripción
    de ponente para no dejar basura operativa.
    """
    tiene_ponencias = Ponencia.objects.filter(evento=evento, ponente=usuario).exists()
    if tiene_ponencias:
        return

    Inscripcion.objects.filter(
        evento=evento,
        usuario=usuario,
        rol=Inscripcion.ROL_PONENTE,
    ).delete()


@login_required
def panel(request):
    qs = Ponencia.objects.select_related("evento", "evaluacion_proyecto").filter(ponente=request.user)

    kpi_ponencias_activas = qs.exclude(estado=Ponencia.ESTADO_RECHAZADA).count()

    kpi_archivos_pendientes = qs.filter(
        models.Q(cv_documento__isnull=True) | models.Q(cv_documento="") |
        models.Q(resena_biografica__isnull=True) | models.Q(resena_biografica="") |
        models.Q(diapositivas_presentacion__isnull=True) | models.Q(diapositivas_presentacion="") |
        models.Q(requerimientos_tecnicos__isnull=True) | models.Q(requerimientos_tecnicos="")
    ).count()

    proximo = (
        qs.filter(
            fecha_programada__isnull=False,
            hora_inicio__isnull=False,
            hora_fin__isnull=False,
        )
        .exclude(estado_programacion=Ponencia.PROG_CANCELADO)
        .order_by("fecha_programada", "hora_inicio", "id")
        .first()
    ) or qs.exclude(estado=Ponencia.ESTADO_RECHAZADA).order_by("-actualizado_en").first()

    estado_label_map = {
        Ponencia.ESTADO_REGISTRADA: "Registrado",
        Ponencia.ESTADO_EN_REVISION: "En revisión",
        Ponencia.ESTADO_ACEPTADA: "Aceptado",
        Ponencia.ESTADO_RECHAZADA: "Rechazado",
    }

    recientes = []
    for p in qs.order_by("-actualizado_en")[:3]:
        recientes.append({
            "id": p.id,
            "titulo": p.titulo,
            "tipo": p.tipo or "Ponencia",
            "fecha": timezone.localtime(p.actualizado_en).strftime("%d/%m/%Y %H:%M"),
            "estado": p.estado,
            "estado_label": estado_label_map.get(p.estado, p.estado),
            "evento_id": p.evento_id,
            "evento_nombre": p.nombre_evento(),
        })

    hoy = timezone.localdate()
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    fecha_larga = f"{dias[hoy.weekday()]}, {hoy.day} de {meses[hoy.month - 1]}, {hoy.year}"

    return render(request, "ponente/dashboard/panel.html", {
        "active": "panel",
        "fecha_larga": fecha_larga,
        "kpi_ponencias_activas": kpi_ponencias_activas,
        "kpi_archivos_pendientes": kpi_archivos_pendientes,
        "kpi_delta": "+0 esta semana",
        "proximo": proximo,
        "recientes": recientes,
    })


@login_required
def inscripcion(request):
    eventos = Evento.objects.filter(estado="PUBLICADO").order_by("-fecha", "-id")

    evento_id = request.GET.get("evento")
    evento_sel = None

    if evento_id:
        try:
            evento_sel = eventos.get(pk=int(evento_id))
        except (ValueError, Evento.DoesNotExist):
            evento_sel = None
            messages.error(request, "El evento seleccionado no es válido o ya no está disponible.")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "select_event":
            form_sel = SeleccionEventoForm(request.POST)
            if form_sel.is_valid():
                eid = form_sel.cleaned_data["evento_id"]
                if eventos.filter(pk=eid).exists():
                    return redirect(f"{request.path}?evento={eid}")
            messages.error(request, "Selecciona un evento válido.")
            return redirect(request.path)

        if action == "registrar":
            if not evento_sel:
                messages.error(request, "Debes seleccionar un evento antes de registrar tu ponencia.")
                return redirect(request.path)
            if Ponencia.objects.filter(evento=evento_sel, ponente=request.user).exists():
                messages.error(request, "Solo puedes registrar una ponencia por evento.")
                return redirect(f"{request.path}?evento={evento_sel.id}")

            form = PonenciaForm(request.POST, request.FILES)
            if form.is_valid():
                ponencia = form.save(commit=False)
                ponencia.evento = evento_sel
                ponencia.ponente = request.user
                ponencia.estado = Ponencia.ESTADO_REGISTRADA

                try:
                    with transaction.atomic():
                        ponencia.save()
                        _asegurar_inscripcion_ponente(evento_sel, request.user)

                    registrar_auditoria(
                        request=request,
                        accion=f"PONENCIAS | CREAR | {ponencia.titulo}",
                        modulo="PONENCIAS",
                        accion_tipo="CREAR",
                        entidad="Ponencia",
                        objeto_id=ponencia.pk,
                        resultado="EXITOSO",
                        detalles={"evento_id": evento_sel.id, "evento": evento_sel.titulo},
                    )
                    messages.success(
                        request,
                        "La ponencia fue registrada correctamente y tu inscripción al evento quedó vinculada como ponente.",
                    )
                except IntegrityError:
                    messages.error(request, "Ya tienes una ponencia registrada en este evento.")

                return redirect(f"{request.path}?evento={evento_sel.id}")

            messages.error(request, "No fue posible registrar la ponencia. Verifica la información.")
            return redirect(f"{request.path}?evento={evento_sel.id}")

        if action == "editar":
            if not evento_sel:
                messages.error(request, "Debes seleccionar un evento válido.")
                return redirect(request.path)

            ponencia_id = request.POST.get("ponencia_id")
            ponencia = get_object_or_404(
                Ponencia,
                pk=ponencia_id,
                evento=evento_sel,
                ponente=request.user,
            )

            if not ponencia.puede_editar():
                messages.error(request, "Esta ponencia ya no permite edición.")
                return redirect(f"{request.path}?evento={evento_sel.id}")

            form = PonenciaForm(request.POST, request.FILES, instance=ponencia)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                        _asegurar_inscripcion_ponente(evento_sel, request.user)

                    registrar_auditoria(
                        request=request,
                        accion=f"PONENCIAS | EDITAR | {ponencia.titulo}",
                        modulo="PONENCIAS",
                        accion_tipo="EDITAR",
                        entidad="Ponencia",
                        objeto_id=ponencia.pk,
                        resultado="EXITOSO",
                        detalles={"evento_id": evento_sel.id, "evento": evento_sel.titulo},
                    )
                    messages.success(request, "La ponencia fue actualizada correctamente.")
                except IntegrityError:
                    messages.error(request, "Ya tienes una ponencia registrada en este evento.")
            else:
                messages.error(request, "No fue posible actualizar la ponencia.")

            return redirect(f"{request.path}?evento={evento_sel.id}")

        if action == "eliminar":
            if not evento_sel:
                messages.error(request, "Debes seleccionar un evento válido.")
                return redirect(request.path)

            ponencia_id = request.POST.get("ponencia_id")
            ponencia = get_object_or_404(
                Ponencia,
                pk=ponencia_id,
                evento=evento_sel,
                ponente=request.user,
            )

            if not ponencia.puede_editar():
                messages.error(request, "Esta ponencia ya no permite eliminación.")
                return redirect(f"{request.path}?evento={evento_sel.id}")

            ponencia_id_deleted = ponencia.pk
            ponencia_titulo = ponencia.titulo
            with transaction.atomic():
                ponencia.delete()
                _limpiar_inscripcion_ponente_si_corresponde(evento_sel, request.user)

            registrar_auditoria(
                request=request,
                accion=f"PONENCIAS | ELIMINAR | {ponencia_titulo}",
                modulo="PONENCIAS",
                accion_tipo="ELIMINAR",
                entidad="Ponencia",
                objeto_id=ponencia_id_deleted,
                resultado="EXITOSO",
                detalles={"evento_id": evento_sel.id, "evento": evento_sel.titulo},
            )
            messages.success(request, "La ponencia fue eliminada correctamente.")
            return redirect(f"{request.path}?evento={evento_sel.id}")

    ponencias = Ponencia.objects.none()
    if evento_sel:
        ponencias = Ponencia.objects.filter(
            evento=evento_sel,
            ponente=request.user
        ).order_by("-actualizado_en")

    puede_registrar = bool(evento_sel) and not ponencias.exists()

    return render(request, "ponente/inscripcion/inscripcion.html", {
        "active": "inscripcion",
        "eventos": eventos,
        "evento_sel": evento_sel,
        "ponencias": ponencias,
        "puede_registrar": puede_registrar,
        "form_registrar": PonenciaForm(),
    })


@login_required
def gestionar_participacion(request):
    ponencia_id = request.GET.get("ponencia")
    ponencias = Ponencia.objects.filter(ponente=request.user).select_related("evento").order_by("-actualizado_en")

    ponencia_sel = None
    if ponencia_id:
        ponencia_sel = get_object_or_404(Ponencia, pk=ponencia_id, ponente=request.user)
    else:
        ponencia_sel = ponencias.first()

    if request.method == "POST":
        if not ponencia_sel:
            messages.error(request, "No se encontró una ponencia válida para gestionar.")
            return redirect("ponente:inscripcion")

        form = GestionParticipacionForm(request.POST, request.FILES, instance=ponencia_sel)
        if form.is_valid():
            registro = form.save(commit=False)

            if request.FILES.get("cv_documento"):
                registro.cv_estado = Ponencia.DOC_EN_REVISION

            if request.FILES.get("resena_biografica"):
                registro.resena_estado = Ponencia.DOC_EN_REVISION

            if request.FILES.get("diapositivas_presentacion"):
                registro.diapositivas_estado = Ponencia.DOC_EN_REVISION

            registro.save()
            registrar_auditoria(
                request=request,
                accion=f"PONENCIAS | ACTUALIZAR_DOCUMENTACION | {registro.titulo}",
                modulo="PONENCIAS",
                accion_tipo="ACTUALIZAR_DOCUMENTACION",
                entidad="Ponencia",
                objeto_id=registro.pk,
                resultado="EXITOSO",
                detalles={"evento_id": registro.evento_id, "evento": registro.nombre_evento()},
            )
            messages.success(request, "La documentación de participación fue actualizada correctamente.")
            return redirect(f"{request.path}?ponencia={registro.id}")

        messages.error(request, "No fue posible guardar la documentación. Verifica la información.")

    form = GestionParticipacionForm(instance=ponencia_sel) if ponencia_sel else GestionParticipacionForm()

    return render(request, "ponente/participacion/gestionar.html", {
        "active": "participacion",
        "ponencias": ponencias,
        "ponencia_sel": ponencia_sel,
        "form": form,
    })


@login_required
def mi_horario(request):
    horarios = (
        Ponencia.objects.select_related("evento")
        .filter(
            ponente=request.user,
            fecha_programada__isnull=False,
            hora_inicio__isnull=False,
            hora_fin__isnull=False,
        )
        .exclude(estado_programacion=Ponencia.PROG_CANCELADO)
        .order_by("fecha_programada", "hora_inicio", "id")
    )

    return render(request, "ponente/horario/horario.html", {
        "active": "horario",
        "horarios": horarios,
        "total_horarios": horarios.count(),
    })


@login_required
def mis_resultados(request):
    ponencias = (
        Ponencia.objects.select_related("evento", "evaluacion_proyecto")
        .filter(ponente=request.user)
        .order_by("-actualizado_en")
    )

    ponencia_id = request.GET.get("ponencia")
    ponencia_sel = None

    if ponencia_id:
        ponencia_sel = get_object_or_404(
            Ponencia.objects.select_related("evento", "evaluacion_proyecto"),
            pk=ponencia_id,
            ponente=request.user,
        )
    else:
        ponencia_sel = ponencias.first()

    promedio_general = Decimal("0.0")
    total_evaluaciones = 0
    evaluaciones_asignadas = 0
    avance_porcentaje = 0
    estado_resultado = "Sin evaluación"
    comentarios = []
    evaluadores_estado = []

    if ponencia_sel and ponencia_sel.evaluacion_proyecto:
        proyecto = ponencia_sel.evaluacion_proyecto

        asignaciones_qs = (
            EvaluacionAsignacion.objects
            .select_related("evaluador")
            .filter(proyecto=proyecto)
            .order_by("evaluador__first_name", "evaluador__last_name", "evaluador__email")
        )

        entregas_qs = (
            EvaluacionEntrega.objects
            .select_related("asignacion", "asignacion__evaluador")
            .filter(asignacion__proyecto=proyecto)
            .order_by("-fecha_envio", "-actualizado_en")
        )

        entregas_enviadas = entregas_qs.filter(estado=EvaluacionEntrega.ESTADO_ENVIADA)

        evaluaciones_asignadas = asignaciones_qs.count()
        total_evaluaciones = entregas_enviadas.count()

        if evaluaciones_asignadas > 0:
            avance_porcentaje = int((total_evaluaciones / evaluaciones_asignadas) * 100)

        promedio_db = entregas_enviadas.aggregate(promedio=Avg("calificacion"))["promedio"]
        if promedio_db is not None:
            promedio_general = Decimal(str(promedio_db)).quantize(Decimal("0.1"))

        if evaluaciones_asignadas == 0:
            estado_resultado = "Pendiente de asignación"
        elif total_evaluaciones == 0:
            estado_resultado = "Evaluación no iniciada"
        elif total_evaluaciones < evaluaciones_asignadas:
            estado_resultado = "Evaluación en progreso"
        else:
            estado_resultado = "Evaluación finalizada"

        comentarios = [
            {
                "evaluador": (
                    f"{entrega.asignacion.evaluador.first_name} {entrega.asignacion.evaluador.last_name}".strip()
                    or entrega.asignacion.evaluador.username
                ),
                "rol": "Evaluador",
                "comentario": entrega.observaciones_generales,
                "calificacion": entrega.calificacion,
                "fecha": entrega.fecha_envio or entrega.actualizado_en,
            }
            for entrega in entregas_enviadas
            if (entrega.observaciones_generales or "").strip()
        ]

        entregas_map = {ent.asignacion_id: ent for ent in entregas_qs}
        for asignacion in asignaciones_qs:
            entrega = entregas_map.get(asignacion.id)
            nombre_evaluador = (
                f"{asignacion.evaluador.first_name} {asignacion.evaluador.last_name}".strip()
                or asignacion.evaluador.username
            )

            evaluadores_estado.append({
                "nombre": nombre_evaluador,
                "estado": (
                    "Enviada"
                    if entrega and entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA
                    else "Pendiente"
                ),
                "calificacion": entrega.calificacion if entrega and entrega.estado == EvaluacionEntrega.ESTADO_ENVIADA else None,
            })

    return render(request, "ponente/resultados/resultados.html", {
        "active": "resultados",
        "ponencias": ponencias,
        "ponencia_sel": ponencia_sel,
        "promedio_general": promedio_general,
        "total_evaluaciones": total_evaluaciones,
        "evaluaciones_asignadas": evaluaciones_asignadas,
        "avance_porcentaje": avance_porcentaje,
        "estado_resultado": estado_resultado,
        "comentarios": comentarios[:10],
        "evaluadores_estado": evaluadores_estado,
    })


@login_required
def historial(request):
    hoy = timezone.localdate()

    items = (
        Ponencia.objects.select_related("evento")
        .filter(
            ponente=request.user,
            fecha_programada__isnull=False,
        )
        .filter(
            Q(fecha_programada__lt=hoy) |
            Q(estado_programacion=Ponencia.PROG_CANCELADO)
        )
        .order_by("-fecha_programada", "-hora_inicio", "-id")
    )

    return render(request, "ponente/historial/historial.html", {
        "active": "historial",
        "items": items,
        "total_items": items.count(),
    })


@login_required
def generar_constancia(request):
    q = (request.GET.get("q") or "").strip().lower()

    ponencias_qs = (
        Ponencia.objects.select_related("evento")
        .filter(ponente=request.user)
        .exclude(estado_programacion=Ponencia.PROG_CANCELADO)
        .order_by("-fecha_programada", "-id")
    )

    ponencias_disponibles = [p for p in ponencias_qs if p.puede_generar_constancia()]

    if q:
        ponencias_disponibles = [
            p for p in ponencias_disponibles
            if q in p.nombre_evento().lower()
            or q in (p.titulo or "").lower()
            or q in (p.tipo or "").lower()
        ]

    ponencia_id = request.GET.get("ponencia")
    ponencia_sel = None

    if ponencia_id:
        ponencia_sel = get_object_or_404(
            Ponencia.objects.select_related("evento"),
            pk=ponencia_id,
            ponente=request.user
        )
        if not ponencia_sel.puede_generar_constancia():
            messages.error(request, "Esta constancia aún no está disponible para descarga.")
            return redirect("ponente:constancia")
    elif ponencias_disponibles:
        ponencia_sel = ponencias_disponibles[0]

    return render(request, "ponente/constancia/constancia.html", {
        "active": "constancia",
        "ponencias_disponibles": ponencias_disponibles,
        "ponencia_sel": ponencia_sel,
        "fecha_emision": timezone.localdate(),
        "search_q": request.GET.get("q", ""),
    })


@login_required
def descargar_constancia_pdf(request, ponencia_id):
    ponencia = get_object_or_404(
        Ponencia.objects.select_related("evento"),
        pk=ponencia_id,
        ponente=request.user
    )

    if not ponencia.puede_generar_constancia():
        messages.error(request, "La constancia no está habilitada para esta participación.")
        return redirect("ponente:constancia")

    if not ponencia.folio_constancia:
        ponencia.folio_constancia = _generar_folio_constancia(ponencia)
        ponencia.constancia_generada_en = timezone.now()
        ponencia.save(update_fields=["folio_constancia", "constancia_generada_en"])

    template = get_template("ponente/constancia/constancia_pdf.html")
    html = template.render({
        "ponencia": ponencia,
        "usuario": request.user,
        "fecha_emision": timezone.localdate(),
    })

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="constancia_{ponencia.id}.pdf"'

    pisa_status = pisa.CreatePDF(src=html, dest=response, encoding="utf-8")

    if pisa_status.err:
        return HttpResponse("No fue posible generar la constancia PDF.", status=500)

    return response


@login_required
def configuracion(request):
    user = request.user
    perfil = _resolve_ponente_profile(user)

    if request.method == "POST":
        cuenta_form = PonenteCuentaForm(request.POST, user=user)
        perfil_form = PonentePerfilForm(request.POST, request.FILES, user=user, profile=perfil)

        if cuenta_form.is_valid() and perfil_form.is_valid():
            usuario_actualizado = cuenta_form.save()
            perfil_form.save()

            if cuenta_form.password_changed:
                update_session_auth_hash(request, usuario_actualizado)

            registrar_auditoria(
                request=request,
                accion="PONENCIAS | CONFIGURACION | Actualización de cuenta/perfil",
                modulo="PONENCIAS",
                accion_tipo="CONFIGURACION",
                entidad="PerfilPonente",
                objeto_id=request.user.pk,
                resultado="EXITOSO",
            )
            messages.success(request, "La configuración del ponente fue actualizada correctamente.")
            return redirect("ponente:configuracion")

        messages.error(request, "No fue posible actualizar la configuración. Verifica los datos capturados.")
    else:
        cuenta_form = PonenteCuentaForm(user=user)
        perfil_form = PonentePerfilForm(user=user, profile=perfil)

    return render(request, "ponente/configuracion/configuracion.html", {
        "active": "configuracion",
        "cuenta_form": cuenta_form,
        "perfil_form": perfil_form,
        "perfil": perfil,
    })
