"""Reglas de contraseña propias del sistema (además de las de Django).

- ComplejidadValidator: al menos una mayúscula, una minúscula, un número y un símbolo.
- HistorialValidator: no repetir ni hacer variantes simples de las últimas contraseñas
  ("Clave2025" → "Clave2026" o "Clave20251"). Para comparar sin guardar contraseñas en
  claro se guarda, cifrada como una contraseña, su "raíz": solo las letras, en minúsculas
  y sin tildes. Dos contraseñas con la misma raíz se consideran parecidas.
"""
import unicodedata
from difflib import SequenceMatcher

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError

LARGO_MINIMO_RAIZ = 4


def raiz(clave):
    """Letras de la contraseña, en minúsculas y sin tildes: 'Clave-2025!' → 'clave'."""
    sin_tildes = unicodedata.normalize("NFKD", clave or "").encode("ascii", "ignore").decode()
    return "".join(c for c in sin_tildes.lower() if c.isalpha())


def muy_parecidas(anterior, nueva, umbral=0.7):
    """Comparación directa cuando se conoce la contraseña anterior (cambio desde el perfil)."""
    a, b = (anterior or "").lower(), (nueva or "").lower()
    if not a or not b:
        return False
    if a == b or (len(raiz(a)) >= LARGO_MINIMO_RAIZ and raiz(a) == raiz(b)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= umbral


class ComplejidadValidator:
    def validate(self, password, user=None):
        if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in password):
            raise ValidationError("La contraseña no puede contener caracteres de control o invisibles.", code="password_invisible")
        faltan = []
        if not any(c.isupper() for c in password):
            faltan.append("una mayúscula")
        if not any(c.islower() for c in password):
            faltan.append("una minúscula")
        if not any(c.isdigit() for c in password):
            faltan.append("un número")
        if not any(unicodedata.category(c)[0] in {"P", "S"} for c in password):
            faltan.append("un símbolo (por ejemplo - _ . ! #)")
        if faltan:
            raise ValidationError(
                "La contraseña debe tener al menos " + ", ".join(faltan) + ".",
                code="password_sin_complejidad")

    def get_help_text(self):
        return "Debe combinar mayúsculas, minúsculas, números y al menos un símbolo."


class HistorialValidator:
    def __init__(self, cantidad=5):
        self.cantidad = cantidad

    def validate(self, password, user=None):
        if user is None or not getattr(user, "pk", None):
            return
        if user.has_usable_password() and user.check_password(password):
            raise ValidationError("La contraseña nueva no puede ser igual a la actual.",
                                  code="password_repetida")
        base = raiz(password)
        if len(base) < LARGO_MINIMO_RAIZ:
            return
        from .models import HistorialClave

        for raiz_guardada in HistorialClave.objects.filter(usuario=user).values_list(
                "raiz_hash", flat=True)[:self.cantidad]:
            if check_password(base, raiz_guardada):
                raise ValidationError(
                    f"La contraseña nueva es igual o muy parecida a una de las últimas "
                    f"{self.cantidad} contraseñas (por ejemplo, la misma palabra con otro número). "
                    "Elegir una distinta.",
                    code="password_parecida_anterior")

    def password_changed(self, password, user=None):
        """Django lo llama cada vez que se guarda una contraseña nueva."""
        if user is None or not getattr(user, "pk", None):
            return
        from .models import HistorialClave

        HistorialClave.objects.create(usuario=user, raiz_hash=make_password(raiz(password)))
        viejas = HistorialClave.objects.filter(usuario=user).values_list("pk", flat=True)[self.cantidad:]
        HistorialClave.objects.filter(pk__in=list(viejas)).delete()

    def get_help_text(self):
        return f"No puede repetir ni parecerse a las últimas {self.cantidad} contraseñas."
