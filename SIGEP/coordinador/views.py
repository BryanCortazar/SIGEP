from __future__ import annotations

import csv
import io
import os

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ActividadCronogramaForm,
    EvaluacionProyectoForm,
    InscripcionUsuarioForm,
    RubricaForm,
    EspacioForm,
    ReporteGenerarForm,
    ReporteEditarForm,
    ConfigCuentaForm,
    ConfigPerfilForm,
)
from .models import (
    ActividadCronograma,
    EvaluacionAsignacion,
    EvaluacionProyecto,
    Inscripcion,
    Rubrica,
    RubricaAdjunto,
    RubricaCriterio,
    Espacio,
    Reporte,
    PerfilUsuario,
)

User = get_user_model()


# =========================================================
# Helpers: Evento actual desde sesión + permisos
# =========================================================
def _get_evento_model():
    """
    Resuelve el modelo Evento sin romper el server aunque cambie de app.
    Ajusta el orden según tu arquitectura real.
    """
    try:
        from .models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    try:
        from administrador.models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    try:
        from eventos.models import Evento  # type: ignore
        return Evento
    except Exception:
        pass

    return None


def _get_evento_id_from_session(request: HttpRequest) -> int | None:
    eid = request.session.get("evento_id") or request.session.get("evento_actual_id")
    try:
        return int(eid) if eid else None
    except Exception:
        return None


def _get_evento_actual(request: HttpRequest):
    Evento = _get_evento_model()
    if not Evento:
        return None

    evento_id = _get_evento_id_from_session(request)
    if not evento_id:
        return None

    return Evento.objects.filter(id=evento_id).first()


def _user_can_manage_evento(user, evento) -> bool:
    """
    Permiso: coordinador/admin inscrito en el evento, o creador si existe creado_por.
    """
    if not user or not getattr(user, "is_authenticated", False) or not evento:
        return False

    permitido = Inscripcion.objects.filter(
        evento=evento,
        usuario=user,
        rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
    ).exists()
    if permitido:
        return True

    try:
        field_names = {f.name for f in evento._meta.fields}
        if "creado_por" in field_names and getattr(evento, "creado_por_id", None) == user.id:
            return True
    except Exception:
        pass

    return False


def _require_evento_or_redirect(request: HttpRequest):
    evento = _get_evento_actual(request)
    if not evento:
        messages.warning(request, "Selecciona un evento antes de continuar.")
        return None, redirect("coordinador:dashboard")

    if not _user_can_manage_evento(request.user, evento):
        request.session.pop("evento_id", None)
        request.session.pop("evento_actual_id", None)
        request.session.modified = True
        messages.error(request, "No tienes permisos para gestionar ese evento.")
        return None, redirect("coordinador:dashboard")

    return evento, None


