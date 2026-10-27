from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.shortcuts import redirect

from . import jwt_tokens


@receiver(user_logged_in)
def _marcar_emision_de_token(sender, request, user, **kwargs):
    """Al iniciar sesión se pide emitir el JWT en la respuesta."""
    if request is not None and hasattr(request, "session"):
        request.session[jwt_tokens.CLAVE_SESION_EMITIR] = True


class JWTMiddleware:
    """Exige un JWT válido a todo usuario con sesión iniciada (ver usuarios.jwt_tokens)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _excluida(self, request):
        static = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        return request.path.startswith("/" + static.lstrip("/"))

    def __call__(self, request):
        if self._excluida(request):
            return self.get_response(request)

        renovar = False
        if request.user.is_authenticated and not request.session.get(jwt_tokens.CLAVE_SESION_EMITIR):
            datos = jwt_tokens.validar(
                request.COOKIES.get(jwt_tokens.NOMBRE_COOKIE),
                request.user,
                request.session.session_key,
            )
            if datos is None:
                logout(request)
                messages.info(request, "La sesión expiró o no es válida. Iniciar sesión de nuevo.")
                respuesta = redirect("login")
                jwt_tokens.borrar_cookie(respuesta)
                return respuesta
            renovar = jwt_tokens.debe_renovarse(datos)

        respuesta = self.get_response(request)

        usuario = getattr(request, "user", None)
        if usuario is not None and usuario.is_authenticated:
            if request.session.pop(jwt_tokens.CLAVE_SESION_EMITIR, False) or renovar:
                if not request.session.session_key:
                    request.session.save()
                jwt_tokens.poner_cookie(
                    respuesta, jwt_tokens.emitir(usuario, request.session.session_key)
                )
        elif jwt_tokens.NOMBRE_COOKIE in request.COOKIES:
            # Cerró sesión (o la sesión venció): el token deja de servir.
            jwt_tokens.borrar_cookie(respuesta)
        return respuesta


class SinCacheMiddleware:
    """Evita que el navegador guarde una copia en caché de las páginas."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        return response
