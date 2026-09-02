"""Verificación en dos pasos (2FA) por correo, al iniciar sesión.

Flujo:
1. El usuario ingresa usuario/contraseña en el login normal.
2. Si son correctos, en vez de completar el login de inmediato, se
   genera un código de 6 dígitos (`CodigoVerificacion`), se envía por
   correo, y se guarda en la sesión el id del usuario "pendiente de
   verificar" (`sesion_2fa_pendiente`).
3. El usuario ingresa el código en `verificar_codigo`; si coincide, no
   expiró y no se agotaron los intentos, recién ahí se completa el
   login con `django.contrib.auth.login`.

Este módulo concentra la lógica de generar/enviar/validar el código
para no mezclarla con las vistas.
"""
from django.core.mail import send_mail
from django.conf import settings

from .models import CodigoVerificacion

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

    if ultimo.codigo != codigo_ingresado.strip():
        ultimo.intentos += 1
        ultimo.save(update_fields=["intentos"])
        return False, "El código ingresado no es correcto."

    ultimo.usado = True
    ultimo.save(update_fields=["usado"])
    return True, None
