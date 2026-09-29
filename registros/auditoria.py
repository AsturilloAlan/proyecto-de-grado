"""Registro de la pista de auditoría (HistorialCambio) sobre modificaciones a
información sensible.
"""
from .models import HistorialCambio


def registrar_creacion(instancia, usuario):
    """Deja constancia de que se creó un objeto nuevo."""
    HistorialCambio.objects.create(
        modelo=instancia.__class__.__name__,
        objeto_id=instancia.pk,
        objeto_descripcion=str(instancia),
        accion="creacion",
        usuario=usuario,
    )


def registrar_edicion(instancia, valores_anteriores, usuario, campos):
    """Compara valores_anteriores (capturados antes de guardar) contra los valores
    actuales de la instancia (ya guardada) y crea una fila de HistorialCambio por
    cada campo que realmente cambió.
    """
    cambios = []
    for campo in campos:
        valor_anterior = valores_anteriores.get(campo, "")
        valor_nuevo = getattr(instancia, campo)
        if str(valor_anterior) != str(valor_nuevo):
            cambios.append(
                HistorialCambio(
                    modelo=instancia.__class__.__name__,
                    objeto_id=instancia.pk,
                    objeto_descripcion=str(instancia),
                    accion="edicion",
                    campo=campo,
                    valor_anterior=str(valor_anterior),
                    valor_nuevo=str(valor_nuevo),
                    usuario=usuario,
                )
            )
    if cambios:
        HistorialCambio.objects.bulk_create(cambios)
    return cambios
