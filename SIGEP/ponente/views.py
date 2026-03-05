from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404

from administrador.models import Evento
from .forms import PonenciaForm, SeleccionEventoForm
from .models import Ponencia

# Reutilizamos tu Inscripcion global (ya existente en tu sistema)
from evaluador.models import Inscripcion


@login_required
def panel(request):
    return render(request, "dashboard/panel.html", {
        "active": "panel",
        "kpi_ponencias": 0,
        "kpi_aceptadas": 0,
        "kpi_revision": 0,
        "kpi_constancia": "Pendiente",
    })


@login_required
def inscripcion(request):
    eventos = Evento.objects.filter(estado="PUBLICADO").order_by("-id")

    evento_id = request.GET.get("evento")
    evento_sel = None
    if evento_id:
        try:
            evento_sel = eventos.get(pk=int(evento_id))
        except Exception:
            evento_sel = None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "select_event":
            form_sel = SeleccionEventoForm(request.POST)
            if form_sel.is_valid():
                eid = form_sel.cleaned_data["evento_id"]
                return redirect(f"{request.path}?evento={eid}")
            messages.error(request, "Selecciona un evento válido.")
            return redirect(request.path)

        if action == "registrar":
            if not evento_sel:
                messages.error(request, "Selecciona un evento antes de registrar la ponencia.")
                return redirect(request.path)

            form = PonenciaForm(request.POST, request.FILES)
            if form.is_valid():
                Inscripcion.objects.get_or_create(
                    evento=evento_sel,
                    usuario=request.user,
                    defaults={"rol": Inscripcion.ROL_PONENTE},
                )

                ponencia = form.save(commit=False)
                ponencia.evento = evento_sel
                ponencia.ponente = request.user
                ponencia.estado = Ponencia.ESTADO_REGISTRADA
                ponencia.save()

                messages.success(request, "Ponencia registrada correctamente.")
                return redirect(f"{request.path}?evento={evento_sel.id}")
            messages.error(request, "Revisa los campos de la ponencia e inténtalo de nuevo.")

        if action == "editar":
            pid = request.POST.get("ponencia_id")
            if not (evento_sel and pid):
                messages.error(request, "Acción inválida.")
                return redirect(request.path)

            ponencia = get_object_or_404(Ponencia, pk=pid, ponente=request.user, evento=evento_sel)
            if not ponencia.puede_editar():
                messages.error(request, "Esta ponencia ya no permite edición.")
                return redirect(f"{request.path}?evento={evento_sel.id}")

            form = PonenciaForm(request.POST, request.FILES, instance=ponencia)
            if form.is_valid():
                form.save()
                messages.success(request, "Ponencia actualizada correctamente.")
            else:
                messages.error(request, "Revisa los campos e inténtalo de nuevo.")
            return redirect(f"{request.path}?evento={evento_sel.id}")

        if action == "eliminar":
            pid = request.POST.get("ponencia_id")
            if not (evento_sel and pid):
                messages.error(request, "Acción inválida.")
                return redirect(request.path)

            ponencia = get_object_or_404(Ponencia, pk=pid, ponente=request.user, evento=evento_sel)
            if not ponencia.puede_editar():
                messages.error(request, "Esta ponencia ya no permite eliminación.")
                return redirect(f"{request.path}?evento={evento_sel.id}")

            ponencia.delete()
            messages.success(request, "Ponencia eliminada correctamente.")
            return redirect(f"{request.path}?evento={evento_sel.id}")

    ponencias = Ponencia.objects.none()
    if evento_sel:
        ponencias = Ponencia.objects.filter(evento=evento_sel, ponente=request.user).order_by("-actualizado_en")

    form_registrar = PonenciaForm()
    form_editar = PonenciaForm()

    # ✅ TU TEMPLATE REAL:
    return render(request, "inscripcion/inscripcion.html", {
        "active": "inscripcion",
        "eventos": eventos,
        "evento_sel": evento_sel,
        "ponencias": ponencias,
        "form_registrar": form_registrar,
        "form_editar": form_editar,
    })


@login_required
def gestionar_participacion(request):
    return render(request, "participacion/gestionar.html", {
        "active": "participacion",
    })


@login_required
def mi_horario(request):
    return render(request, "horario/horario.html", {
        "active": "horario",
        "asignaciones": [],
    })


@login_required
def mis_resultados(request):
    return render(request, "resultados/resultados.html", {
        "active": "resultados",
        "resultados": [],
    })


@login_required
def historial(request):
    return render(request, "historial/historial.html", {
        "active": "historial",
        "items": [],
    })


@login_required
def generar_constancia(request):
    return render(request, "constancia/constancia.html", {
        "active": "constancia",
    })


@login_required
def configuracion(request):
    return render(request, "configuracion/configuracion.html", {
        "active": "configuracion",
    })