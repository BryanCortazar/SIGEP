from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

def root_redirect(request):
    # Landing del sistema (puedes cambiarlo a donde tú quieras)
    return redirect("principal:login")

urlpatterns = [
    path("admin/", admin.site.urls),
    
     path("", include("home.urls")),

    # ✅ raíz del sitio
    path("", root_redirect),

    # ✅ apps (rutas por módulo)
    path("principal/", include("principal.urls")),
    path("administrador/", include("administrador.urls")),
    path("coordinador/", include("coordinador.urls")),
    path("evaluador/", include("evaluador.urls")),
    path("ponente/", include("ponente.urls")),

    # Si ya tienes estos módulos creados y con urls.py, descomenta:
    # path("participante/", include("participante.urls")),

]