# =========================================================
# PDF mínimo (sin dependencias externas)
# =========================================================
def _build_minimal_pdf(lines: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y_start = 760
    leading = 14
    text_ops = ["BT", "/F1 12 Tf", f"72 {y_start} Td"]
    for i, line in enumerate(lines[:55]):
        if i > 0:
            text_ops.append(f"0 -{leading} Td")
        text_ops.append(f"({esc(line)}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("latin-1", "ignore")

    objs = []
    objs.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objs.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objs.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources<< /Font<< /F1 4 0 R >> >> /Contents 5 0 R >>endobj\n"
    )
    objs.append(b"4 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
    objs.append(
        f"5 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n"
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    xref = [0]
    for obj in objs:
        xref.append(out.tell())
        out.write(obj)

    xref_pos = out.tell()
    out.write(f"xref\n0 {len(xref)}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for pos in xref[1:]:
        out.write(f"{pos:010d} 00000 n \n".encode("ascii"))

    out.write(b"trailer<< ")
    out.write(f"/Size {len(xref)} /Root 1 0 R".encode("ascii"))
    out.write(b" >>\nstartxref\n")
    out.write(f"{xref_pos}\n%%EOF".encode("ascii"))
    return out.getvalue()


def _xlsx_from_rows(sheet_name: str, headers: list[str], rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook  # type: ignore

    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Reporte")[:31]
    ws.append(headers)
    for r in rows:
        ws.append(r)

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# =========================================================
# Dashboard (Panel de coordinación)
# =========================================================
@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    Evento = _get_evento_model()
    eventos = []

    if Evento:
        field_names = {f.name for f in Evento._meta.fields}
        if "creado_por" in field_names:
            eventos = Evento.objects.filter(creado_por=request.user).order_by("-id")
        else:
            eventos_ids = Inscripcion.objects.filter(
                usuario=request.user,
                rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
            ).values_list("evento_id", flat=True)
            eventos = Evento.objects.filter(id__in=eventos_ids).order_by("-id")

    evento = _get_evento_actual(request)
    return render(
        request,
        "coordinador/dashboard/index.html",
        {"active": "dashboard", "eventos": eventos, "evento": evento},
    )


# =========================================================
# EVENTOS (crear / seleccionar / gestión)
# =========================================================
@login_required
@require_POST
@transaction.atomic
def evento_crear(request: HttpRequest) -> HttpResponse:
    Evento = _get_evento_model()
    if not Evento:
        messages.error(request, "No se encontró el modelo Evento. Revisa dónde está definido.")
        return redirect("coordinador:dashboard")

    titulo = (request.POST.get("titulo") or "").strip()
    descripcion = (request.POST.get("descripcion") or "").strip()
    fecha = request.POST.get("fecha")
    lugar = (request.POST.get("lugar") or "").strip()
    cupo = request.POST.get("cupo")

    if not titulo or not fecha:
        messages.error(request, "Título y fecha son obligatorios.")
        return redirect("coordinador:dashboard")

    kwargs = {"titulo": titulo, "descripcion": descripcion, "fecha": fecha, "lugar": lugar}
    if cupo and str(cupo).isdigit():
        kwargs["cupo"] = int(cupo)

    field_names = {f.name for f in Evento._meta.fields}
    if "creado_por" in field_names:
        kwargs["creado_por"] = request.user

    evento = Evento.objects.create(**kwargs)

    Inscripcion.objects.get_or_create(
        evento=evento,
        usuario=request.user,
        defaults={"rol": Inscripcion.ROL_COORDINADOR},
    )

    request.session["evento_id"] = evento.id
    request.session.modified = True
    messages.success(request, "Evento creado y seleccionado correctamente.")
    return redirect("coordinador:dashboard")


@login_required
@require_POST
@transaction.atomic
def evento_seleccionar(request: HttpRequest) -> HttpResponse:
    Evento = _get_evento_model()
    if not Evento:
        messages.error(request, "No se encontró el modelo Evento. Revisa dónde está definido.")
        return redirect("coordinador:dashboard")

    evento_id = (request.POST.get("evento_id") or "").strip()
    if not evento_id.isdigit():
        messages.error(request, "Selecciona un evento válido.")
        return redirect("coordinador:dashboard")

    evento = Evento.objects.filter(id=int(evento_id)).first()
    if not evento:
        messages.error(request, "El evento seleccionado no existe.")
        return redirect("coordinador:dashboard")

    if not _user_can_manage_evento(request.user, evento):
        messages.error(request, "No tienes permisos para gestionar este evento.")
        return redirect("coordinador:dashboard")

    request.session["evento_id"] = evento.id
    request.session.modified = True
    messages.success(request, f"Evento seleccionado: {getattr(evento, 'titulo', 'Evento')}")
    return redirect("coordinador:dashboard")


@login_required
def gestion_evento(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir
    return render(request, "coordinador/eventos/gestion.html", {"active": "eventos", "evento": evento})


# =========================================================
# CRONOGRAMA
# =========================================================
@login_required
def cronograma(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    actividades = ActividadCronograma.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = ActividadCronogramaForm()
    return render(
        request,
        "coordinador/cronograma/cronograma.html",
        {"active": "cronograma", "evento": evento, "actividades": actividades, "form": form},
    )


@login_required
@require_POST
@transaction.atomic
def cronograma_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    form = ActividadCronogramaForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Verifica los datos del cronograma.")
        return redirect("coordinador:cronograma")

    actividad_id = (request.POST.get("actividad_id") or "").strip()

    if actividad_id:
        actividad = get_object_or_404(ActividadCronograma, id=actividad_id, evento=evento)
        for k, v in form.cleaned_data.items():
            setattr(actividad, k, v)
        actividad.save()
        messages.success(request, "Actividad actualizada correctamente.")
        return redirect("coordinador:cronograma")

    ActividadCronograma.objects.create(evento=evento, **form.cleaned_data)
    messages.success(request, "Actividad agregada correctamente.")
    return redirect("coordinador:cronograma")


@login_required
@require_POST
@transaction.atomic
def cronograma_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    actividad = get_object_or_404(ActividadCronograma, id=pk, evento=evento)
    actividad.delete()
    messages.success(request, "Actividad eliminada correctamente.")
    return redirect("coordinador:cronograma")


# =========================================================
# INSCRIPCIONES
# =========================================================
@login_required
def inscripciones(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    q = (request.GET.get("q") or "").strip()
    rol = (request.GET.get("rol") or "").strip()
    activo = (request.GET.get("activo") or "").strip()

    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento)

    if q:
        qs = qs.filter(
            Q(usuario__first_name__icontains=q)
            | Q(usuario__last_name__icontains=q)
            | Q(usuario__email__icontains=q)
            | Q(rol__icontains=q)
        )
    if rol:
        qs = qs.filter(rol=rol)
    if activo in ("0", "1"):
        qs = qs.filter(usuario__is_active=(activo == "1"))

    return render(
        request,
        "coordinador/inscripciones/inscripciones.html",
        {
            "active": "inscripciones",
            "evento": evento,
            "inscripciones": qs.order_by("-id"),
            "form": InscripcionUsuarioForm(),
            "q": q,
            "rol": rol,
            "activo": activo,
        },
    )


@login_required
@require_POST
@transaction.atomic
def inscripcion_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    form = InscripcionUsuarioForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Verifica los datos del usuario/inscripción.")
        return redirect("coordinador:inscripciones")

    user_id = form.cleaned_data.get("user_id")
    nombres = form.cleaned_data["nombres"].strip()
    apellidos = form.cleaned_data["apellidos"].strip()
    correo = form.cleaned_data["correo"].strip().lower()
    rol = form.cleaned_data["rol"]
    activo = bool(form.cleaned_data.get("activo"))
    password = (form.cleaned_data.get("password") or "").strip()

    if user_id:
        user = get_object_or_404(User, id=user_id)
        user.first_name = nombres
        user.last_name = apellidos
        user.email = correo
        if hasattr(user, "username"):
            user.username = correo
        user.is_active = activo
        if password:
            user.set_password(password)
        user.save()

        insc = Inscripcion.objects.filter(evento=evento, usuario=user).first()
        if insc:
            insc.rol = rol
            insc.save(update_fields=["rol"])

        messages.success(request, "Inscripción actualizada correctamente.")
        return redirect("coordinador:inscripciones")

    if User.objects.filter(email__iexact=correo).exists():
        messages.error(request, "Ya existe un usuario registrado con ese correo.")
        return redirect("coordinador:inscripciones")

    if not password:
        messages.error(request, "La contraseña es obligatoria para crear el usuario.")
        return redirect("coordinador:inscripciones")

    user = User(first_name=nombres, last_name=apellidos, email=correo, is_active=activo)
    if hasattr(user, "username"):
        user.username = correo
    user.set_password(password)
    user.save()

    Inscripcion.objects.create(evento=evento, usuario=user, rol=rol)
    messages.success(request, "Inscripción creada correctamente.")
    return redirect("coordinador:inscripciones")


@login_required
@require_POST
@transaction.atomic
def inscripcion_eliminar(request: HttpRequest, user_id: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    user = get_object_or_404(User, id=user_id)
    insc = Inscripcion.objects.filter(evento=evento, usuario=user).first()
    if not insc:
        messages.error(request, "No se encontró la inscripción a eliminar.")
        return redirect("coordinador:inscripciones")

    insc.delete()

    if not Inscripcion.objects.filter(usuario=user).exists():
        user.is_active = False
        user.save(update_fields=["is_active"])

    messages.success(request, "Inscripción eliminada correctamente.")
    return redirect("coordinador:inscripciones")


@login_required
def inscripciones_exportar_csv(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento).order_by("-id")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="inscripciones_evento_{evento.id}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Nombres", "Apellidos", "Correo", "Rol", "Activo", "Evento"])

    for insc in qs:
        u = insc.usuario
        writer.writerow(
            [
                (u.first_name or ""),
                (u.last_name or ""),
                (u.email or ""),
                (insc.rol or ""),
                ("SI" if u.is_active else "NO"),
                getattr(evento, "titulo", f"Evento {evento.id}"),
            ]
        )
    return response


# =========================================================
# EVALUADORES
# =========================================================
@login_required
def evaluadores(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyectos = (
        EvaluacionProyecto.objects.filter(evento=evento)
        .prefetch_related("asignaciones__evaluador")
        .order_by("inicio", "fin", "id")
    )

    evaluadores_disponibles = (
        Inscripcion.objects.select_related("usuario")
        .filter(evento=evento, rol=Inscripcion.ROL_EVALUADOR, usuario__is_active=True)
        .order_by("usuario__first_name", "usuario__last_name", "usuario__email")
    )
    evaluadores_disponibles = [i.usuario for i in evaluadores_disponibles]

    return render(
        request,
        "coordinador/evaluadores/evaluadores.html",
        {
            "active": "evaluadores",
            "evento": evento,
            "proyectos": proyectos,
            "evaluadores_disponibles": evaluadores_disponibles,
            "form_proyecto": EvaluacionProyectoForm(),
        },
    )


@login_required
@require_POST
@transaction.atomic
def eval_proyecto_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyecto_id = (request.POST.get("proyecto_id") or "").strip()
    if not proyecto_id:
        messages.error(request, "Proyecto inválido.")
        return redirect("coordinador:evaluadores")

    instance = get_object_or_404(EvaluacionProyecto, pk=proyecto_id, evento=evento)
    form = EvaluacionProyectoForm(request.POST, instance=instance)

    if not form.is_valid():
        messages.error(request, "Verifica los datos del proyecto/ponencia.")
        return redirect("coordinador:evaluadores")

    try:
        obj = form.save(commit=False)
        obj.evento = evento
        obj.full_clean()
        obj.save()
        messages.success(request, "Proyecto/ponencia actualizado.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))

    return redirect("coordinador:evaluadores")


@login_required
@require_POST
@transaction.atomic
def eval_proyecto_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyecto = get_object_or_404(EvaluacionProyecto, pk=pk, evento=evento)
    proyecto.delete()
    messages.success(request, "Proyecto/ponencia eliminado.")
    return redirect("coordinador:evaluadores")


@login_required
@require_POST
@transaction.atomic
def eval_gestionar_guardar(request: HttpRequest) -> HttpResponse:
    """
    Guarda horario/lugar del proyecto + evaluadores asignados (reemplazo total).
    """
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyecto_id = (request.POST.get("proyecto_id") or "").strip()
    if not proyecto_id:
        messages.error(request, "Proyecto inválido.")
        return redirect("coordinador:evaluadores")

    proyecto = get_object_or_404(EvaluacionProyecto, pk=proyecto_id, evento=evento)

    inicio = request.POST.get("inicio")
    fin = request.POST.get("fin")
    lugar = (request.POST.get("lugar") or "").strip()

    evaluadores_ids = request.POST.getlist("evaluadores")
    evaluadores_ids = [e for e in evaluadores_ids if str(e).isdigit()]

    permitidos = set(
        Inscripcion.objects.filter(evento=evento, rol=Inscripcion.ROL_EVALUADOR, usuario__is_active=True)
        .values_list("usuario_id", flat=True)
    )
    seleccionados = [int(e) for e in evaluadores_ids if int(e) in permitidos]

    try:
        data_form = {"titulo": proyecto.titulo, "ponente": proyecto.ponente, "inicio": inicio, "fin": fin, "lugar": lugar}
        f = EvaluacionProyectoForm(data_form, instance=proyecto)
        if not f.is_valid():
            messages.error(request, "Horario/lugar inválidos. Verifica inicio/fin.")
            return redirect("coordinador:evaluadores")

        obj = f.save(commit=False)
        obj.evento = evento
        obj.full_clean()
        obj.save()

        EvaluacionAsignacion.objects.filter(proyecto=proyecto).delete()

        nuevas = []
        for uid in seleccionados:
            a = EvaluacionAsignacion(proyecto=proyecto, evaluador_id=uid)
            a.full_clean()
            nuevas.append(a)

        if nuevas:
            EvaluacionAsignacion.objects.bulk_create(nuevas)

        messages.success(request, "Asignación guardada correctamente.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))

    return redirect("coordinador:evaluadores")


# =========================================================
# RÚBRICAS
# =========================================================
@login_required
def rubricas(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubricas_qs = (
        Rubrica.objects.filter(evento=evento)
        .prefetch_related("criterios", "adjuntos", "proyecto")
        .order_by("-actualizado_en", "-id")
    )
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")

    return render(
        request,
        "coordinador/rubricas/rubricas.html",
        {
            "active": "rubricas",
            "evento": evento,
            "rubricas": rubricas_qs,
            "proyectos": proyectos,
            "form_rubrica": RubricaForm(proyectos_qs=proyectos),
        },
    )


@login_required
@require_POST
@transaction.atomic
def rubrica_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubrica_id = (request.POST.get("rubrica_id") or "").strip()
    instance = get_object_or_404(Rubrica, pk=rubrica_id, evento=evento) if rubrica_id else None

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = RubricaForm(request.POST, instance=instance, proyectos_qs=proyectos)

    if not form.is_valid():
        messages.error(request, "Verifica los datos de la rúbrica.")
        return redirect("coordinador:rubricas")

    titulos = request.POST.getlist("criterio_titulo")
    descripciones = request.POST.getlist("criterio_desc")
    puntajes = request.POST.getlist("criterio_puntos")

    criterios_limpios = []
    for i in range(len(titulos)):
        t = (titulos[i] or "").strip()
        d = (descripciones[i] or "").strip() if i < len(descripciones) else ""
        p = (puntajes[i] or "").strip() if i < len(puntajes) else ""
        if not t:
            continue
        if not p.isdigit() or int(p) <= 0:
            messages.error(request, f"El puntaje del criterio '{t}' debe ser un número mayor que 0.")
            return redirect("coordinador:rubricas")
        criterios_limpios.append((t, d, int(p)))

    if not criterios_limpios:
        messages.error(request, "Debes agregar al menos 1 criterio de evaluación.")
        return redirect("coordinador:rubricas")

    rubrica = form.save(commit=False)
    rubrica.evento = evento
    rubrica.full_clean()
    rubrica.save()

    RubricaCriterio.objects.filter(rubrica=rubrica).delete()
    nuevos = []
    for idx, (t, d, p) in enumerate(criterios_limpios, start=1):
        c = RubricaCriterio(rubrica=rubrica, titulo=t, descripcion=d, puntaje_max=p, orden=idx)
        c.full_clean()
        nuevos.append(c)
    RubricaCriterio.objects.bulk_create(nuevos)

    MAX_MB = 15
    MAX_BYTES = MAX_MB * 1024 * 1024

    for f in request.FILES.getlist("archivos"):
        size = int(getattr(f, "size", 0) or 0)
        if size > MAX_BYTES:
            messages.error(request, f"El archivo '{getattr(f, 'name', 'archivo')}' excede {MAX_MB}MB.")
            return redirect("coordinador:rubricas")

        adj = RubricaAdjunto(rubrica=rubrica, archivo=f, nombre_original=getattr(f, "name", "")[:255])
        adj.full_clean()
        adj.save()

    messages.success(request, "Rúbrica guardada correctamente.")
    return redirect("coordinador:rubricas")


@login_required
@require_POST
@transaction.atomic
def rubrica_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rubrica = get_object_or_404(Rubrica, pk=pk, evento=evento)
    rubrica.delete()
    messages.success(request, "Rúbrica eliminada correctamente.")
    return redirect("coordinador:rubricas")


# =========================================================
# ESPACIOS
# =========================================================
@login_required
def espacios(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    q = (request.GET.get("q") or "").strip()

    espacios_qs = Espacio.objects.filter(evento=evento).select_related("proyecto").order_by("-actualizado_en", "-id")
    if q:
        espacios_qs = espacios_qs.filter(
            Q(nombre__icontains=q) | Q(ubicacion__icontains=q) | Q(tags__icontains=q) | Q(proyecto__titulo__icontains=q)
        )

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    return render(
        request,
        "coordinador/espacios/espacios.html",
        {
            "active": "espacios",
            "evento": evento,
            "espacios": espacios_qs,
            "proyectos": proyectos,
            "form_espacio": EspacioForm(proyectos_qs=proyectos),
            "q": q,
        },
    )


@login_required
@require_POST
@transaction.atomic
def espacio_guardar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    espacio_id = (request.POST.get("espacio_id") or "").strip()
    instance = get_object_or_404(Espacio, pk=espacio_id, evento=evento) if espacio_id else None

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = EspacioForm(request.POST, instance=instance, proyectos_qs=proyectos)
    if not form.is_valid():
        messages.error(request, "Verifica los datos del espacio.")
        return redirect("coordinador:espacios")

    obj = form.save(commit=False)
    obj.evento = evento
    obj.full_clean()
    obj.save()
    messages.success(request, "Espacio guardado correctamente.")
    return redirect("coordinador:espacios")


@login_required
@require_POST
@transaction.atomic
def espacio_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    espacio = get_object_or_404(Espacio, pk=pk, evento=evento)
    espacio.delete()
    messages.success(request, "Espacio eliminado.")
    return redirect("coordinador:espacios")


# =========================================================
# REPORTES
# =========================================================
@login_required
def reportes(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    categoria = (request.GET.get("categoria") or Reporte.CATEG_TODOS).strip().upper()
    q = (request.GET.get("q") or "").strip()

    qs = Reporte.objects.filter(evento=evento).select_related("proyecto", "creado_por").order_by("-generado_en", "-id")

    categorias_validas = {c for c, _ in Reporte.CATEGORIAS}
    if categoria in categorias_validas and categoria != Reporte.CATEG_TODOS:
        qs = qs.filter(categoria=categoria)

    if q:
        qs = qs.filter(
            Q(nombre__icontains=q)
            | Q(nombre_original__icontains=q)
            | Q(proyecto__titulo__icontains=q)
            | Q(categoria__icontains=q)
            | Q(formato__icontains=q)
        )

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form_generar = ReporteGenerarForm(proyectos_qs=proyectos)

    Evento = _get_evento_model()
    eventos = []
    if Evento:
        field_names = {f.name for f in Evento._meta.fields}
        if "creado_por" in field_names:
            eventos = Evento.objects.filter(creado_por=request.user).order_by("-id")
        else:
            eventos_ids = Inscripcion.objects.filter(
                usuario=request.user,
                rol__in=[Inscripcion.ROL_COORDINADOR, Inscripcion.ROL_ADMINISTRADOR],
            ).values_list("evento_id", flat=True)
            eventos = Evento.objects.filter(id__in=eventos_ids).order_by("-id")

    return render(
        request,
        "coordinador/reportes/reportes.html",
        {
            "active": "reportes",
            "evento": evento,
            "eventos": eventos,
            "proyectos": proyectos,
            "reportes": qs,
            "form_generar": form_generar,
            "categoria": categoria,
            "q": q,
        },
    )


def _dataset_inscripciones(evento):
    headers = ["Nombres", "Apellidos", "Correo", "Rol", "Activo"]
    rows = []
    qs = Inscripcion.objects.select_related("usuario").filter(evento=evento).order_by("rol", "usuario__last_name")
    for ins in qs:
        u = ins.usuario
        rows.append([u.first_name or "", u.last_name or "", u.email or "", ins.rol or "", "SI" if u.is_active else "NO"])
    return headers, rows


def _dataset_evaluaciones(evento, proyecto=None):
    headers = ["Proyecto", "Ponente", "Inicio", "Fin", "Lugar", "Evaluadores Asignados"]
    rows = []
    proyectos = EvaluacionProyecto.objects.filter(evento=evento).prefetch_related("asignaciones__evaluador")
    if proyecto:
        proyectos = proyectos.filter(id=proyecto.id)

    for p in proyectos.order_by("inicio", "fin", "id"):
        evaluadores = [
            f"{a.evaluador.first_name} {a.evaluador.last_name}".strip()
            for a in p.asignaciones.all()
        ]
        evaluadores = [e for e in evaluadores if e] or [a.evaluador.email for a in p.asignaciones.all() if a.evaluador.email]
        rows.append([p.titulo, p.ponente or "", str(p.inicio), str(p.fin), p.lugar or "", ", ".join(evaluadores) if evaluadores else "SIN ASIGNACIÓN"])
    return headers, rows


def _dataset_general(evento):
    headers = ["Métrica", "Valor"]
    rows = [
        ["Inscripciones", str(Inscripcion.objects.filter(evento=evento).count())],
        ["Proyectos/Ponencias", str(EvaluacionProyecto.objects.filter(evento=evento).count())],
        ["Asignaciones evaluador", str(EvaluacionAsignacion.objects.filter(proyecto__evento=evento).count())],
        ["Rúbricas", str(Rubrica.objects.filter(evento=evento).count())],
        ["Espacios", str(Espacio.objects.filter(evento=evento).count())],
        ["Asistencia", "NO DISPONIBLE (pendiente módulo)"],
    ]
    return headers, rows


@login_required
@require_POST
@transaction.atomic
def reporte_generar(request: HttpRequest) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    proyectos = EvaluacionProyecto.objects.filter(evento=evento).order_by("inicio", "fin", "id")
    form = ReporteGenerarForm(request.POST, proyectos_qs=proyectos)
    if not form.is_valid():
        messages.error(request, "Verifica los datos para generar el reporte.")
        return redirect("coordinador:reportes")

    categoria = form.cleaned_data["categoria"]
    formato = form.cleaned_data["formato"]
    modo = form.cleaned_data["modo"]
    proyecto = form.cleaned_data.get("proyecto")
    nombre = (form.cleaned_data.get("nombre") or "").strip()

    ts = timezone.now().strftime("%Y%m%d_%H%M%S")
    if not nombre:
        base = f"Reporte_{categoria}"
        if proyecto:
            base += f"_{proyecto.id}"
        nombre = f"{base}_{ts}"

    if categoria == Reporte.CATEG_INSCRIPCIONES:
        headers, rows = _dataset_inscripciones(evento)
        sheet = "Inscripciones"
    elif categoria == Reporte.CATEG_EVALUACIONES:
        headers, rows = _dataset_evaluaciones(evento, proyecto=proyecto)
        sheet = "Evaluaciones"
    elif categoria == Reporte.CATEG_ASISTENCIA:
        headers, rows = ["Asistencia", "Nota"], [["Asistencia", "NO DISPONIBLE (pendiente módulo)"]]
        sheet = "Asistencia"
    else:
        headers, rows = _dataset_general(evento)
        sheet = "General"

    rep = Reporte(
        evento=evento,
        proyecto=proyecto,
        creado_por=request.user,
        nombre=nombre,
        categoria=categoria,
        formato=formato,
        modo=modo,
        estado=Reporte.ESTADO_LISTO,
        generado_en=timezone.now(),
    )
    rep.full_clean()
    rep.save()

    try:
        if formato == Reporte.FORMATO_XLSX:
            content = _xlsx_from_rows(sheet, headers, rows)
            rep.archivo.save(f"{nombre}.xlsx", ContentFile(content), save=True)
        else:
            title = f"{nombre} | Evento {getattr(evento, 'titulo', evento.id)}"
            lines = [
                title,
                f"Categoría: {categoria} | Modo: {modo} | Generado: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                " | ".join(headers),
                "-" * 90,
            ]
            for r in rows[:250]:
                lines.append(" | ".join([str(x) for x in r]))
            content = _build_minimal_pdf(lines)
            rep.archivo.save(f"{nombre}.pdf", ContentFile(content), save=True)

        messages.success(request, "Reporte generado correctamente.")
    except ImportError:
        messages.error(request, "Falta openpyxl para Excel. Instala: pip install openpyxl")
    except Exception as e:
        messages.error(request, f"No se pudo generar el reporte: {e}")

    return redirect("coordinador:reportes")


@login_required
def reporte_descargar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    if not rep.archivo:
        raise Http404("El reporte no tiene archivo asociado.")

    file_path = rep.archivo.path
    if not os.path.exists(file_path):
        raise Http404("Archivo no encontrado.")

    return FileResponse(open(file_path, "rb"), as_attachment=True, filename=os.path.basename(file_path))


@login_required
@require_POST
@transaction.atomic
def reporte_editar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    form = ReporteEditarForm(request.POST, instance=rep)
    if not form.is_valid():
        messages.error(request, "Verifica los datos del reporte.")
        return redirect("coordinador:reportes")

    obj = form.save(commit=False)
    obj.full_clean()
    obj.save()
    messages.success(request, "Reporte actualizado.")
    return redirect("coordinador:reportes")


@login_required
@require_POST
@transaction.atomic
def reporte_eliminar(request: HttpRequest, pk: int) -> HttpResponse:
    evento, redir = _require_evento_or_redirect(request)
    if redir:
        return redir

    rep = get_object_or_404(Reporte, pk=pk, evento=evento)
    try:
        if rep.archivo:
            rep.archivo.delete(save=False)
    except Exception:
        pass
    rep.delete()
    messages.success(request, "Reporte eliminado.")
    return redirect("coordinador:reportes")


# =========================================================
# CONFIGURACIÓN (✅ COMPLETA Y FUNCIONAL)
# =========================================================
@login_required
def configuracion(request: HttpRequest) -> HttpResponse:
    """
    Configuración de perfil/cuenta (segura):
    - Solo usuario autenticado y activo
    - Cambios con POST + CSRF
    - Cambio de password requiere password actual
    - Archivos con validación (en modelo PerfilUsuario)
    - Mantiene sesión si cambia password
    """
    if not request.user.is_active:
        messages.error(request, "Tu cuenta está inactiva. No puedes editar tu perfil.")
        return redirect("coordinador:dashboard")

    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        cuenta_form = ConfigCuentaForm(request.POST, user=request.user)
        perfil_form = ConfigPerfilForm(request.POST, request.FILES, instance=perfil)

        if cuenta_form.is_valid() and perfil_form.is_valid():
            with transaction.atomic():
                request.user.first_name = cuenta_form.cleaned_data["nombres"].strip()
                request.user.last_name = cuenta_form.cleaned_data["apellidos"].strip()
                request.user.email = cuenta_form.cleaned_data["correo"].strip().lower()
                if hasattr(request.user, "username"):
                    request.user.username = request.user.email

                nueva = (cuenta_form.cleaned_data.get("password_nueva") or "").strip()
                if nueva:
                    request.user.set_password(nueva)

                request.user.save()
                perfil_form.save()

                if nueva:
                    update_session_auth_hash(request, request.user)

            messages.success(request, "Configuración actualizada correctamente.")
            return redirect("coordinador:configuracion")

        messages.error(request, "Verifica los campos marcados.")
    else:
        cuenta_form = ConfigCuentaForm(
            user=request.user,
            initial={
                "nombres": request.user.first_name or "",
                "apellidos": request.user.last_name or "",
                "correo": request.user.email or "",
            },
        )
        perfil_form = ConfigPerfilForm(instance=perfil)

    evento = _get_evento_actual(request)
    return render(
        request,
        "coordinador/configuracion/configuracion.html",
        {
            "active": "configuracion",
            "evento": evento,
            "cuenta_form": cuenta_form,
            "perfil_form": perfil_form,
            "perfil": perfil,
        },
    )