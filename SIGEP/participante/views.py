from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Avg, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from administrador.models import Evento
from coordinador.models import Inscripcion
from evaluador.models import EvaluacionAsignacion, EvaluacionEntrega

from .forms import (
    ParticipanteCuentaForm,
    ParticipantePerfilForm,
    ProyectoParticipanteForm,
    SeleccionEventoForm,
)
from .models import PerfilParticipante, ProyectoParticipante

try:
    from administrador.utils_auditoria import registrar_auditoria
except Exception:  # pragma: no cover
    def registrar_auditoria(**kwargs):
        return None


def _resolve_profile(user):
    perfil, _ = PerfilParticipante.objects.get_or_create(usuario=user)
    return perfil


def _asegurar_inscripcion_participante(evento: Evento, usuario) -> Inscripcion:
    inscripcion, _ = Inscripcion.objects.get_or_create(
        evento=evento,
        usuario=usuario,
        defaults={"rol": Inscripcion.ROL_PARTICIPANTE},
    )
    if inscripcion.rol != Inscripcion.ROL_PARTICIPANTE:
        inscripcion.rol = Inscripcion.ROL_PARTICIPANTE
        inscripcion.save(update_fields=["rol", "actualizado_en"])
    return inscripcion


def _limpiar_inscripcion_si_corresponde(evento: Evento, usuario) -> None:
    if ProyectoParticipante.objects.filter(evento=evento, participante=usuario).exists():
        return
    Inscripcion.objects.filter(
        evento=evento,
        usuario=usuario,
        rol=Inscripcion.ROL_PARTICIPANTE,
    ).delete()


@login_required
def panel_participante(request):
    qs = ProyectoParticipante.objects.select_related("evento", "evaluacion_proyecto").filter(participante=request.user)
    hoy = timezone.localdate()
    proximo = (
        qs.filter(fecha_programada__gte=hoy, hora_inicio__isnull=False)
        .exclude(estado_programacion=ProyectoParticipante.PROG_CANCELADO)
        .order_by("fecha_programada", "hora_inicio", "id")
        .first()
    ) or qs.order_by("-actualizado_en").first()

    kpis = {
        "proyectos_activos": qs.exclude(estado=ProyectoParticipante.ESTADO_RECHAZADO).count(),
        "pendientes_programacion": qs.filter(estado_programacion=ProyectoParticipante.PROG_PENDIENTE).count(),
        "documentacion_incompleta": sum(1 for p in qs if p.porcentaje_documentacion() < 100),
        "evaluaciones_completadas": EvaluacionEntrega.objects.filter(
            asignacion__proyecto__proyecto_real__participante=request.user,
            estado=EvaluacionEntrega.ESTADO_ENVIADA,
        ).count(),
    }

    proyectos = []
    for p in qs.order_by("-actualizado_en")[:5]:
        proyectos.append({
            "id": p.id,
            "nombre": p.nombre_proyecto,
            "evento": p.nombre_evento(),
            "estado": p.get_estado_display(),
            "programacion": p.get_estado_programacion_display(),
            "progreso": p.porcentaje_documentacion(),
        })

    return render(request, "participante/dashboard/panel.html", {
        "active": "panel",
        "kpis": kpis,
        "proximo": proximo,
        "proyectos": proyectos,
    })


@login_required
def elegir_evento(request):
    eventos = Evento.objects.filter(estado="PUBLICADO").order_by("-fecha", "-id")
    evento_id = request.GET.get("evento")
    evento_sel = None
    if evento_id:
        try:
            evento_sel = eventos.get(pk=int(evento_id))
        except (ValueError, Evento.DoesNotExist):
            messages.error(request, "El evento seleccionado no es válido o ya no está disponible.")

    proyectos_qs = ProyectoParticipante.objects.select_related("evento").filter(participante=request.user)
    proyecto_sel = proyectos_qs.filter(evento=evento_sel).first() if evento_sel else None

    form = ProyectoParticipanteForm(
        evento=evento_sel,
        usuario=request.user,
        initial={
            "nombre_participante": request.user.get_full_name() or getattr(request.user, "username", ""),
            "correo": getattr(request.user, "email", ""),
        },
    )
    abrir_modal = False
    evento_modal = evento_sel

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "select_event":
            sel = SeleccionEventoForm(request.POST)
            if sel.is_valid() and eventos.filter(pk=sel.cleaned_data["evento_id"]).exists():
                return redirect(f"{request.path}?evento={sel.cleaned_data['evento_id']}")
            messages.error(request, "Selecciona un evento válido.")
            return redirect(request.path)

        if action == "registrar":
            if not evento_sel:
                messages.error(request, "Debes seleccionar un evento publicado antes de registrar tu proyecto.")
                return redirect(request.path)

            form = ProyectoParticipanteForm(request.POST, request.FILES, evento=evento_sel, usuario=request.user)
            abrir_modal = True
            if form.is_valid():
                proyecto = form.save(commit=False)
                proyecto.evento = evento_sel
                proyecto.participante = request.user
                proyecto.estado = ProyectoParticipante.ESTADO_REGISTRADO
                try:
                    with transaction.atomic():
                        proyecto.save()
                        _asegurar_inscripcion_participante(evento_sel, request.user)
                    registrar_auditoria(
                        request=request,
                        accion=f"PROYECTOS | CREAR | {proyecto.nombre_proyecto}",
                        modulo="PARTICIPANTE",
                        accion_tipo="CREAR",
                        entidad="ProyectoParticipante",
                        objeto_id=proyecto.pk,
                        resultado="EXITOSO",
                    )
                    messages.success(request, "El proyecto fue registrado correctamente y tu inscripción al evento quedó vinculada como participante.")
                    return redirect(f"{request.path}?evento={evento_sel.id}")
                except IntegrityError:
                    form.add_error(None, "Ya tienes un proyecto registrado en este evento.")
            messages.error(request, "No fue posible registrar el proyecto. Verifica la información capturada.")

        if action == "editar":
            proyecto = get_object_or_404(ProyectoParticipante, pk=request.POST.get("proyecto_id"), participante=request.user)
            evento_sel = proyecto.evento
            evento_modal = evento_sel
            form = ProyectoParticipanteForm(request.POST, request.FILES, instance=proyecto, evento=evento_sel, usuario=request.user)
            abrir_modal = True
            if not proyecto.puede_editar():
                messages.error(request, "Este proyecto ya no permite edición.")
                return redirect(f"{request.path}?evento={evento_sel.id}")
            if form.is_valid():
                with transaction.atomic():
                    proyecto = form.save()
                    _asegurar_inscripcion_participante(evento_sel, request.user)
                messages.success(request, "El proyecto fue actualizado correctamente.")
                return redirect(f"{request.path}?evento={evento_sel.id}")
            messages.error(request, "No fue posible actualizar el proyecto. Revisa los campos marcados.")

    eventos_ya_inscritos = set(proyectos_qs.values_list("evento_id", flat=True))
    return render(request, "participante/evento/elegir_evento.html", {
        "active": "elegir_evento",
        "eventos": eventos,
        "evento_sel": evento_sel,
        "evento_modal": evento_modal,
        "proyecto_sel": proyecto_sel,
        "proyectos_usuario": proyectos_qs,
        "eventos_ya_inscritos": eventos_ya_inscritos,
        "form": form,
        "abrir_modal": abrir_modal,
    })


