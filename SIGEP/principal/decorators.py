from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required

from .middleware import redirect_to_user_panel


def rol_requerido(*roles_permitidos):
    """
    Decorador opcional para vistas específicas.
    El middleware principal ya protege los módulos por prefijo, pero este decorador
    permite reforzar vistas críticas de forma explícita.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            rol_usuario = getattr(request.user, "rol", None)

            if rol_usuario not in roles_permitidos:
                messages.error(
                    request,
                    "No tienes permisos para realizar esta acción. "
                    "Has sido redirigido a tu panel correspondiente."
                )
                return redirect_to_user_panel(request)

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator