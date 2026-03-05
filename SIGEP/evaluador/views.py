from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    PerfilUsuario,
    EvaluacionAsignacion,
    EvaluacionEntrega,
)


# =========================
# PANEL
# =========================
@login_required
def panel(request):
    """
    Dashboard del evaluador.
    Corrige el uso de estados: NO existe EvaluacionEntrega.Estado.
    """
    # Asignaciones del evaluador
    asignaciones = (
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio")
    )

    # Entregas del evaluador (enviadas)
    entregas_enviadas = (
        EvaluacionEntrega.objects
        .select_related("asignacion", "asignacion__proyecto")
        .filter(asignacion__evaluador=request.user, estado=EvaluacionEntrega.ESTADO_ENVIADA)
        .order_by("-fecha_envio")
    )

    # KPIs simples
    total_asignadas = asignaciones.count()
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


# =========================
# PROYECTOS ASIGNADOS
# =========================
@login_required
def proyectos_asignados(request):
    asignaciones = (
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("-creado_en")
    )

    # Si tu template usa "proyectos", lo armamos desde asignaciones
    proyectos = [a.proyecto for a in asignaciones]

    return render(request, "asignados/proyectos_asignados.html", {
        "active": "proyectos",
        "proyectos": proyectos,
    })


# =========================
# FORMULARIO (stub seguro)
# =========================
@login_required
def formulario(request, proyecto_id: int):
    """
    Este endpoint normalmente consume rúbrica asignada por coordinador.
    Por ahora: placeholder seguro para que no truene.
    """
    asignacion = get_object_or_404(EvaluacionAsignacion, evaluador=request.user, proyecto_id=proyecto_id)

    if request.method == "POST":
        accion = request.POST.get("accion", "guardar")

        entrega, _ = EvaluacionEntrega.objects.get_or_create(asignacion=asignacion)
        entrega.observaciones_generales = request.POST.get("observaciones_generales", "").strip()

        # Aquí luego calculas calificación real (por criterios).
        # De momento guardamos 0.0 si no hay cálculo.
        try:
            entrega.calificacion = float(request.POST.get("calificacion", entrega.calificacion or 0.0))
        except Exception:
            pass

        if accion == "enviar":
            entrega.estado = EvaluacionEntrega.ESTADO_ENVIADA
            entrega.fecha_envio = timezone.now()
            messages.success(request, "Evaluación enviada correctamente.")
        else:
            entrega.estado = EvaluacionEntrega.ESTADO_BORRADOR
            messages.success(request, "Borrador guardado correctamente.")

        entrega.save()
        return redirect("evaluador:historial")

    return render(request, "evaluacion/formulario.html", {
        "active": "formulario",
        "proyecto": asignacion.proyecto,
        "asignacion": asignacion,
        "criterios": [],  # luego se llena con la rúbrica asignada
        "rubrica": None,
    })


# =========================
# HORARIO ASIGNADO
# =========================
@login_required
def mi_horario(request):
    # Si tu template usa "asignaciones" (como ya vimos), lo mandamos así.
    asignaciones = (
        EvaluacionAsignacion.objects
        .select_related("proyecto")
        .filter(evaluador=request.user)
        .order_by("proyecto__inicio")
    )

    # Mapeo simple para que el template muestre tipo/fecha/hora/espacio si lo tienes luego.
    # Por ahora mandamos asignaciones (tu template ya soporta vacío).
    return render(request, "horario/horario.html", {
        "active": "horario",
        "asignaciones": asignaciones,
    })


# =========================
# HISTORIAL
# =========================
@login_required
def historial(request):
    entregas = (
        EvaluacionEntrega.objects
        .select_related("asignacion", "asignacion__proyecto")
        .filter(asignacion__evaluador=request.user)
        .order_by("-fecha_envio", "-actualizado_en")
    )

    # KPIs básicos
    kpi_total = entregas.count()
    # promedio simple sobre calificacion (Decimal)
    vals = [float(e.calificacion or 0) for e in entregas]
    kpi_promedio = round(sum(vals) / len(vals), 1) if vals else None
    kpi_pendientes = EvaluacionEntrega.objects.filter(
        asignacion__evaluador=request.user,
        estado=EvaluacionEntrega.ESTADO_BORRADOR
    ).count()

    return render(request, "historial/historial.html", {
        "active": "historial",
        "entregas": entregas[:50],
        "kpi_total": kpi_total,
        "kpi_promedio": kpi_promedio,
        "kpi_pendientes": kpi_pendientes,
    })


# =========================
# CONFIGURACIÓN (ajustada a perfil_evaluador)
# =========================
@login_required
def configuracion(request):
    # OJO: con el models.py blindado, ahora es request.user.perfil_evaluador
    perfil, _ = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST":
        # Datos base
        request.user.first_name = request.POST.get("nombres", "").strip()
        request.user.last_name = request.POST.get("apellidos", "").strip()
        request.user.email = request.POST.get("correo", "").strip()
        request.user.save()

        perfil.institucion = request.POST.get("institucion", "").strip()
        perfil.puesto = request.POST.get("puesto", "").strip()
        perfil.telefono = request.POST.get("telefono", "").strip()
        perfil.bio = request.POST.get("bio", "").strip()

        # Archivos
        if "avatar" in request.FILES:
            perfil.avatar = request.FILES["avatar"]
        if "cv" in request.FILES:
            perfil.cv = request.FILES["cv"]

        perfil.save()

        # Password opcional
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

    # Render: tu template espera perfil, cuenta_form, perfil_form.
    # Para no romper, mandamos objetos “compatibles” vía dicts sencillos.
    class _V:
        def __init__(self, v): self.value = v

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