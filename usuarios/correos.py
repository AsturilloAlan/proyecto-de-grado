"""Envío de correos del sistema con registro único por envío (RegistroCorreo).

Todo correo sale por `enviar`: se guarda el tipo, el destinatario, si se envió o falló,
y un identificador único que aparece al pie del correo. Así, ante un reclamo ("no me
llegó el código", "yo no pedí esto"), se puede rastrear cada envío.
"""
import logging
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import RegistroCorreo

logger = logging.getLogger(__name__)


def _ip(request):
    return (request.META.get("REMOTE_ADDR") or None) if request is not None else None


def _logo():
    ruta = Path(settings.BASE_DIR) / "static" / "img" / "logo-sts-claro.png"
    if not ruta.exists():
        return None
    imagen = MIMEImage(ruta.read_bytes())
    imagen.add_header("Content-ID", "<logo_sts>")
    imagen.add_header("Content-Disposition", "inline", filename="logo-sts.png")
    return imagen


def enviar(tipo, usuario, destinatario, asunto, texto, html=None, request=None,
           fallar_en_silencio=False, detalle=""):
    """Envía un correo y deja su registro. `texto` y `html` pueden ser funciones que
    reciben el identificador del registro (para mostrarlo en el pie del correo).
    Devuelve el RegistroCorreo; si falla y no es silencioso, vuelve a lanzar el error.
    """
    registro = RegistroCorreo.objects.create(
        tipo=tipo, usuario=usuario, destinatario=destinatario, ip=_ip(request), detalle=detalle[:255])
    referencia = str(registro.identificador)[:8].upper()
    cuerpo = texto(referencia) if callable(texto) else texto
    correo = EmailMultiAlternatives(
        subject=asunto, body=cuerpo, from_email=settings.DEFAULT_FROM_EMAIL, to=[destinatario])
    if html is not None:
        correo.attach_alternative(html(referencia) if callable(html) else html, "text/html")
        correo.mixed_subtype = "related"
        logo = _logo()
        if logo is not None:
            correo.attach(logo)
    try:
        correo.send(fail_silently=False)
    except Exception as error:
        RegistroCorreo.objects.filter(pk=registro.pk).update(
            detalle=(f"{detalle} · Falló: {type(error).__name__}" if detalle else f"Falló: {type(error).__name__}")[:255])
        logger.warning("Correo %s (%s) a usuario %s no enviado: %s",
                       referencia, tipo, getattr(usuario, "pk", None), type(error).__name__)
        if not fallar_en_silencio:
            raise
        return registro
    RegistroCorreo.objects.filter(pk=registro.pk).update(enviado=True)
    registro.enviado = True
    return registro
