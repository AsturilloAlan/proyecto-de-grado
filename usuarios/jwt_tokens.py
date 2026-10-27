"""JSON Web Token (JWT) del inicio de sesión.

Tras la verificación en dos pasos, el sistema emite un token firmado (HS256) con el
usuario, su rol y la sesión a la que pertenece. El token viaja en una cookie HttpOnly,
por lo que el JavaScript de la página no puede leerlo (protección ante XSS). En cada
petición el middleware verifica la firma, la vigencia y que el token corresponda al
usuario y a la sesión; si falla, cierra la sesión. El token se renueva solo mientras el
usuario sigue activo y vence a los 30 minutos de inactividad, igual que la sesión.
"""
import hashlib
import uuid
from datetime import timedelta

import jwt
from django.conf import settings
from django.utils import timezone

ALGORITMO = "HS256"
EMISOR = "sistema-auditoria-sts"
NOMBRE_COOKIE = "token_acceso"
# La sesión marca aquí que el siguiente paso por el middleware debe emitir el token.
CLAVE_SESION_EMITIR = "jwt_emitir"


def _secreto():
    return getattr(settings, "JWT_SECRET", None) or settings.SECRET_KEY


def minutos_vigencia():
    return getattr(settings, "JWT_MINUTOS_VIGENCIA", settings.SESSION_COOKIE_AGE // 60)


def huella_sesion(session_key):
    """Identifica la sesión sin poner su clave dentro del token."""
    return hashlib.sha256(f"{session_key}".encode()).hexdigest()[:32]


def rol_de(usuario):
    if usuario.is_superuser or usuario.groups.filter(name="Administrador").exists():
        return "Administrador"
    if usuario.groups.filter(name="Auditor").exists():
        return "Auditor"
    return ""


def emitir(usuario, session_key):
    ahora = timezone.now()
    datos = {
        "iss": EMISOR,
        "sub": str(usuario.pk),
        "usuario": usuario.get_username(),
        "rol": rol_de(usuario),
        "ses": huella_sesion(session_key),
        "iat": ahora,
        "exp": ahora + timedelta(minutes=minutos_vigencia()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(datos, _secreto(), algorithm=ALGORITMO)


def validar(token, usuario, session_key):
    """Devuelve los datos del token si es válido para ese usuario y sesión; si no, None."""
    if not token:
        return None
    try:
        datos = jwt.decode(
            token,
            _secreto(),
            algorithms=[ALGORITMO],
            issuer=EMISOR,
            options={"require": ["exp", "iat", "sub", "ses", "iss"]},
            leeway=10,
        )
    except jwt.PyJWTError:
        return None
    if datos["sub"] != str(usuario.pk) or datos["ses"] != huella_sesion(session_key):
        return None
    return datos


def debe_renovarse(datos):
    """Se renueva cuando ya pasó la mitad de su vigencia."""
    restante = datos["exp"] - timezone.now().timestamp()
    return restante < minutos_vigencia() * 60 / 2


def poner_cookie(respuesta, token):
    respuesta.set_cookie(
        NOMBRE_COOKIE,
        token,
        max_age=minutos_vigencia() * 60,
        httponly=True,
        secure=getattr(settings, "USAR_HTTPS", False),
        samesite="Lax",
    )


def borrar_cookie(respuesta):
    respuesta.delete_cookie(NOMBRE_COOKIE, samesite="Lax")
