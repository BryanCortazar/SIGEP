from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse


# Rutas públicas o semipúblicas que no deben bloquearse por prefijo de módulo.
# Ejemplo: validación pública del QR del pase del participante.
EXEMPT_PATH_PREFIXES = (
    "/participante/pase/validar/",
)


# Prefijos principales de cada módulo y roles autorizados para entrar.
# Mantiene una separación estricta por módulo para la prueba CP-AUT-013.
ROLE_PROTECTED_PREFIXES = (
    ("/administrador/", {"ADMIN"}),
    ("/coordinador/", {"COOR"}),
    ("/evaluador/", {"EVAL"}),
    ("/ponente/", {"PON"}),
    ("/participante/", {"PART"}),
)


# Candidatos de redirección por rol. Se incluyen variantes porque algunos módulos
# usan "dashboard" y otros usan "panel" como nombre de URL principal.
ROLE_PANEL_CANDIDATES = {
    "ADMIN": ("administrador:dashboard",),
    "COOR": ("coordinador:dashboard",),
    "EVAL": ("evaluador:dashboard", "evaluador:panel"),
    "PON": ("ponente:dashboard", "ponente:panel"),
    "PART": ("participante:panel_participante", "participante:panel"),
}


def _normalize_path(path: str) -> str:
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def _path_starts_with(path: str, prefix: str) -> bool:
    path = _normalize_path(path)
    prefix = _normalize_path(prefix)
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _is_exempt_path(path: str) -> bool:
    return any(_path_starts_with(path, prefix) for prefix in EXEMPT_PATH_PREFIXES)


def resolve_panel_url_name(user) -> str:
    """
    Devuelve el nombre de URL del panel correspondiente al rol del usuario.
    Si una ruta no existe, prueba con el siguiente candidato.
    """
    rol = getattr(user, "rol", None)
    candidates = ROLE_PANEL_CANDIDATES.get(rol, ("principal:dashboard",))

    for url_name in candidates:
        try:
            reverse(url_name)
            return url_name
        except NoReverseMatch:
            continue

    return "principal:dashboard"


def redirect_to_user_panel(request):
    return redirect(resolve_panel_url_name(request.user))


class RoleAccessMiddleware:
    """
    Control centralizado de acceso por módulo.

    Si un usuario autenticado intenta entrar a un módulo que no corresponde a su rol,
    se le muestra un mensaje profesional y se le redirige a su panel correspondiente.

    También refuerza rutas protegidas sin sesión activa redirigiendo al login definido
    en settings.LOGIN_URL.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = _normalize_path(getattr(request, "path_info", request.path))

        if _is_exempt_path(path):
            return self.get_response(request)

        for prefix, roles_permitidos in ROLE_PROTECTED_PREFIXES:
            if not _path_starts_with(path, prefix):
                continue

            if not request.user.is_authenticated:
                login_url = getattr(settings, "LOGIN_URL", "/login/")
                return redirect(f"{login_url}?next={quote(request.get_full_path())}")

            rol_usuario = getattr(request.user, "rol", None)
            if rol_usuario not in roles_permitidos:
                messages.error(
                    request,
                    "No tienes permisos para acceder a este módulo. "
                    "Has sido redirigido a tu panel correspondiente."
                )
                return redirect_to_user_panel(request)

            break

        return self.get_response(request)