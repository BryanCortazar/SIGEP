from __future__ import annotations

from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from administrador.models import Evento
from .models import InscripcionParticipante, PerfilParticipante


def _resolve_participante_profile(user):
    try:
        return user.perfilparticipante
    except Exception:
        return SimpleNamespace(
            institucion=getattr(user, "institucion", ""),
            carrera=getattr(user, "carrera", ""),
            telefono=getattr(user, "telefono", ""),
            bio=getattr(user, "bio", ""),
            avatar=getattr(user, "avatar", None),
        )


def _get_event_name(evento):
    if hasattr(evento, "nombre") and evento.nombre:
        return evento.nombre
    if hasattr(evento, "titulo") and evento.titulo:
        return evento.titulo
    return "Evento"


def _get_event_place(evento):
    if hasattr(evento, "lugar") and evento.lugar:
        return evento.lugar
    if hasattr(evento, "ubicacion") and evento.ubicacion:
        return evento.ubicacion
    if hasattr(evento, "sede") and evento.sede:
        return evento.sede
    return "Por definir"


def _get_event_start(evento):
    if hasattr(evento, "fecha_inicio") and evento.fecha_inicio:
        return evento.fecha_inicio
    if hasattr(evento, "fecha") and evento.fecha:
        return evento.fecha
    return None


def _get_event_end(evento):
    if hasattr(evento, "fecha_fin") and evento.fecha_fin:
        return evento.fecha_fin
    if hasattr(evento, "fecha") and evento.fecha:
        return evento.fecha
    if hasattr(evento, "fecha_inicio") and evento.fecha_inicio:
        return evento.fecha_inicio
    return None


def _generar_folio_inscripcion(inscripcion_id: int) -> str:
    fecha = timezone.localdate().strftime("%Y%m%d")
    return f"INS-PAR-{fecha}-{inscripcion_id:06d}"


def _generar_codigo_pase(inscripcion_id: int) -> str:
    fecha = timezone.localdate().strftime("%Y%m%d")
    return f"PASE-PAR-{fecha}-{inscripcion_id:06d}"


def _generar_folio_constancia(inscripcion_id: int) -> str:
    fecha = timezone.localdate().strftime("%Y%m%d")
    return f"CONST-PAR-{fecha}-{inscripcion_id:06d}"


@login_required
def panel(request):
    inscripciones = (
        InscripcionParticipante.objects.select_related("evento")
        .filter(participante=request.user)
        .order_by("-actualizado_en", "-id")
    )

    total_inscripciones = inscripciones.count()
    total_confirmadas = inscripciones.filter(
        estado_inscripcion=InscripcionParticipante.ESTADO_CONFIRMADO
    ).count()
    total_constancias = sum(1 for i in inscripciones if i.puede_descargar_constancia())
    total_pases = sum(1 for i in inscripciones if i.puede_descargar_pase())

    hoy = timezone.localdate()
    proximo_evento = None
    for item in inscripciones:
        fecha_inicio = item.fecha_evento()
        if fecha_inicio and fecha_inicio >= hoy and item.estado_inscripcion != InscripcionParticipante.ESTADO_CANCELADO:
            proximo_evento = item
            break

    recientes = list(inscripciones[:4])

    return render(request, "participante/dashboard/panel.html", {
        "active": "panel",
        "inscripciones": inscripciones,
        "total_inscripciones": total_inscripciones,
        "total_confirmadas": total_confirmadas,
        "total_constancias": total_constancias,
        "total_pases": total_pases,
        "proximo_evento": proximo_evento,
        "recientes": recientes,
    })


