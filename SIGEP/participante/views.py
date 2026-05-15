from __future__ import annotations

from decimal import Decimal
from django.conf import settings
from io import BytesIO
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Avg, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.utils import timezone

from administrador.models import Evento
from coordinador.models import Inscripcion, EvaluacionProyecto as CoordEvaluacionProyecto
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

from xhtml2pdf import pisa


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


def _sync_project_to_coordinador_record(
    proyecto: ProyectoParticipante,
    titulo_anterior: str | None = None,
) -> CoordEvaluacionProyecto:
    """
    Crea o actualiza el registro puente que consume el módulo coordinador.
    Con esto el proyecto aparece desde el registro inicial en espacios,
    evaluadores y rúbricas.
    """
    titulo = (proyecto.nombre_proyecto or "").strip()
    responsable = (
        (proyecto.nombre_participante or "").strip()
        or proyecto.participante.get_full_name().strip()
        or getattr(proyecto.participante, "username", "")
    )

    filtros = Q(titulo__iexact=titulo)
    if titulo_anterior:
        filtros |= Q(titulo__iexact=(titulo_anterior or "").strip())

    registro = (
        CoordEvaluacionProyecto.objects.filter(evento=proyecto.evento)
        .filter(filtros)
        .order_by("id")
        .first()
    )

    if registro is None:
        defaults = {
            "ponente": responsable,
            # Campos mínimos para no romper la lógica de coordinador;
            # el horario real se completa cuando el coordinador programa.
            "inicio": proyecto.hora_inicio or timezone.datetime.strptime("08:00", "%H:%M").time(),
            "fin": proyecto.hora_fin or timezone.datetime.strptime("08:30", "%H:%M").time(),
            "lugar": proyecto.espacio_asignado or "",
        }
        registro = CoordEvaluacionProyecto.objects.create(
            evento=proyecto.evento,
            titulo=titulo,
            **defaults,
        )
    else:
        changed = False
        if registro.titulo != titulo:
            registro.titulo = titulo
            changed = True
        if (registro.ponente or "") != responsable:
            registro.ponente = responsable
            changed = True
        if changed:
            registro.save(update_fields=["titulo", "ponente", "actualizado_en"])

    # OJO: ProyectoParticipante.evaluacion_proyecto apunta a evaluador.EvaluacionProyecto,
    # no a coordinador.EvaluacionProyecto. Aquí solo creamos/actualizamos el registro
    # puente del coordinador para que aparezca en espacios/evaluadores/rúbricas.
    # El enlace a evaluador.EvaluacionProyecto se resuelve después, cuando el
    # coordinador programa y sincroniza hacia el módulo evaluador.
    return registro


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



def _build_pase_validacion_url(request, pase: PaseAccesoParticipante) -> str:
    """
    Construye la URL pública de validación del QR.

    En desarrollo puede usar request.build_absolute_uri().
    Para pruebas con celular o producción, se puede definir en settings.py:
        PASE_QR_BASE_URL = "http://192.168.1.20:8000"
    o:
        SITE_URL = "https://tudominio.edu.mx"
    """
    path = pase.url_validacion()
    base_url = (
        getattr(settings, "PASE_QR_BASE_URL", "")
        or getattr(settings, "SITE_URL", "")
        or ""
    ).strip().rstrip("/")

    if base_url:
        return f"{base_url}{path}"

    return request.build_absolute_uri(path)


