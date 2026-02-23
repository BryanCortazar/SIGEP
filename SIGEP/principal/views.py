import re
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import render, redirect
from django.urls import reverse, NoReverseMatch
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

# Si tienes el modelo de recuperación, lo usa. Si no, igual muestra mensaje.
try:
    from .models import SolicitudRecuperacionCuenta
except Exception:
    SolicitudRecuperacionCuenta = None


User = get_user_model()

# ⚠️ Cambia este código y NO lo publiques en repositorios
ADMIN_CODE = "SIGEP-ADMIN-2026"


# -----------------------------
# Utilidades internas
# -----------------------------
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
    """
    Evita que truene si aún no existe una URL (por ejemplo dashboards aún no creados).
    """
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
        return _safe_redirect("participante:dashboard")

    # Si no hay rol definido (o el campo no existe)
    return redirect("principal:dashboard")


# -----------------------------
# ✅ Dashboard principal (ARREGLA TU ERROR)
# -----------------------------
def dashboard(request):
    """
    Esta vista existe para que principal/urls.py pueda apuntar a views.dashboard.
    Cambia la plantilla si tu landing es otra.
    """
    return render(request, "principal/index.html")


# -----------------------------
# Vistas principales
# -----------------------------
def login_view(request):
    """
    Login real:
    - acepta username o email en el input name="username"
    - valida contraseña
    - redirige por rol
    """
    if request.method == "GET":
        return render(request, "principal/login.html")

    username_or_email = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""

    if not username_or_email or not password:
        messages.error(request, "Ingresa tu usuario/correo y tu contraseña.")
        return render(request, "principal/login.html")

    # Login por email o username
    if "@" in username_or_email:
        user_obj = User.objects.filter(email__iexact=username_or_email).first()
        if not user_obj:
            messages.error(request, "No existe una cuenta asociada a ese correo.")
            return render(request, "principal/login.html")
        user = authenticate(request, username=user_obj.username, password=password)
    else:
        user = authenticate(request, username=username_or_email, password=password)

    if user is None:
        messages.error(request, "Credenciales incorrectas. Verifica tus datos.")
        return render(request, "principal/login.html")

    if not user.is_active:
        messages.error(request, "Tu cuenta está desactivada. Contacta al administrador.")
        return render(request, "principal/login.html")

    login(request, user)
    messages.success(request, "Inicio de sesión exitoso.")
    return _redirect_por_rol(user)


def registrar_view(request):
    """
    Registro real:
    - valida campos requeridos
    - valida contraseñas
    - evita email duplicado
    - genera username desde email
    - asigna rol (si existe campo rol)
    - ADMIN requiere código maestro (admin_code)
    """
    if request.method == "GET":
        return render(request, "principal/registrar.html")

    fullname = (request.POST.get("fullname") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()

    rol = (request.POST.get("rol") or "PART").strip().upper()
    admin_code = (request.POST.get("admin_code") or "").strip()

    password = request.POST.get("password") or ""
    confirm = request.POST.get("confirm_password") or ""

    # Validaciones
    if not fullname or not email or not password or not confirm or not rol:
        messages.error(request, "Completa todos los campos requeridos.")
        return render(request, "principal/registrar.html")

    if "@" not in email:
        messages.error(request, "Ingresa un correo electrónico válido.")
        return render(request, "principal/registrar.html")

    if password != confirm:
        messages.error(request, "Las contraseñas no coinciden.")
        return render(request, "principal/registrar.html")

    # ADMIN: exigir código maestro
    if rol == "ADMIN" and admin_code != ADMIN_CODE:
        messages.error(request, "Código de administrador inválido. No tienes autorización.")
        return render(request, "principal/registrar.html")

    # Evitar duplicado por correo
    if User.objects.filter(email__iexact=email).exists():
        messages.error(request, "Ese correo ya está registrado. Intenta iniciar sesión.")
        return render(request, "principal/registrar.html")

    # Crear username único desde correo
    username = _generate_unique_username(email)

    # Crear usuario
    user = User.objects.create_user(
        username=username,
        email=email,
        password=password
    )

    # Guardar nombre completo en first_name (sin inventar campos)
    user.first_name = fullname

    # Guardar rol si existe el campo
    if hasattr(user, "rol"):
        user.rol = rol

    user.save()

    messages.success(request, "Usuario registrado correctamente. Ahora puedes iniciar sesión.")
    return redirect("principal:login")


def recuperar_cuenta_view(request):
    """
    Recuperación:
    - pide email
    - mensaje neutro por seguridad (no revela si existe)
    - si existe y tienes el modelo, crea solicitud con expiración
    """
    if request.method == "GET":
        return render(request, "principal/recuperar_cuenta.html")

    email = (request.POST.get("email") or "").strip().lower()
    if not email:
        messages.error(request, "Ingresa tu correo electrónico.")
        return render(request, "principal/recuperar_cuenta.html")

    ok_msg = "Si el correo está registrado, recibirás instrucciones para recuperar tu cuenta."

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        messages.success(request, ok_msg)
        return render(request, "principal/recuperar_cuenta.html")

    # Si tienes el modelo de recuperación, genera token (30 min)
    if SolicitudRecuperacionCuenta is not None:
        expira_en = timezone.now() + timedelta(minutes=30)
        try:
            SolicitudRecuperacionCuenta.objects.create(
                usuario=user,
                expira_en=expira_en
            )
        except TypeError:
            # Si tu modelo no tiene expira_en, no tronamos el flujo
            SolicitudRecuperacionCuenta.objects.create(usuario=user)

    # En producción enviarías el correo aquí
    messages.success(request, ok_msg)
    return render(request, "principal/recuperar_cuenta.html")


@login_required
def salir(request):
    logout(request)
    messages.success(request, "Sesión cerrada correctamente.")
    return redirect("principal:login")


# -----------------------------
# ✅ Set Password (para enlace desde Admin)
# -----------------------------
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
