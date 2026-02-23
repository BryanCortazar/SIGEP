from django.shortcuts import render

from functools import wraps
from typing import Callable

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseForbidden
from django.shortcuts import render


def coordinador_required(view_func: Callable):
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if getattr(request.user, "rol", None) != "COOR":
            return HttpResponseForbidden("Acceso denegado.")
        return view_func(request, *args, **kwargs)
    return _wrapped


@coordinador_required
def dashboard(request: HttpRequest):
    return render(request, "coordinador/dashboard/index.html", {"active": "dashboard"})


@coordinador_required
def eventos(request: HttpRequest):
    return render(request, "coordinador/eventos/eventos.html", {"active": "eventos"})


@coordinador_required
def cronograma(request: HttpRequest):
    return render(request, "coordinador/cronograma/cronograma.html", {"active": "cronograma"})


@coordinador_required
def inscripciones(request: HttpRequest):
    return render(request, "coordinador/inscripciones/inscripciones.html", {"active": "inscripciones"})


@coordinador_required
def asignacion_evaluadores(request: HttpRequest):
    return render(request, "coordinador/evaluadores/evaluadores.html", {"active": "evaluadores"})


@coordinador_required
def rubricas(request: HttpRequest):
    return render(request, "coordinador/rubricas/rubricas.html", {"active": "rubricas"})


@coordinador_required
def asignacion_espacios(request: HttpRequest):
    return render(request, "coordinador/espacios/espacios.html", {"active": "espacios"})


@coordinador_required
def reportes(request: HttpRequest):
    return render(request, "coordinador/reportes/reportes.html", {"active": "reportes"})


@coordinador_required
def configuracion(request: HttpRequest):
    return render(request, "coordinador/configuracion/configuracion.html", {"active": "configuracion"})