def _build_pase_context(pase: PaseAccesoParticipante | None, proyecto: ProyectoParticipante | None, *, es_valido: bool, motivo: str = "") -> dict:
    """
    Contexto enriquecido para el pase y para la página pública de validación.
    """
    evento = getattr(proyecto, "evento", None) if proyecto else None
    participante = getattr(proyecto, "participante", None) if proyecto else None

    nombre_participante = ""
    if proyecto:
        nombre_participante = (proyecto.nombre_participante or "").strip()
    if not nombre_participante and participante:
        nombre_participante = participante.get_full_name().strip() or getattr(participante, "username", "")

    horario = "Pendiente"
    if proyecto and proyecto.hora_inicio and proyecto.hora_fin:
        horario = f"{proyecto.hora_inicio.strftime('%H:%M')} - {proyecto.hora_fin.strftime('%H:%M')}"

    return {
        "pase": pase,
        "proyecto": proyecto,
        "evento": evento,
        "participante": participante,
        "es_valido": es_valido,
        "motivo": motivo,
        "nombre_participante": nombre_participante or "Participante",
        "correo_participante": getattr(proyecto, "correo", "") if proyecto else "",
        "telefono_participante": getattr(proyecto, "telefono", "") if proyecto else "",
        "institucion": getattr(proyecto, "institucion_empresa", "") if proyecto else "",
        "evento_nombre": proyecto.nombre_evento() if proyecto else "Evento no disponible",
        "proyecto_nombre": getattr(proyecto, "nombre_proyecto", "") if proyecto else "Proyecto no disponible",
        "categoria": proyecto.get_categoria_display() if proyecto else "",
        "estado_proyecto": proyecto.get_estado_display() if proyecto else "No disponible",
        "estado_programacion": proyecto.get_estado_programacion_display() if proyecto else "No disponible",
        "fecha_programada": getattr(proyecto, "fecha_programada", None) if proyecto else None,
        "horario": horario,
        "espacio": getattr(proyecto, "espacio_asignado", "") if proyecto else "",
        "numero_integrantes": getattr(proyecto, "numero_integrantes", None) if proyecto else None,
        "total_escaneos": getattr(pase, "total_escaneos", 0) if pase else 0,
        "ultimo_escaneo": getattr(pase, "ultimo_escaneo", None) if pase else None,
        "pase_activo": bool(getattr(pase, "activo", False)) if pase else False,
        "token": str(getattr(pase, "token", "")) if pase else "",
    }

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



def _validar_gestion_documental_participante(proyecto: ProyectoParticipante, post_data, files) -> list[str]:
    """
    Valida que la gestión de participación del participante no se guarde incompleta.
    La información básica del proyecto se registra en "Elegir evento"; esta sección
    se enfoca en documentos y requerimientos técnicos.
    """
    errores = []

    limpiar_presentacion = post_data.get("presentacion-clear") in {"on", "true", "1"}
    limpiar_informe = post_data.get("informe-clear") in {"on", "true", "1"}

    tiene_presentacion = bool(files.get("presentacion") or (getattr(proyecto, "presentacion", None) and not limpiar_presentacion))
    tiene_informe = bool(files.get("informe") or (getattr(proyecto, "informe", None) and not limpiar_informe))
    requerimientos = (post_data.get("requerimientos_tecnicos") or "").strip()

    if not tiene_presentacion:
        errores.append("Debes cargar la presentación del proyecto en formato PDF, PPT o PPTX.")
    if not tiene_informe:
        errores.append("Debes cargar el informe o documentación del proyecto en formato PDF, DOC o DOCX.")
    if not requerimientos:
        errores.append("Debes capturar los requerimientos técnicos del proyecto.")

    return errores


