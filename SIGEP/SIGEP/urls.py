from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    
    path("admin/", admin.site.urls),
    path("", include("home.urls")),

    path("django-admin/", admin.site.urls),
    path("", include("principal.urls")),
    path("administrador/", include("administrador.urls")),
    path("coordinador/", include("coordinador.urls")),
    path("evaluador/", include("evaluador.urls")),
    path("ponente/", include("ponente.urls")),
    path("participante/", include("participante.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
