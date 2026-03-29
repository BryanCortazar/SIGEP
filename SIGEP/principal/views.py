import re
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

try:
    from .models import SolicitudRecuperacionCuenta
except Exception:
    SolicitudRecuperacionCuenta = None


User = get_user_model()

ADMIN_CODE = "SIGEP-ADMIN-2026"
COOR_CODE = "SIGEP-COOR-2026"


def _role_options():
    if hasattr(User, "Rol"):
        return list(User.Rol.choices)
    return [
        ("PART", "Participante"),
        ("PON", "Ponente"),
        ("EVAL", "Evaluador/Jurado"),
        ("COOR", "Coordinador"),
        ("ADMIN", "Administrador"),
    ]


def _clean_username(base: str) -> str:
    base = (base or "").strip().lower()
    base = re.sub(r"[^a-z0-9._-]", "", base)
    return base[:150] if base else "usuario"


def _generate_unique_username(email: str) -> str:
    local = (email or "").split("@")[0]
    base = _clean_username(local)
    username = base
    i = 1
    while User.objects.filter(username__iexact=username).exists():
        i += 1
        suffix = str(i)
        username = base[: (150 - len(suffix))] + suffix
    return username


def _safe_redirect(url_name: str, fallback: str = "principal:dashboard"):
    try:
        reverse(url_name)
        return redirect(url_name)
    except NoReverseMatch:
        return redirect(fallback)


def _redirect_por_rol(user):
    rol = getattr(user, "rol", None)

    if rol == "ADMIN":
        return _safe_redirect("administrador:dashboard")
    if rol == "COOR":
        return _safe_redirect("coordinador:dashboard")
    if rol == "EVAL":
        return _safe_redirect("evaluador:dashboard")
    if rol == "PON":
        return _safe_redirect("ponente:dashboard")
    if rol == "PART":
        return _safe_redirect("participante:panel_participante")

    return redirect("principal:dashboard")


def _render_login(request, form_data=None):
    return render(request, "principal/login.html", {
        "form_data": form_data or {},
    })


def _render_registrar(request, form_data=None):
    return render(request, "principal/registrar.html", {
        "form_data": form_data or {},
        "role_options": _role_options(),
    })


def _render_recuperar(request, form_data=None):
    return render(request, "principal/recuperar_cuenta.html", {
        "form_data": form_data or {},
    })


def dashboard(request):
    return render(request, "principal/index.html")


def login_view(request):
    if request.method == "GET":
        return _render_login(request)

    username_or_email = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""

    form_data = {"username": username_or_email}

    if not username_or_email or not password:
        messages.error(request, "Ingresa tu usuario o correo y tu contraseña.")
        return _render_login(request, form_data)

    if "@" in username_or_email:
        user_obj = User.objects.filter(email__iexact=username_or_email).first()
        if not user_obj:
            messages.error(request, "No existe una cuenta asociada a ese correo.")
            return _render_login(request, form_data)
        user = authenticate(request, username=user_obj.username, password=password)
    else:
        user = authenticate(request, username=username_or_email, password=password)

    if user is None:
        messages.error(request, "Credenciales incorrectas. Verifica tus datos.")
        return _render_login(request, form_data)

    if not user.is_active:
        messages.error(request, "Tu cuenta está desactivada. Contacta al administrador.")
        return _render_login(request, form_data)

    login(request, user)
    return _redirect_por_rol(user)


def registrar_view(request):
    if request.method == "GET":
        return _render_registrar(request)

    nombres = (request.POST.get("nombres") or "").strip()
    apellido_paterno = (request.POST.get("apellido_paterno") or "").strip()
    apellido_materno = (request.POST.get("apellido_materno") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    rol = (request.POST.get("rol") or "PART").strip().upper()
    admin_code = (request.POST.get("admin_code") or "").strip()
    password = request.POST.get("password") or ""
    confirm = request.POST.get("confirm_password") or ""

    form_data = {
        "nombres": nombres,
        "apellido_paterno": apellido_paterno,
        "apellido_materno": apellido_materno,
        "email": email,
        "rol": rol,
        "admin_code": admin_code,
    }

    if not nombres or not apellido_paterno or not email or not password or not confirm or not rol:
        messages.error(request, "Completa todos los campos obligatorios.")
        return _render_registrar(request, form_data)

    if "@" not in email:
        messages.error(request, "Ingresa un correo electrónico válido.")
        return _render_registrar(request, form_data)

    if password != confirm:
        messages.error(request, "Las contraseñas no coinciden.")
        return _render_registrar(request, form_data)

    valid_roles = {value for value, _ in _role_options()}
    if rol not in valid_roles:
        messages.error(request, "Selecciona un rol válido.")
        return _render_registrar(request, form_data)

    if rol == "ADMIN" and admin_code != ADMIN_CODE:
        messages.error(request, "Código de administrador inválido. No tienes autorización.")
        return _render_registrar(request, form_data)

    if rol == "COOR" and admin_code != COOR_CODE:
        messages.error(request, "Código de coordinador inválido. No tienes autorización.")
        return _render_registrar(request, form_data)

    if User.objects.filter(email__iexact=email).exists():
        messages.error(request, "Ese correo ya está registrado. Intenta iniciar sesión.")
        return _render_registrar(request, form_data)

    username = _generate_unique_username(email)

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )

    # Compatibilidad con modelo antiguo y nuevo
    if hasattr(user, "nombres"):
        user.nombres = nombres
    if hasattr(user, "apellido_paterno"):
        user.apellido_paterno = apellido_paterno
    if hasattr(user, "apellido_materno"):
        user.apellido_materno = apellido_materno

    user.first_name = nombres
    user.last_name = " ".join(part for part in [apellido_paterno, apellido_materno] if part).strip()

    if hasattr(user, "rol"):
        user.rol = rol

    user.save()

    messages.success(request, "Usuario registrado correctamente. Ahora puedes iniciar sesión.")
    return redirect("principal:login")


def recuperar_cuenta_view(request):
    if request.method == "GET":
        return _render_recuperar(request)

    email = (request.POST.get("email") or "").strip().lower()
    form_data = {"email": email}

    if not email:
        messages.error(request, "Ingresa tu correo electrónico.")
        return _render_recuperar(request, form_data)

    ok_msg = "Si el correo está registrado, recibirás instrucciones para recuperar tu cuenta."

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        messages.success(request, ok_msg)
        return _render_recuperar(request, form_data)

    if SolicitudRecuperacionCuenta is not None:
        expira_en = timezone.now() + timedelta(minutes=30)
        defaults = {
            "expira_en": expira_en,
            "ip_origen": request.META.get("REMOTE_ADDR"),
            "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:255],
        }
        try:
            SolicitudRecuperacionCuenta.objects.create(
                usuario=user,
                **defaults,
            )
        except TypeError:
            SolicitudRecuperacionCuenta.objects.create(usuario=user, expira_en=expira_en)

    messages.success(request, ok_msg)
    return _render_recuperar(request, form_data)


@login_required
def salir(request):
    logout(request)
    messages.success(request, "Sesión cerrada correctamente.")
    return redirect("principal:login")


def set_password(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        messages.error(request, "El enlace es inválido o ya expiró.")
        return redirect("principal:login")

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Contraseña establecida correctamente. Ya puedes iniciar sesión.")
            return redirect("principal:login")
    else:
        form = SetPasswordForm(user)

    return render(request, "principal/set_password.html", {"form": form})
