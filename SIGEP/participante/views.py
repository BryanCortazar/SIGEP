from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Avg
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from administrador.models import Evento
from coordinador.models import Inscripcion
from evaluador.models import EvaluacionAsignacion, EvaluacionEntrega

from .forms import (
    GestionParticipacionForm,
    ParticipanteCuentaForm,
    ParticipantePerfilForm,
    ProyectoParticipanteForm,
    SeleccionEventoForm,
)
from .models import (
    ConstanciaParticipante,
    PaseAccesoParticipante,
    PerfilParticipante,
    ProyectoParticipante,
)

try:
    from administrador.utils_auditoria import registrar_auditoria
except Exception:  # pragma: no cover
    def registrar_auditoria(**kwargs):
        return None


try:
    import qrcode
    import qrcode.image.svg
except Exception:  # pragma: no cover
    qrcode = None


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


def _resolve_pase_acceso(proyecto: ProyectoParticipante | None) -> PaseAccesoParticipante | None:
    if proyecto is None:
        return None
    pase, _ = PaseAccesoParticipante.objects.get_or_create(proyecto=proyecto)
    return pase


def _build_qr_svg(data: str) -> str:
    if qrcode is None:
        return ""

    factory = qrcode.image.svg.SvgPathImage
    imagen = qrcode.make(data, image_factory=factory, box_size=8, border=3)
    output = BytesIO()
    imagen.save(output)
    return output.getvalue().decode("utf-8")


def _resolve_constancia_project(user):
    return (
        ProyectoParticipante.objects.filter(participante=user)
        .exclude(estado=ProyectoParticipante.ESTADO_RECHAZADO)
        .select_related("evento", "evaluacion_proyecto")
        .order_by("-actualizado_en")
        .first()
    )


def _build_constancia_state(user, proyecto: ProyectoParticipante | None):
    perfil = _resolve_profile(user)
    data = {
        "perfil": perfil,
        "proyecto": proyecto,
        "constancia": None,
        "promedio": None,
        "total_evaluadores": 0,
        "evaluaciones_enviadas": 0,
        "evaluacion_concluida": False,
        "porcentaje_evaluacion": 0,
        "nombre_participante": user.get_full_name().strip() or getattr(user, "username", ""),
        "institucion": getattr(perfil, "institucion", "") or "",
        "telefono_contacto": getattr(perfil, "telefono", "") or "",
        "correo_contacto": getattr(user, "email", "") or "",
        "evento_nombre": "",
        "fecha_evento": None,
    }

    if proyecto is None:
        return data

    data["constancia"] = getattr(proyecto, "constancia_generada", None)
    data["nombre_participante"] = (
        proyecto.nombre_participante.strip()
        or user.get_full_name().strip()
        or getattr(user, "username", "")
    )
    data["institucion"] = (
        proyecto.institucion_empresa.strip()
        or getattr(perfil, "institucion", "")
        or "No especificada"
    )
    data["telefono_contacto"] = proyecto.telefono or getattr(perfil, "telefono", "") or "—"
    data["correo_contacto"] = proyecto.correo or getattr(user, "email", "") or "—"
    data["evento_nombre"] = proyecto.nombre_evento()
    data["fecha_evento"] = getattr(proyecto.evento, "fecha", None)

    if not proyecto.evaluacion_proyecto_id:
        return data

    asignaciones = EvaluacionAsignacion.objects.filter(proyecto=proyecto.evaluacion_proyecto)
    total_evaluadores = asignaciones.count()
    entregas_enviadas = EvaluacionEntrega.objects.filter(
        asignacion__proyecto=proyecto.evaluacion_proyecto,
        estado=EvaluacionEntrega.ESTADO_ENVIADA,
    )
    evaluaciones_enviadas = entregas_enviadas.count()

    promedio = entregas_enviadas.aggregate(v=Avg("calificacion"))["v"]
    if promedio is not None:
        promedio = Decimal(promedio).quantize(Decimal("0.01"))

    data.update({
        "promedio": promedio,
        "total_evaluadores": total_evaluadores,
        "evaluaciones_enviadas": evaluaciones_enviadas,
        "evaluacion_concluida": total_evaluadores > 0 and evaluaciones_enviadas == total_evaluadores,
        "porcentaje_evaluacion": int((evaluaciones_enviadas / total_evaluadores) * 100) if total_evaluadores else 0,
    })
    return data