def _build_resumen_gestion_participante(proyecto: ProyectoParticipante | None):
    if proyecto is None:
        return None

    return {
        "evento": proyecto.nombre_evento(),
        "proyecto": proyecto.nombre_proyecto,
        "categoria": proyecto.get_categoria_display(),
        "estado": proyecto.get_estado_display(),
        "programacion": proyecto.get_estado_programacion_display(),
        "avance": proyecto.porcentaje_documentacion(),
        "presentacion_cargada": bool(proyecto.presentacion),
        "informe_cargado": bool(proyecto.informe),
        "requiere_apoyo": bool((proyecto.requerimientos_tecnicos or "").strip()),
        "edicion_habilitada": proyecto.puede_editar(),
    }



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
                            _sync_project_to_coordinador_record(proyecto)

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
                titulo_anterior = proyecto.nombre_proyecto
                with transaction.atomic():
                    proyecto = form.save()
                    _asegurar_inscripcion_participante(evento_sel, request.user)
                    _sync_project_to_coordinador_record(proyecto, titulo_anterior=titulo_anterior)
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

    errores_documentacion = []

    if request.method == "POST" and proyecto_sel is not None:
        form = GestionParticipacionForm(request.POST, request.FILES, instance=proyecto_sel)

        if not proyecto_sel.puede_editar():
            messages.error(request, "Este proyecto ya no permite actualizar documentación desde esta opción.")
            return redirect(f"{request.path}?proyecto={proyecto_sel.id}")

        errores_documentacion = _validar_gestion_documental_participante(proyecto_sel, request.POST, request.FILES)
        if errores_documentacion:
            for error in errores_documentacion:
                messages.error(request, error)

            return render(request, "participante/gestion/gestionar_participacion.html", {
                "active": "gestionar_participacion",
                "proyectos": proyectos,
                "proyecto_sel": proyecto_sel,
                "form": form,
                "resumen": _build_resumen_gestion_participante(proyecto_sel),
                "errores_documentacion": errores_documentacion,
            })

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

    resumen = _build_resumen_gestion_participante(proyecto_sel)

    return render(request, "participante/gestion/gestionar_participacion.html", {
        "active": "gestionar_participacion",
        "proyectos": proyectos,
        "proyecto_sel": proyecto_sel,
        "form": form,
        "resumen": resumen,
        "errores_documentacion": errores_documentacion,
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
        .select_related("evento", "participante")
        .order_by("-actualizado_en")
        .first()
    )

    pase = _resolve_pase_acceso(proyecto)
    qr_svg = ""
    validacion_url = ""

    if pase is not None:
        validacion_url = _build_pase_validacion_url(request, pase)
        qr_svg = _build_qr_svg(validacion_url)

    contexto = _build_pase_context(
        pase,
        proyecto,
        es_valido=bool(pase and pase.activo and proyecto and proyecto.estado != ProyectoParticipante.ESTADO_RECHAZADO),
        motivo="" if pase else "Aún no existe un proyecto activo para generar el pase.",
    )
    contexto.update({
        "active": "mi_pase",
        "qr_svg": qr_svg,
        "validacion_url": validacion_url,
        "qr_disponible": bool(qr_svg),
    })

    return render(request, "participante/pase/pase.html", contexto)


def validar_pase_qr(request, token):
    pase = (
        PaseAccesoParticipante.objects
        .select_related("proyecto", "proyecto__evento", "proyecto__participante")
        .filter(token=token)
        .first()
    )

    if pase is None:
        return render(request, "participante/pase/validacion_qr.html", _build_pase_context(
            None,
            None,
            es_valido=False,
            motivo="El código QR no corresponde a un pase registrado en SIGEP.",
        ))

    proyecto = pase.proyecto
    es_valido = bool(pase.activo and proyecto.estado != ProyectoParticipante.ESTADO_RECHAZADO)
    motivo = ""

    if not pase.activo:
        motivo = "El pase se encuentra inactivo."
    elif proyecto.estado == ProyectoParticipante.ESTADO_RECHAZADO:
        motivo = "El proyecto asociado al pase fue rechazado."

    if es_valido:
        pase.registrar_escaneo()
        # Recargar datos para mostrar total_escaneos y ultimo_escaneo actualizados.
        pase.refresh_from_db()

    return render(request, "participante/pase/validacion_qr.html", _build_pase_context(
        pase,
        proyecto,
        es_valido=es_valido,
        motivo=motivo,
    ))


@login_required
def constancia(request):
    proyecto = _resolve_constancia_project(request.user)
    contexto = _build_constancia_state(request.user, proyecto)
    contexto.update({
        "active": "constancia",
        "descarga_disponible": bool(proyecto and contexto["evaluacion_concluida"] and not contexto.get("constancia")),
        "constancia_emitida": bool(contexto.get("constancia")),
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

    contexto.update({
        "auto_print": False,
        "descarga_disponible": not bool(contexto.get("constancia")),
        "constancia_emitida": bool(contexto.get("constancia")),
        "fecha_emision": timezone.now(),
    })
    return render(request, "participante/constancia/constancia_preview.html", contexto)



@login_required
def descargar_constancia_pdf(request):
    proyecto = _resolve_constancia_project(request.user)
    contexto = _build_constancia_state(request.user, proyecto)

    if proyecto is None:
        messages.error(request, "Aún no cuentas con un proyecto registrado para generar la constancia.")
        return redirect("participante:constancia")

    if not contexto["evaluacion_concluida"]:
        messages.error(request, "La constancia solo puede descargarse cuando todas las evaluaciones han concluido.")
        return redirect("participante:constancia")

    if contexto.get("constancia"):
        messages.error(request, "La constancia ya fue emitida y solo puede descargarse una vez.")
        return redirect("participante:constancia")

    folio = ConstanciaParticipante._meta.get_field("folio").get_default()
    emitida_en = timezone.now()
    constancia_virtual = SimpleNamespace(folio=folio, emitida_en=emitida_en)

    contexto.update({
        "constancia": constancia_virtual,
        "fecha_emision": timezone.localdate(),
    })

    template = get_template("participante/constancia/constancia_pdf.html")
    html = template.render(contexto)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="constancia_participante_{proyecto.id}.pdf"'

    pisa_status = pisa.CreatePDF(src=html, dest=response, encoding="utf-8")
    if pisa_status.err:
        return HttpResponse("No fue posible generar la constancia PDF.", status=500)

    ConstanciaParticipante.objects.create(
        proyecto=proyecto,
        folio=folio,
        emitida_en=emitida_en,
    )
    return response


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