@login_required
def elegir_evento(request):
    eventos = Evento.objects.all().order_by("-id")

    q = (request.GET.get("q") or "").strip().lower()
    if q:
        eventos = [
            e for e in eventos
            if q in _get_event_name(e).lower()
            or q in _get_event_place(e).lower()
        ]

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "inscribir":
            evento_id = request.POST.get("evento_id")
            evento = get_object_or_404(Evento, pk=evento_id)

            existente = InscripcionParticipante.objects.filter(
                participante=request.user,
                evento=evento
            ).first()

            if existente:
                messages.error(request, "Ya cuentas con un registro para este evento.")
                return redirect("participante:elegir_evento")

            inscripcion = InscripcionParticipante.objects.create(
                participante=request.user,
                evento=evento,
                estado_inscripcion=InscripcionParticipante.ESTADO_PREINSCRITO,
                tipo_acceso="Participante",
            )
            inscripcion.folio_inscripcion = _generar_folio_inscripcion(inscripcion.id)
            inscripcion.codigo_pase = _generar_codigo_pase(inscripcion.id)
            inscripcion.pase_generado_en = timezone.now()
            inscripcion.save(update_fields=[
                "folio_inscripcion",
                "codigo_pase",
                "pase_generado_en",
            ])

            messages.success(request, "Tu inscripción al evento fue registrada correctamente.")
            return redirect("participante:elegir_evento")

    inscripciones_usuario = {
        item.evento_id: item
        for item in InscripcionParticipante.objects.filter(participante=request.user)
    }

    return render(request, "participante/evento/elegir_evento.html", {
        "active": "elegir_evento",
        "eventos": eventos,
        "search_q": request.GET.get("q", ""),
        "inscripciones_usuario": inscripciones_usuario,
    })


@login_required
def programa(request):
    inscripciones = (
        InscripcionParticipante.objects.select_related("evento")
        .filter(
            participante=request.user,
            estado_inscripcion__in=[
                InscripcionParticipante.ESTADO_PREINSCRITO,
                InscripcionParticipante.ESTADO_CONFIRMADO,
            ],
        )
        .order_by("-actualizado_en", "-id")
    )

    inscripcion_id = request.GET.get("inscripcion")
    inscripcion_sel = None

    if inscripcion_id:
        inscripcion_sel = get_object_or_404(
            InscripcionParticipante.objects.select_related("evento"),
            pk=inscripcion_id,
            participante=request.user,
        )
    else:
        inscripcion_sel = inscripciones.first()

    programa_items = []
    if inscripcion_sel:
        programa_items.append({
            "titulo": _get_event_name(inscripcion_sel.evento),
            "fecha_inicio": _get_event_start(inscripcion_sel.evento),
            "fecha_fin": _get_event_end(inscripcion_sel.evento),
            "lugar": _get_event_place(inscripcion_sel.evento),
            "tipo": "Evento",
            "descripcion": getattr(inscripcion_sel.evento, "descripcion", "") or "Programa general del evento.",
        })

    return render(request, "participante/programa/programa.html", {
        "active": "programa",
        "inscripciones": inscripciones,
        "inscripcion_sel": inscripcion_sel,
        "programa_items": programa_items,
    })


@login_required
def mi_pase(request):
    inscripciones = (
        InscripcionParticipante.objects.select_related("evento")
        .filter(participante=request.user)
        .exclude(estado_inscripcion=InscripcionParticipante.ESTADO_CANCELADO)
        .order_by("-actualizado_en", "-id")
    )

    inscripcion_id = request.GET.get("inscripcion")
    inscripcion_sel = None

    if inscripcion_id:
        inscripcion_sel = get_object_or_404(
            InscripcionParticipante.objects.select_related("evento"),
            pk=inscripcion_id,
            participante=request.user,
        )
    else:
        inscripcion_sel = inscripciones.first()

    if inscripcion_sel and not inscripcion_sel.codigo_pase:
        inscripcion_sel.codigo_pase = _generar_codigo_pase(inscripcion_sel.id)
        inscripcion_sel.pase_generado_en = timezone.now()
        inscripcion_sel.save(update_fields=["codigo_pase", "pase_generado_en"])

    return render(request, "participante/pase/mi_pase.html", {
        "active": "mi_pase",
        "inscripciones": inscripciones,
        "inscripcion_sel": inscripcion_sel,
    })


