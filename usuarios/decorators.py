"""Control de acceso por roles (RF-07)."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def rol_requerido(*roles):
    """Restringe una vista a usuarios autenticados que pertenezcan a alguno de los
    grupos (roles) indicados.
    """

    def decorador(vista):
        @login_required
        @wraps(vista)
        def envoltura(request, *args, **kwargs):
            usuario = request.user
            if usuario.is_superuser or usuario.groups.filter(name__in=roles).exists():
                return vista(request, *args, **kwargs)

            messages.error(
                request,
                "No tienes el rol necesario para acceder a esa sección "
                f"({', '.join(roles)}).",
            )
            return redirect("home")

        return envoltura

    return decorador
