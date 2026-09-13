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
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone


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


def token_requerido(vista):
    """Autenticación por token para endpoints pensados para integraciones
    externas (ej. `/api/mis-cargas/`), como alternativa a la sesión del
    navegador — ver `TokenAcceso` en models.py.

    Se espera el encabezado `Authorization: Bearer <token>`. A diferencia
    de `login_required`/`rol_requerido`, esta vista NO depende de la
    sesión ni de las cookies: identifica al usuario únicamente por el
    token, así que sirve para un script que solo hace una petición HTTP
    (sin pasar antes por el formulario de login ni por el 2FA).
    """

    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        # Import local: evita un ciclo de imports entre decorators.py y
        # models.py al cargarse la app.
        from .models import TokenAcceso

        encabezado = request.META.get("HTTP_AUTHORIZATION", "")
        if not encabezado.startswith("Bearer "):
            return JsonResponse(
                {"error": "Falta el encabezado 'Authorization: Bearer <token>'."},
                status=401,
            )

        valor_token = encabezado.removeprefix("Bearer ").strip()
        try:
            token = TokenAcceso.objects.select_related("usuario").get(token=valor_token)
        except TokenAcceso.DoesNotExist:
            return JsonResponse({"error": "Token inválido."}, status=401)

        token.ultimo_uso = timezone.now()
        token.save(update_fields=["ultimo_uso"])

        request.usuario_token = token.usuario
        return vista(request, *args, **kwargs)

    return envoltura