@login_required
def eliminar_proyecto(request, pk: int):
    proyecto = get_object_or_404(ProyectoParticipante, pk=pk, participante=request.user)
    evento_id = proyecto.evento_id
    if request.method == "POST":
        with transaction.atomic():
            proyecto.delete()
            _limpiar_inscripcion_si_corresponde(proyecto.evento, request.user)
        messages.success(request, "El proyecto fue eliminado correctamente.")
    return redirect(f"/participante/evento/elegir/?evento={evento_id}")


@login_required
def programa(request):
    proyectos = ProyectoParticipante.objects.filter(participante=request.user).select_related("evento").order_by("fecha_programada", "hora_inicio", "-actualizado_en")
    return render(request, "participante/programa/programa.html", {
        "active": "programa",
        "proyectos": proyectos,
    })


@login_required
def mi_pase(request):
    proyecto = (
        ProyectoParticipante.objects.filter(participante=request.user)
        .exclude(estado=ProyectoParticipante.ESTADO_RECHAZADO)
        .select_related("evento")
        .order_by("-actualizado_en")
        .first()
    )
    return render(request, "participante/pase/pase.html", {
        "active": "mi_pase",
        "proyecto": proyecto,
    })


@login_required
def constancia(request):
    proyecto = (
        ProyectoParticipante.objects.filter(participante=request.user)
        .select_related("evento")
        .order_by("-actualizado_en")
        .first()
    )
    promedio = None
    if proyecto and proyecto.evaluacion_proyecto:
        promedio = EvaluacionEntrega.objects.filter(
            asignacion__proyecto=proyecto.evaluacion_proyecto,
            estado=EvaluacionEntrega.ESTADO_ENVIADA,
        ).aggregate(prom=Avg("calificacion_total")).get("prom")
        promedio = Decimal(promedio or 0).quantize(Decimal("0.01")) if promedio is not None else None

    return render(request, "participante/constancia/constancia.html", {
        "active": "constancia",
        "proyecto": proyecto,
        "promedio": promedio,
    })


@login_required
def configuracion(request):
    perfil = _resolve_profile(request.user)
    cuenta_form = ParticipanteCuentaForm(user=request.user, initial={
        "nombres": getattr(request.user, "first_name", ""),
        "apellidos": getattr(request.user, "last_name", ""),
        "correo": getattr(request.user, "email", ""),
    })
    perfil_form = ParticipantePerfilForm(instance=perfil)

    if request.method == "POST":
        cuenta_form = ParticipanteCuentaForm(request.POST, user=request.user)
        perfil_form = ParticipantePerfilForm(request.POST, request.FILES, instance=perfil)
        if cuenta_form.is_valid() and perfil_form.is_valid():
            request.user.first_name = cuenta_form.cleaned_data["nombres"]
            request.user.last_name = cuenta_form.cleaned_data["apellidos"]
            request.user.email = cuenta_form.cleaned_data["correo"]
            nueva = cuenta_form.cleaned_data.get("password_nueva")
            if nueva:
                request.user.set_password(nueva)
            request.user.save()
            perfil_form.save()
            if nueva:
                update_session_auth_hash(request, request.user)
            messages.success(request, "La configuración se actualizó correctamente.")
            return redirect("participante:configuracion")
        messages.error(request, "No fue posible guardar la configuración. Verifica la información.")

    return render(request, "participante/configuracion/configuracion.html", {
        "active": "configuracion",
        "cuenta_form": cuenta_form,
        "perfil_form": perfil_form,
        "perfil": perfil,
    })