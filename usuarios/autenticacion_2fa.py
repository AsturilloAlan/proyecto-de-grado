"""Verificación en dos pasos (2FA) por correo, al iniciar sesión."""
import hmac
from datetime import timedelta
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

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
    texto = render_to_string("usuarios/correo_codigo.txt", contexto)
    html = render_to_string("usuarios/correo_codigo.html", contexto)

    correo = EmailMultiAlternatives(
        subject="Código de verificación - ST&S Auditores",
        body=texto,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[usuario.email],
    )
    correo.attach_alternative(html, "text/html")
    correo.mixed_subtype = "related"
    ruta_logo = Path(settings.BASE_DIR) / "static" / "img" / "logo-sts-claro.png"
    if ruta_logo.exists():
        imagen = MIMEImage(ruta_logo.read_bytes())
        imagen.add_header("Content-ID", "<logo_sts>")
        imagen.add_header("Content-Disposition", "inline", filename="logo-sts.png")
        correo.attach(imagen)
    correo.send(fail_silently=False)
    return codigo


def validar_codigo(usuario, codigo_ingresado):
    """Valida el código ingresado contra el más reciente y no usado del
    usuario. Devuelve (ok: bool, mensaje_error: str | None)."""
    ultimo = (
        CodigoVerificacion.objects.filter(usuario=usuario, usado=False)
        .order_by("-creado")
        .first()
    )
    if ultimo is None:
        return False, "No hay un código activo. Solicita uno nuevo."

    if ultimo.expirado():
        return False, "El código venció. Solicita uno nuevo."

    if ultimo.bloqueado_por_intentos():
        return False, "Se agotaron los intentos para este código. Solicita uno nuevo."

    if not hmac.compare_digest(ultimo.codigo.encode(), codigo_ingresado.strip().encode()):
        # Incremento atómico en la base (F()): dos intentos simultáneos no
        # pueden "pisarse" y perder un fallo del contador.
        CodigoVerificacion.objects.filter(pk=ultimo.pk).update(intentos=F("intentos") + 1)
        return False, "El código ingresado no es correcto."

    # Consumo atómico: solo uno de dos envíos simultáneos del mismo código
    # puede marcarlo como usado; el otro recibe 0 filas actualizadas.
    consumido = CodigoVerificacion.objects.filter(
        pk=ultimo.pk, usado=False, intentos__lt=MAXIMO_INTENTOS_CODIGO
    ).update(usado=True)
    if not consumido:
        return False, "El código ya no es válido. Solicita uno nuevo."
    return True, None
