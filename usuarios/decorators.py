"""Control de acceso por roles (RF-07).

Los superusuarios (staff) siempre tienen acceso completo, sin importar
el rol asignado — así el administrador técnico del sistema nunca queda
bloqueado por su propia configuración de grupos.

Validaciones de redirección:
- Usuario no autenticado que intenta entrar a una URL protegida →
  `login_required` lo redirige a la página de login (con `?next=` para
  volver automáticamente a la URL original tras iniciar sesión).
- Usuario autenticado pero sin el rol requerido → en vez de mostrar la
  página de error 403 por defecto de Django, se le redirige a `home`
  con un mensaje explicando por qué no pudo entrar.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def rol_requerido(*roles):
    """Restringe una vista a usuarios autenticados que pertenezcan a
    alguno de los grupos (roles) indicados.

    Uso:
        @rol_requerido("Administrador")
        def gestionar_empresas(request): ...
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
