"""
Perfil de usuario: datos adicionales que Django no guarda en su modelo
`User` por defecto (aquí, la foto de perfil/avatar).

Se modela como una tabla aparte (relación 1 a 1 con `User`) en vez de
extender directamente el modelo de usuario de Django, para no tocar el
sistema de autenticación ya construido (RF-07) — cumple con no repetir
datos: el usuario, contraseña, permisos y grupos siguen viviendo solo en
`auth_user`.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# Avatares predefinidos: para quien no quiera subir una foto propia.
# (id, color de fondo, ícono de Bootstrap Icons)
PRESETS_AVATAR = [
    ("guindo", "#6E1B3F", "bi-person-fill"),
    ("guindo_oscuro", "#4A1129", "bi-shield-check"),
    ("azul", "#1d4ed8", "bi-person-badge-fill"),
    ("verde", "#15803d", "bi-briefcase-fill"),
    ("teal", "#0f766e", "bi-person-workspace"),
    ("naranja", "#c2410c", "bi-mortarboard-fill"),
    ("gris", "#4b5563", "bi-person-gear"),
    ("dorado", "#a16207", "bi-award-fill"),
]
PRESETS_AVATAR_CHOICES = [(clave, clave) for clave, _color, _icono in PRESETS_AVATAR]
PRESETS_AVATAR_MAPA = {clave: (color, icono) for clave, color, icono in PRESETS_AVATAR}


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil"
    )
    avatar = models.ImageField(
        "Foto de perfil", upload_to="avatares/", blank=True, null=True
    )
    avatar_preset = models.CharField(
        "Avatar predefinido", max_length=20, choices=PRESETS_AVATAR_CHOICES, blank=True
    )

    class Meta:
        verbose_name = "Perfil de Usuario"
        verbose_name_plural = "Perfiles de Usuario"

    def __str__(self):
        return f"Perfil de {self.usuario.username}"

    @property
    def avatar_preset_color(self):
        return PRESETS_AVATAR_MAPA.get(self.avatar_preset, (None, None))[0]

    @property
    def avatar_preset_icono(self):
        return PRESETS_AVATAR_MAPA.get(self.avatar_preset, (None, None))[1]


MINUTOS_VALIDEZ_CODIGO = 5
MAXIMO_INTENTOS_CODIGO = 5


class CodigoVerificacion(models.Model):
    """Código de un solo uso para la verificación en dos pasos (2FA) al
    iniciar sesión: tras validar usuario/contraseña, se genera un código
    de 6 dígitos, se envía por correo, y debe ingresarse antes de
    completar el login. Expira a los pocos minutos y se invalida tras
    varios intentos fallidos, para no dejarlo abierto indefinidamente."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="codigos_verificacion",
    )
    codigo = models.CharField(max_length=6)
    creado = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False)
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Código de verificación"
        verbose_name_plural = "Códigos de verificación"
        ordering = ["-creado"]

    def __str__(self):
        return f"Código para {self.usuario.username} ({'usado' if self.usado else 'pendiente'})"

    def expirado(self):
        return timezone.now() > self.creado + timedelta(minutes=MINUTOS_VALIDEZ_CODIGO)

    def bloqueado_por_intentos(self):
        return self.intentos >= MAXIMO_INTENTOS_CODIGO

    @classmethod
    def generar_para(cls, usuario):
        """Invalida códigos anteriores no usados del usuario y crea uno nuevo."""
        cls.objects.filter(usuario=usuario, usado=False).update(usado=True)
        codigo = f"{random.randint(0, 999999):06d}"
        return cls.objects.create(usuario=usuario, codigo=codigo)
