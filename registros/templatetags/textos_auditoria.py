"""Nombres claros para los tipos de aviso y de error (en lugar de las claves internas)."""
from django import template

register = template.Library()

NOMBRES_REGLA = {
    "duplicado": "Posible duplicado",
    "naturaleza_cuenta": "Movimiento contrario a la cuenta",
    "cuenta": "Cuenta nueva en el catálogo",
    "comprobante": "Sin número de comprobante",
    "fecha": "Fecha fuera de lo esperado",
    "debe": "Importe en Debe",
    "haber": "Importe en Haber",
    "debe/haber": "Importe en Debe/Haber",
    "archivo": "Archivo",
}


@register.filter
def nombre_regla(campo):
    return NOMBRES_REGLA.get(campo or "", (campo or "General").replace("_", " ").capitalize())


@register.filter
def partes_aviso(descripcion):
    """Separa la descripción de un aviso en lo principal y el detalle, para leerla de un
    vistazo: "Posible duplicado de la fila 4544: misma fecha (...), cuenta..." →
    ("Posible duplicado de la fila 4544", "Misma fecha (...), cuenta...")."""
    texto = (descripcion or "").strip()
    principal, separador, detalle = texto.partition(": ")
    if not separador or not detalle:
        return texto, ""
    return principal, detalle[:1].upper() + detalle[1:]
