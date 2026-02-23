from __future__ import annotations

import csv
from functools import wraps
from typing import Callable

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import AuditoriaLog, ConfiguracionSistema, Evento

User = get_user_model()

# =========================
# crear usuario desde dashboard (modal)
# =========================

def crear_usuario(request):

    if request.method != "POST":
        return redirect("administrador:dashboard")

    nombre = request.POST.get("nombre")
    email = request.POST.get("email")
    password = request.POST.get("password")
    rol = request.POST.get("rol", "PART")

    if not nombre or not email or not password:
        messages.error(request, "Completa todos los campos.")
        return redirect("administrador:dashboard")

    if User.objects.filter(email=email).exists():
        messages.error(request, "Ese correo ya existe.")
        return redirect("administrador:dashboard")

    user = User.objects.create_user(
        username=email,
        email=email,
        password=password
    )

    user.first_name = nombre

    if hasattr(user, "rol"):
        user.rol = rol

    user.save()

    messages.success(request, "Usuario creado correctamente.")
    return redirect("administrador:dashboard")
# =========================
# Exportar auditoria 
# =========================

def auditoria_export_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="auditoria.csv"'

    writer = csv.writer(response)
    writer.writerow(["Fecha", "Usuario", "Acción"])

    logs = AuditoriaLog.objects.all().order_by("-fecha")

    for l in logs:
        writer.writerow([
            l.fecha,
            getattr(l.usuario, "username", "Sistema"),
            l.accion
        ])

    return response

# =========================
# Exportar auditoria 
# =========================

def reporte_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="reporte_sigep.csv"'

    writer = csv.writer(response)

    writer.writerow(["Usuarios"])
    writer.writerow(["ID", "Username", "Email", "Rol", "Activo"])

    for u in User.objects.all():
        writer.writerow([u.id, u.username, u.email, u.rol, u.is_active])

    writer.writerow([])
    writer.writerow(["Eventos"])
    writer.writerow(["ID", "Título", "Fecha", "Estado"])

    for e in Evento.objects.all():
        writer.writerow([e.id, e.titulo, e.fecha, e.estado])

    return response

# =========================
# Decorador Admin
# =========================
def admin_required(view_func: Callable):
    @login_required
    @wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs):
        if getattr(request.user, "rol", None) != "ADMIN":
            return HttpResponseForbidden("Acceso denegado.")
        return view_func(request, *args, **kwargs)
    return _wrapped


# =========================
# Utilidades
# =========================
def _get_roles_disponibles():
    # Ajusta aquí si tus roles reales son otros
    return ["ADMIN", "COOR", "EVAL", "PON", "PART"]


def _is_admin_user(u: User) -> bool:
    return getattr(u, "rol", None) == "ADMIN"


def _set_full_name(u: User, full_name: str) -> None:
    # Compatible: usamos first_name como "nombre completo"
    if hasattr(u, "first_name"):
        u.first_name = full_name


def _client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _audit(request: HttpRequest, accion: str) -> None:
    """Auditoría real en tu tabla AuditoriaLog (no revienta si falla)."""
    try:
        AuditoriaLog.objects.create(
            usuario=request.user if request.user.is_authenticated else None,
            accion=accion,
            ip_origen=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2000],
        )
    except Exception:
        pass


# =========================
# Dashboard
# =========================
@admin_required
def dashboard(request: HttpRequest):
    total_usuarios = User.objects.count()
    eventos_activos = Evento.objects.filter(estado="PUBLICADO").count()
    roles_configurados = len(_get_roles_disponibles())
    solicitudes_pendientes = 0

    actividad = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:10]

    context = {
        "active": "dashboard",
        "total_usuarios": total_usuarios,
        "eventos_activos": eventos_activos,
        "roles_configurados": roles_configurados,
        "solicitudes_pendientes": solicitudes_pendientes,
        "actividad": actividad,
        "roles": _get_roles_disponibles(),  # por si tu modal del dashboard usa roles
    }
    return render(request, "administrador/dashboard/index.html", context)


