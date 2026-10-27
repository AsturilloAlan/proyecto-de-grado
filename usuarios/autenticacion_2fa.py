"""Verificación en dos pasos (2FA) por correo, al iniciar sesión."""
import hmac
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from . import correos
from .models import MAXIMO_INTENTOS_CODIGO, MINUTOS_VALIDEZ_CODIGO, CodigoVerificacion

CLAVE_SESION_USUARIO_PENDIENTE = "sesion_2fa_pendiente"


def _dispositivo(request):
    """Descripción corta del navegador y sistema operativo, a partir del User-Agent."""
    agente = (request.META.get("HTTP_USER_AGENT", "") if request else "").lower()
    navegador = next(
        (nombre for clave, nombre in (
            ("edg/", "Edge"), ("opr/", "Opera"), ("chrome/", "Chrome"),
            ("firefox/", "Firefox"), ("safari/", "Safari"),
        ) if clave in agente),
        "Navegador desconocido",
    )
    sistema = next(
        (nombre for clave, nombre in (
            ("windows", "Windows"), ("android", "Android"), ("iphone", "iPhone"),
            ("mac os", "macOS"), ("linux", "Linux"),
        ) if clave in agente),
        "sistema desconocido",
    )
    return f"{navegador} en {sistema}"


def enviar_codigo(usuario, request=None):
    """Genera un código nuevo para el usuario y lo envía por correo (HTML con texto de respaldo).
    Devuelve el objeto CodigoVerificacion creado."""
    codigo = CodigoVerificacion.generar_para(usuario)
    ahora = timezone.localtime(codigo.creado)
    vence = timezone.localtime(codigo.creado + timedelta(minutes=MINUTOS_VALIDEZ_CODIGO))
    contexto = {
        "nombre": usuario.first_name or usuario.username,
        "usuario": usuario.username,
        "codigo": codigo.codigo,
        "digitos": list(codigo.codigo),
        "minutos": MINUTOS_VALIDEZ_CODIGO,
        "vence": vence,
        "fecha_solicitud": ahora,
        "ip": (request.META.get("REMOTE_ADDR", "") if request else "") or "No disponible",
        "dispositivo": _dispositivo(request),
    }
    correos.enviar(
        "codigo_2fa", usuario, usuario.email,
        asunto="Código de verificación - ST&S Auditores",
        texto=lambda ref: render_to_string("usuarios/correo_codigo.txt", {**contexto, "referencia": ref}),
        html=lambda ref: render_to_string("usuarios/correo_codigo.html", {**contexto, "referencia": ref}),
        request=request,
    )
    return codigo


def validar_codigo(usuario, codigo_ingresado, codigo_id=None):
    """Valida el código ingresado contra el más reciente y no usado del
    usuario. Devuelve (ok: bool, mensaje_error: str | None)."""
    candidatos = CodigoVerificacion.objects.filter(usuario=usuario, usado=False)
    if codigo_id is not None:
        candidatos = candidatos.filter(pk=codigo_id)
    ultimo = (
        candidatos
        .order_by("-creado")
        .first()
    )
    if ultimo is None:
        return False, "No hay un código activo. Solicita uno nuevo."

    if ultimo.expirado():
        return False, "El código venció. Solicita uno nuevo."

    if ultimo.bloqueado_por_intentos():
        return False, "Este código ya no sirve: pide uno nuevo con «Reenviar código»."

    if not hmac.compare_digest(ultimo.codigo.encode(), codigo_ingresado.strip().encode()):
        # F() evita perder incrementos en intentos simultáneos.
        CodigoVerificacion.objects.filter(pk=ultimo.pk).update(intentos=F("intentos") + 1)
        if ultimo.intentos + 1 >= MAXIMO_INTENTOS_CODIGO:
            return False, "Código incorrecto. Este código ya no sirve: pide uno nuevo con «Reenviar código»."
        return False, "El código ingresado no es correcto."

    # La actualización atómica permite consumir el código una sola vez.
    consumido = CodigoVerificacion.objects.filter(
        pk=ultimo.pk, usado=False, intentos__lt=MAXIMO_INTENTOS_CODIGO
    ).update(usado=True)
    if not consumido:
        return False, "El código ya no es válido. Solicita uno nuevo."
    return True, None
