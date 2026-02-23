from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    path("", include(("home.urls", "home"), namespace="home")),

    path("principal/", include(("principal.urls", "principal"), namespace="principal")),
    path("administrador/", include(("administrador.urls", "administrador"), namespace="administrador")),
    path("coordinador/", include(("coordinador.urls", "coordinador"), namespace="coordinador")),
    path("ponente/", include(("ponente.urls", "ponente"), namespace="ponente")),
    path("participante/", include(("participante.urls", "participante"), namespace="participante")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