@login_required
def descargar_constancia(request):
    inscripciones_qs = (
        InscripcionParticipante.objects.select_related("evento")
        .filter(participante=request.user)
        .order_by("-actualizado_en", "-id")
    )

    disponibles = [i for i in inscripciones_qs if i.puede_descargar_constancia()]

    inscripcion_id = request.GET.get("inscripcion")
    inscripcion_sel = None

    if inscripcion_id:
        inscripcion_sel = get_object_or_404(
            InscripcionParticipante.objects.select_related("evento"),
            pk=inscripcion_id,
            participante=request.user,
        )
        if not inscripcion_sel.puede_descargar_constancia():
            messages.error(request, "La constancia aún no está disponible para esta inscripción.")
            return redirect("participante:constancia")
    elif disponibles:
        inscripcion_sel = disponibles[0]

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "generar_pdf":
            inscripcion_id_post = request.POST.get("inscripcion_id")
            inscripcion_pdf = get_object_or_404(
                InscripcionParticipante.objects.select_related("evento"),
                pk=inscripcion_id_post,
                participante=request.user,
            )

            if not inscripcion_pdf.puede_descargar_constancia():
                messages.error(request, "La constancia no está habilitada para esta inscripción.")
                return redirect("participante:constancia")

            if not inscripcion_pdf.folio_constancia:
                inscripcion_pdf.folio_constancia = _generar_folio_constancia(inscripcion_pdf.id)
                inscripcion_pdf.constancia_generada_en = timezone.now()
                inscripcion_pdf.save(update_fields=["folio_constancia", "constancia_generada_en"])

            messages.success(request, "La constancia quedó lista para descarga.")
            return redirect(f"{request.path}?inscripcion={inscripcion_pdf.id}")

    return render(request, "participante/constancia/constancia.html", {
        "active": "constancia",
        "inscripciones_disponibles": disponibles,
        "inscripcion_sel": inscripcion_sel,
        "fecha_emision": timezone.localdate(),
    })


@login_required
def configuracion(request):
    user = request.user

    perfil, _ = PerfilParticipante.objects.get_or_create(user=user)
    if perfil is None:
        perfil = _resolve_participante_profile(user)

    if request.method == "POST":
        nombres = (request.POST.get("nombres") or "").strip()
        apellidos = (request.POST.get("apellidos") or "").strip()
        correo = (request.POST.get("correo") or "").strip().lower()
        telefono = (request.POST.get("telefono") or "").strip()
        institucion = (request.POST.get("institucion") or "").strip()
        carrera = (request.POST.get("carrera") or "").strip()
        bio = (request.POST.get("bio") or "").strip()

        password_actual = (request.POST.get("password_actual") or "").strip()
        password_nueva1 = (request.POST.get("password_nueva1") or "").strip()
        password_nueva2 = (request.POST.get("password_nueva2") or "").strip()

        if not nombres or not apellidos or not correo:
            messages.error(request, "Nombre, apellidos y correo son obligatorios.")
            return redirect("participante:configuracion")

        existe_correo = type(user).objects.exclude(pk=user.pk).filter(email__iexact=correo).exists()
        if existe_correo:
            messages.error(request, "Ya existe una cuenta registrada con este correo.")
            return redirect("participante:configuracion")

        if any([password_actual, password_nueva1, password_nueva2]):
            if not password_actual:
                messages.error(request, "Debes capturar tu contraseña actual.")
                return redirect("participante:configuracion")

            if not user.check_password(password_actual):
                messages.error(request, "La contraseña actual no es correcta.")
                return redirect("participante:configuracion")

            if not password_nueva1 or not password_nueva2:
                messages.error(request, "Debes capturar y confirmar la nueva contraseña.")
                return redirect("participante:configuracion")

            if password_nueva1 != password_nueva2:
                messages.error(request, "La confirmación de contraseña no coincide.")
                return redirect("participante:configuracion")

            if len(password_nueva1) < 8:
                messages.error(request, "La nueva contraseña debe tener al menos 8 caracteres.")
                return redirect("participante:configuracion")

            user.set_password(password_nueva1)

        user.first_name = nombres
        user.last_name = apellidos
        user.email = correo
        user.save()

        perfil.telefono = telefono
        perfil.institucion = institucion
        perfil.carrera = carrera
        perfil.bio = bio

        if request.FILES.get("avatar"):
            perfil.avatar = request.FILES["avatar"]

        perfil.save()

        if any([password_actual, password_nueva1, password_nueva2]):
            update_session_auth_hash(request, user)

        messages.success(request, "La configuración del participante fue actualizada correctamente.")
        return redirect("participante:configuracion")

    return render(request, "participante/configuracion/configuracion.html", {
        "active": "configuracion",
        "perfil": perfil,
    })