@login_required
def panel_participante(request):
    qs = ProyectoParticipante.objects.select_related("evento", "evaluacion_proyecto").filter(participante=request.user)
    hoy = timezone.localdate()

    proximo_obj = (
        qs.filter(fecha_programada__gte=hoy, hora_inicio__isnull=False)
        .exclude(estado_programacion=ProyectoParticipante.PROG_CANCELADO)
        .order_by("fecha_programada", "hora_inicio", "id")
        .first()
    ) or qs.order_by("-actualizado_en").first()

    proximo = None
    if proximo_obj:
        proximo = {
            "nombre_proyecto": proximo_obj.nombre_proyecto,
            "nombre_evento": proximo_obj.nombre_evento(),
            "fecha_programada": proximo_obj.fecha_programada,
            "hora_inicio": proximo_obj.hora_inicio,
            "hora_fin": proximo_obj.hora_fin,
            "espacio_asignado": proximo_obj.espacio_asignado,
        }

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
    evento_sel = None
    evento_modal = None
    abrir_modal = False

    evento_id_get = request.GET.get("evento")
    if evento_id_get:
        try:
            evento_sel = eventos.get(pk=int(evento_id_get))
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
            abrir_modal = True
            evento_id_post = request.POST.get("evento_id")
            if evento_id_post:
                try:
                    evento_modal = eventos.get(pk=int(evento_id_post))
                except (ValueError, Evento.DoesNotExist):
                    evento_modal = None
            else:
                evento_modal = evento_sel

            if not evento_modal:
                messages.error(request, "Debes seleccionar un evento publicado antes de registrar tu proyecto.")
            else:
                evento_sel = evento_modal
                form = ProyectoParticipanteForm(
                    request.POST,
                    request.FILES,
                    evento=evento_modal,
                    usuario=request.user,
                )
                if form.is_valid():
                    proyecto = form.save(commit=False)
                    proyecto.evento = evento_modal
                    proyecto.participante = request.user
                    proyecto.estado = ProyectoParticipante.ESTADO_REGISTRADO
                    try:
                        with transaction.atomic():
                            proyecto.save()
                            _asegurar_inscripcion_participante(evento_modal, request.user)
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
                        return redirect(f"{request.path}?evento={evento_modal.id}")
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
def gestionar_participacion(request):
    proyectos = (
        ProyectoParticipante.objects.filter(participante=request.user)
        .exclude(estado=ProyectoParticipante.ESTADO_RECHAZADO)
        .select_related("evento")
        .order_by("-actualizado_en", "-id")
    )

    proyecto_sel = None
    proyecto_id = request.GET.get("proyecto") or request.POST.get("proyecto_id")
    if proyecto_id:
        try:
            proyecto_sel = proyectos.get(pk=int(proyecto_id))
        except (ValueError, ProyectoParticipante.DoesNotExist):
            messages.error(request, "El proyecto seleccionado no es válido para tu cuenta.")

    if proyecto_sel is None:
        proyecto_sel = proyectos.first()

    if request.method == "POST" and proyecto_sel is not None:
        form = GestionParticipacionForm(request.POST, request.FILES, instance=proyecto_sel)
        if not proyecto_sel.puede_editar():
            messages.error(request, "Este proyecto ya no permite actualizar documentación desde esta opción.")
            return redirect(f"{request.path}?proyecto={proyecto_sel.id}")

        if form.is_valid():
            proyecto_actualizado = form.save()
            registrar_auditoria(
                request=request,
                accion=f"GESTION_PARTICIPACION | EDITAR | {proyecto_actualizado.nombre_proyecto}",
                modulo="PARTICIPANTE",
                accion_tipo="EDITAR",
                entidad="ProyectoParticipante",
                objeto_id=proyecto_actualizado.pk,
                resultado="EXITOSO",
            )
            messages.success(request, "La documentación y los requerimientos técnicos se actualizaron correctamente.")
            return redirect(f"{request.path}?proyecto={proyecto_actualizado.id}")

        messages.error(request, "No fue posible actualizar la participación. Revisa los campos marcados.")
    else:
        form = GestionParticipacionForm(instance=proyecto_sel) if proyecto_sel is not None else None

    resumen = None
    if proyecto_sel is not None:
        resumen = {
            "evento": proyecto_sel.nombre_evento(),
            "proyecto": proyecto_sel.nombre_proyecto,
            "categoria": proyecto_sel.get_categoria_display(),
            "estado": proyecto_sel.get_estado_display(),
            "programacion": proyecto_sel.get_estado_programacion_display(),
            "avance": proyecto_sel.porcentaje_documentacion(),
            "presentacion_cargada": bool(proyecto_sel.presentacion),
            "informe_cargado": bool(proyecto_sel.informe),
            "requiere_apoyo": bool((proyecto_sel.requerimientos_tecnicos or "").strip()),
            "edicion_habilitada": proyecto_sel.puede_editar(),
        }

    return render(request, "participante/gestion/gestionar_participacion.html", {
        "active": "gestionar_participacion",
        "proyectos": proyectos,
        "proyecto_sel": proyecto_sel,
        "form": form,
        "resumen": resumen,
    })


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

    pase = _resolve_pase_acceso(proyecto)
    qr_svg = ""
    validacion_url = ""

    if pase is not None:
        validacion_url = request.build_absolute_uri(pase.url_validacion())
        qr_svg = _build_qr_svg(validacion_url)

    return render(request, "participante/pase/pase.html", {
        "active": "mi_pase",
        "proyecto": proyecto,
        "pase": pase,
        "qr_svg": qr_svg,
        "validacion_url": validacion_url,
        "qr_disponible": bool(qr_svg),
    })