# ==========================================================
# ✅ Crear usuario desde Dashboard (MODAL)  <--- ESTA FALTABA
# ==========================================================
@admin_required
def crear_usuario(request: HttpRequest):
    """
    Crea usuario desde el modal del Dashboard y envía enlace para setear contraseña.
    Requiere que exista la ruta principal:set_password (si no, igual crea el usuario).
    """
    if request.method != "POST":
        return redirect("administrador:dashboard")

    nombre = (request.POST.get("nombre") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    rol = (request.POST.get("rol") or "").strip().upper()

    roles_validos = _get_roles_disponibles()
    if not nombre or not email or rol not in roles_validos:
        messages.error(request, "Datos inválidos. Verifica nombre, correo y rol.")
        return redirect("administrador:dashboard")

    if User.objects.filter(email=email).exists():
        messages.error(request, "Ya existe un usuario con ese correo.")
        return redirect("administrador:dashboard")

    # username base: parte antes del @. Si ya existe, hacemos variante
    base_username = email.split("@")[0]
    username = base_username
    i = 1
    while User.objects.filter(username=username).exists():
        i += 1
        username = f"{base_username}{i}"

    # Crear
    u = User.objects.create(username=username, email=email, is_active=True)

    if hasattr(u, "rol"):
        u.rol = rol
    _set_full_name(u, nombre)

    # No usable password: obliga a set password
    u.set_unusable_password()
    u.save()

    _audit(request, f"USUARIOS | CREAR (DASHBOARD) | {u.username} ({u.email}) rol={rol}")

    # Intentar mandar mail con set_password
    try:
        from django.contrib.auth.tokens import default_token_generator

        uidb64 = urlsafe_base64_encode(force_bytes(u.pk))
        token = default_token_generator.make_token(u)
        setpass_path = reverse("principal:set_password", kwargs={"uidb64": uidb64, "token": token})
        setpass_url = request.build_absolute_uri(setpass_path)

        subject = "SIGEP | Establece tu contraseña"
        body = (
            f"Hola {nombre},\n\n"
            f"Se ha creado tu cuenta en SIGEP con el rol: {rol}.\n"
            f"Para establecer tu contraseña, entra al siguiente enlace:\n\n"
            f"{setpass_url}\n\n"
            f"— SIGEP"
        )

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        send_mail(subject, body, from_email, [email], fail_silently=False)
        messages.success(request, f"Usuario creado. Se envió un correo a {email} para establecer contraseña.")
    except Exception:
        # Si aún no tienes implementada la vista principal:set_password o no hay correo configurado
        messages.warning(
            request,
            "Usuario creado, pero no fue posible enviar el correo para establecer contraseña. "
            "Verifica la configuración de correo y/o la ruta principal:set_password."
        )

    return redirect("administrador:dashboard")


# =========================
# Usuarios
# =========================
@admin_required
def usuarios(request: HttpRequest):
    roles = _get_roles_disponibles()

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        user_id = request.POST.get("user_id")

        if not user_id:
            messages.error(request, "Falta user_id.")
            return redirect("administrador:usuarios")

        u = get_object_or_404(User, pk=user_id)

        if accion == "toggle":
            if u.pk == request.user.pk:
                messages.warning(request, "No puedes desactivar tu propia cuenta desde aquí.")
                return redirect("administrador:usuarios")

            if u.is_active and _is_admin_user(u):
                admins_activos = User.objects.filter(is_active=True, rol="ADMIN").count()
                if admins_activos <= 1:
                    messages.error(request, "No puedes desactivar al último administrador activo.")
                    return redirect("administrador:usuarios")

            u.is_active = not u.is_active
            u.save(update_fields=["is_active"])
            _audit(request, f"USUARIOS | TOGGLE | {u.username} activo={u.is_active}")
            messages.success(request, f"Estado actualizado para {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "rol":
            nuevo_rol = (request.POST.get("rol") or "").strip().upper()
            if nuevo_rol not in roles:
                messages.error(request, "Rol inválido.")
                return redirect("administrador:usuarios")

            if u.pk == request.user.pk and nuevo_rol != "ADMIN":
                messages.warning(request, "Por seguridad, no puedes quitarte el rol ADMIN a ti mismo.")
                return redirect("administrador:usuarios")

            u.rol = nuevo_rol
            u.save(update_fields=["rol"])
            _audit(request, f"USUARIOS | CAMBIAR_ROL | {u.username} rol={nuevo_rol}")
            messages.success(request, f"Rol actualizado a {nuevo_rol} para {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "editar":
            nombre = (request.POST.get("nombre") or "").strip()
            email = (request.POST.get("email") or "").strip().lower()
            username = (request.POST.get("username") or "").strip()
            nuevo_rol = (request.POST.get("rol") or "").strip().upper()

            if not nombre or not email or not username:
                messages.error(request, "Nombre, correo y username son obligatorios.")
                return redirect("administrador:usuarios")

            if nuevo_rol and nuevo_rol not in roles:
                messages.error(request, "Rol inválido.")
                return redirect("administrador:usuarios")

            if User.objects.filter(email=email).exclude(pk=u.pk).exists():
                messages.error(request, "Ese correo ya está en uso.")
                return redirect("administrador:usuarios")

            if User.objects.filter(username=username).exclude(pk=u.pk).exists():
                messages.error(request, "Ese username ya está en uso.")
                return redirect("administrador:usuarios")

            if u.pk == request.user.pk and nuevo_rol and nuevo_rol != "ADMIN":
                messages.warning(request, "Por seguridad, no puedes quitarte el rol ADMIN a ti mismo.")
                return redirect("administrador:usuarios")

            u.email = email
            u.username = username
            if hasattr(u, "rol") and nuevo_rol:
                u.rol = nuevo_rol
            _set_full_name(u, nombre)

            u.save()
            _audit(request, f"USUARIOS | EDITAR | {u.username} ({u.email})")
            messages.success(request, f"Usuario actualizado: {u.username}.")
            return redirect("administrador:usuarios")

        if accion == "eliminar":
            if u.pk == request.user.pk:
                messages.error(request, "No puedes eliminar tu propia cuenta.")
                return redirect("administrador:usuarios")

            if _is_admin_user(u):
                admins_totales = User.objects.filter(rol="ADMIN").count()
                if admins_totales <= 1:
                    messages.error(request, "No puedes eliminar al último administrador del sistema.")
                    return redirect("administrador:usuarios")

            _audit(request, f"USUARIOS | ELIMINAR | {u.username} ({u.email})")
            u.delete()
            messages.success(request, "Usuario eliminado correctamente.")
            return redirect("administrador:usuarios")

        messages.error(request, "Acción no válida.")
        return redirect("administrador:usuarios")

    q = (request.GET.get("q") or "").strip()
    rol_filter = (request.GET.get("rol") or "").strip().upper()
    estado = (request.GET.get("estado") or "").strip().lower()

    usuarios_qs = User.objects.all().order_by("id")

    if q:
        usuarios_qs = usuarios_qs.filter(
            Q(username__icontains=q) |
            Q(email__icontains=q) |
            Q(first_name__icontains=q)
        )

    if rol_filter in roles:
        usuarios_qs = usuarios_qs.filter(rol=rol_filter)

    if estado == "activos":
        usuarios_qs = usuarios_qs.filter(is_active=True)
    elif estado == "inactivos":
        usuarios_qs = usuarios_qs.filter(is_active=False)

    context = {
        "active": "usuarios",
        "usuarios": usuarios_qs,
        "roles": roles,
        "q": q,
        "rol_filter": rol_filter,
        "estado": estado,
    }
    return render(request, "administrador/usuarios/usuarios.html", context)


# =========================
# Roles y Permisos
# =========================
@admin_required
def roles(request: HttpRequest):
    context = {"active": "roles", "roles": _get_roles_disponibles()}
    return render(request, "administrador/roles/roles_permisos.html", context)


# =========================
# Eventos
# =========================
@admin_required
def eventos(request: HttpRequest):
    q = (request.GET.get("q") or "").strip()
    estado = (request.GET.get("estado") or "").strip().upper()

    qs = Evento.objects.all()
    if q:
        qs = qs.filter(Q(titulo__icontains=q) | Q(descripcion__icontains=q) | Q(lugar__icontains=q))
    if estado in ("BORRADOR", "PUBLICADO"):
        qs = qs.filter(estado=estado)

    return render(request, "administrador/eventos/eventos.html", {"active": "eventos", "eventos": qs})


# =========================
# Auditoría
# =========================
@admin_required
def auditoria(request: HttpRequest):
    logs = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:500]

    hoy = timezone.localdate()
    total_logs = AuditoriaLog.objects.count()
    logs_hoy = AuditoriaLog.objects.filter(fecha__date=hoy).count()
    logs_24h = AuditoriaLog.objects.filter(fecha__gte=timezone.now() - timezone.timedelta(hours=24)).count()
    ultimo_log = AuditoriaLog.objects.order_by("-fecha").first()

    usuarios_filtro = User.objects.order_by("first_name", "email")[:200]

    context = {
        "active": "auditoria",
        "logs": logs,
        "total_logs": total_logs,
        "logs_hoy": logs_hoy,
        "logs_24h": logs_24h,
        "ultimo_log": ultimo_log,
        "usuarios_filtro": usuarios_filtro,
        "q": "",
        "usuario_id": "",
        "desde": "",
        "hasta": "",
    }
    return render(request, "administrador/auditoria/auditoria.html", context)


@admin_required
def auditoria_export_csv(request: HttpRequest):
    logs = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:5000]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="auditoria_sigep.csv"'
    writer = csv.writer(response)

    writer.writerow(["id", "fecha", "usuario", "email", "accion", "ip_origen"])
    for l in logs:
        writer.writerow([
            l.id,
            timezone.localtime(l.fecha).strftime("%Y-%m-%d %H:%M:%S"),
            (l.usuario.get_full_name() if l.usuario else "Sistema"),
            (l.usuario.email if l.usuario else ""),
            l.accion,
            l.ip_origen or "",
        ])

    _audit(request, "AUDITORIA | EXPORTAR_CSV | auditoria_sigep.csv")
    return response


# =========================
# Reporte general CSV
# =========================
@admin_required
def reporte_csv(request: HttpRequest):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="reporte_sigep.csv"'
    writer = csv.writer(response)

    writer.writerow(["SIGEP | REPORTE GENERAL"])
    writer.writerow(["Generado", timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    writer.writerow(["== USUARIOS =="])
    writer.writerow(["id", "username", "email", "rol", "activo", "date_joined"])
    for u in User.objects.all().order_by("id"):
        writer.writerow([
            u.id,
            getattr(u, "username", ""),
            getattr(u, "email", ""),
            getattr(u, "rol", ""),
            "SI" if getattr(u, "is_active", False) else "NO",
            getattr(u, "date_joined", ""),
        ])
    writer.writerow([])

    writer.writerow(["== EVENTOS =="])
    writer.writerow(["id", "titulo", "fecha", "lugar", "cupo", "estado", "creado_en"])
    for e in Evento.objects.all().order_by("id"):
        writer.writerow([
            e.id,
            e.titulo,
            e.fecha,
            e.lugar,
            e.cupo,
            e.estado,
            timezone.localtime(e.creado_en).strftime("%Y-%m-%d %H:%M:%S") if getattr(e, "creado_en", None) else "",
        ])
    writer.writerow([])

    writer.writerow(["== AUDITORÍA (últimos 500) =="])
    writer.writerow(["id", "fecha", "usuario", "email", "accion", "ip_origen"])
    logs = AuditoriaLog.objects.select_related("usuario").order_by("-fecha")[:500]
    for l in logs:
        writer.writerow([
            l.id,
            timezone.localtime(l.fecha).strftime("%Y-%m-%d %H:%M:%S"),
            (l.usuario.get_full_name() if l.usuario else "Sistema"),
            (l.usuario.email if l.usuario else ""),
            l.accion,
            l.ip_origen or "",
        ])

    _audit(request, "REPORTE | EXPORTAR_CSV | reporte_sigep.csv")
    return response


# =========================
# Configuración
# =========================
@admin_required
def configuracion(request: HttpRequest):
    allowed_keys = {
        "sistema_nombre": {"default": "SIGEP"},
        "sistema_correo_soporte": {"default": ""},
        "sistema_telefono_soporte": {"default": ""},

        "seguridad_max_intentos_login": {"default": "5"},
        "seguridad_bloqueo_minutos": {"default": "15"},
        "seguridad_requiere_2fa": {"default": "0"},

        "notif_email_habilitado": {"default": "1"},
        "notif_email_remitente": {"default": getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""},
        "notif_email_asunto_base": {"default": "SIGEP | Notificación"},

        "mantenimiento_modo": {"default": "0"},
        "mantenimiento_mensaje": {"default": "Sistema en mantenimiento. Intenta más tarde."},
    }

    boolean_keys = {"seguridad_requiere_2fa", "notif_email_habilitado", "mantenimiento_modo"}

    def get_config_value(key: str) -> str:
        obj = ConfiguracionSistema.objects.filter(clave=key).first()
        if obj and obj.valor is not None:
            return obj.valor
        return allowed_keys[key]["default"]

    def set_config_value(key: str, value: str) -> None:
        ConfiguracionSistema.objects.update_or_create(
            clave=key,
            defaults={"valor": value, "actualizado_por": request.user},
        )

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip()
        if accion == "cancelar":
            return redirect("administrador:configuracion")

        payload = {}
        for key in allowed_keys.keys():
            if key in boolean_keys:
                payload[key] = "1" if request.POST.get(key) in ("on", "1", "true", "True") else "0"
            else:
                payload[key] = (request.POST.get(key) or "").strip()

        def _to_int(s: str, fallback: int) -> int:
            try:
                return int(s)
            except Exception:
                return fallback

        payload["seguridad_max_intentos_login"] = str(max(1, _to_int(payload["seguridad_max_intentos_login"], 5)))
        payload["seguridad_bloqueo_minutos"] = str(max(1, _to_int(payload["seguridad_bloqueo_minutos"], 15)))

        with transaction.atomic():
            for k, v in payload.items():
                set_config_value(k, v)

        _audit(request, "CONFIGURACIÓN | GUARDAR | Se actualizaron parámetros del sistema")
        messages.success(request, "Configuración guardada correctamente.")
        return redirect("administrador:configuracion")

    values = {k: get_config_value(k) for k in allowed_keys.keys()}
    bools = {k: (values[k] == "1") for k in boolean_keys}

    historial = AuditoriaLog.objects.select_related("usuario").filter(
        accion__icontains="CONFIGURACIÓN"
    ).order_by("-fecha")[:50]

    context = {"active": "configuracion", "cfg": values, "cfg_bool": bools, "historial": historial}
    return render(request, "administrador/configuracion/configuracion.html", context)


# =========================
# Perfil admin: username / foto
# =========================
@admin_required
def perfil_actualizar(request: HttpRequest):
    if request.method != "POST":
        return redirect("administrador:configuracion")

    accion = (request.POST.get("accion") or "").strip()

    if accion == "cambiar_username":
        new_username = (request.POST.get("username") or "").strip()
        if not new_username:
            messages.error(request, "El nombre de usuario no puede ir vacío.")
            return redirect("administrador:configuracion")
        if User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
            messages.error(request, "Ese nombre de usuario ya está en uso.")
            return redirect("administrador:configuracion")

        request.user.username = new_username
        request.user.save(update_fields=["username"])
        _audit(request, f"PERFIL | CAMBIAR_USERNAME | {new_username}")
        messages.success(request, "Nombre de usuario actualizado.")
        return redirect("administrador:configuracion")

    if accion == "subir_foto":
        foto = request.FILES.get("foto")
        if not foto:
            messages.error(request, "Selecciona una imagen para subir.")
            return redirect("administrador:configuracion")

        if not hasattr(request.user, "foto"):
            messages.error(request, "Tu modelo de usuario aún no tiene el campo 'foto'.")
            return redirect("administrador:configuracion")

        request.user.foto = foto
        request.user.save(update_fields=["foto"])
        _audit(request, "PERFIL | ACTUALIZAR_FOTO")
        messages.success(request, "Foto de perfil actualizada.")
        return redirect("administrador:configuracion")

    messages.error(request, "Acción inválida.")
    return redirect("administrador:configuracion")


# =========================
# Perfil admin: password
# =========================
@admin_required
def perfil_cambiar_password(request: HttpRequest):
    if request.method != "POST":
        return redirect("administrador:configuracion")

    current = request.POST.get("current_password") or ""
    p1 = request.POST.get("new_password") or ""
    p2 = request.POST.get("confirm_password") or ""

    if not request.user.check_password(current):
        messages.error(request, "Tu contraseña actual es incorrecta.")
        return redirect("administrador:configuracion")

    if p1 != p2:
        messages.error(request, "La nueva contraseña y su confirmación no coinciden.")
        return redirect("administrador:configuracion")

    try:
        validate_password(p1, user=request.user)
    except ValidationError as e:
        messages.error(request, "Contraseña no válida: " + " ".join(e.messages))
        return redirect("administrador:configuracion")

    request.user.set_password(p1)
    request.user.save()
    update_session_auth_hash(request, request.user)

    _audit(request, "PERFIL | CAMBIAR_PASSWORD")
    messages.success(request, "Contraseña actualizada correctamente.")
    return redirect("administrador:configuracion")


# =========================
# Salir
# =========================
@login_required
def salir(request: HttpRequest):
    logout(request)
    return redirect("principal:login")
