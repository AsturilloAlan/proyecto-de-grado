"""Verificación en dos pasos (2FA) por correo, al iniciar sesión."""
import hmac

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F

from .models import MAXIMO_INTENTOS_CODIGO, CodigoVerificacion

CLAVE_SESION_USUARIO_PENDIENTE = "sesion_2fa_pendiente"


def enviar_codigo(usuario):
    """Genera un código nuevo para el usuario y lo envía por correo.
    Devuelve el objeto CodigoVerificacion creado."""
    codigo = CodigoVerificacion.generar_para(usuario)
    send_mail(
        subject="Tu código de verificación - ST&S Auditores",
        message=(
            f"Hola {usuario.first_name or usuario.username},\n\n"
            f"Tu código de verificación para iniciar sesión es: {codigo.codigo}\n\n"
            f"Este código vence en {codigo.creado.strftime('%H:%M')} + "
            "5 minutos. Si no intentaste iniciar sesión, ignora este correo."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[usuario.email],
        fail_silently=False,
    )
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