def validar_pase_qr(request, token):
    pase = get_object_or_404(
        PaseAccesoParticipante.objects.select_related("proyecto", "proyecto__evento", "proyecto__participante"),
        token=token,
    )

    if pase.activo:
        pase.registrar_escaneo()

    proyecto = pase.proyecto
    return render(request, "participante/pase/validacion_qr.html", {
        "pase": pase,
        "proyecto": proyecto,
        "es_valido": pase.activo and proyecto.estado != ProyectoParticipante.ESTADO_RECHAZADO,
    })


@login_required
def constancia(request):
    proyecto = _resolve_constancia_project(request.user)
    contexto = _build_constancia_state(request.user, proyecto)
    contexto.update({
        "active": "constancia",
    })
    return render(request, "participante/constancia/constancia.html", contexto)


@login_required
def constancia_previa(request):
    proyecto = _resolve_constancia_project(request.user)
    contexto = _build_constancia_state(request.user, proyecto)

    if proyecto is None:
        messages.error(request, "Aún no cuentas con un proyecto registrado para generar la constancia.")
        return redirect("participante:constancia")

    if not contexto["evaluacion_concluida"]:
        messages.error(request, "La constancia se habilita cuando todos los evaluadores asignados han enviado su evaluación.")
        return redirect("participante:constancia")

    constancia, _ = ConstanciaParticipante.objects.get_or_create(proyecto=proyecto)
    contexto.update({
        "constancia": constancia,
        "auto_print": request.GET.get("print") == "1",
    })
    return render(request, "participante/constancia/constancia_preview.html", contexto)


@login_required
def configuracion(request):
    perfil = _resolve_profile(request.user)

    if request.method == "POST":
        cuenta_form = ParticipanteCuentaForm(request.POST, user=request.user)
        perfil_form = ParticipantePerfilForm(request.POST, request.FILES, instance=perfil)
        if cuenta_form.is_valid() and perfil_form.is_valid():
            cuenta_form.save()
            perfil_form.save()

            password_nueva = cuenta_form.cleaned_data.get("password_nueva")
            if password_nueva:
                request.user.set_password(password_nueva)
                request.user.save(update_fields=["password"])
                update_session_auth_hash(request, request.user)

            messages.success(request, "Tu información fue actualizada correctamente.")
            return redirect("participante:configuracion")

        messages.error(request, "No fue posible actualizar tu información. Revisa los campos marcados.")
    else:
        cuenta_form = ParticipanteCuentaForm(user=request.user)
        perfil_form = ParticipantePerfilForm(instance=perfil)

    return render(request, "participante/configuracion/configuracion.html", {
        "active": "configuracion",
        "cuenta_form": cuenta_form,
        "perfil_form": perfil_form,
        "perfil": perfil,
    })