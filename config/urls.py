from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.urls import include, path, re_path
from django.views.static import serve

from usuarios.decorators import rol_requerido
from django.contrib.auth.decorators import login_required

admin.site.site_header = "Sistema de Apoyo a la Auditoría Externa"
admin.site.site_title = "ST&S Auditores"
admin.site.index_title = "Panel de administración"


def _login_admin_con_2fa(request, extra_context=None):
    """El admin usa el login del sistema, con segundo factor y bloqueo por intentos."""
    return redirect_to_login(request.GET.get("next") or "/admin/", login_url="login")


admin.site.login = _login_admin_con_2fa


@login_required
def _servir_avatar(request, path):
    """Fotos de perfil: solo para usuarios con sesión iniciada."""
    return serve(request, "avatares/" + path, document_root=settings.MEDIA_ROOT)


@rol_requerido("Administrador", "Auditor")
def _servir_original(request, path):
    """Originales contables: solo con sesión y rol de negocio."""
    return serve(request, "cargas/" + path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("usuarios.urls")),
    path("registros/", include("registros.urls")),
    # Archivos subidos (MEDIA): siempre a través de vistas con control de acceso, nunca
    # con `static()` abierto.
    re_path(r"^media/avatares/(?P<path>.+)$", _servir_avatar),
    re_path(r"^media/cargas/(?P<path>.+)$", _servir_original),
]
