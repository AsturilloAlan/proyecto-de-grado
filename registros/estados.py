"""Filtros de cargas por estado de importación y de revisión (ver CargaArchivo)."""
from django.db.models import Count, Q


def anotar_avisos_pendientes(queryset):
    return queryset.annotate(
        avisos_pendientes_anotado=Count(
            "errores", filter=Q(errores__tipo="aviso", errores__revisado=False)
        )
    )


def filtrar_por_importacion(queryset, valor):
    procesada = ~Q(estado="pendiente")
    if valor == "completa":
        return queryset.filter(procesada, registros_validos__gt=0, registros_con_error=0)
    if valor == "parcial":
        return queryset.filter(procesada, registros_validos__gt=0, registros_con_error__gt=0)
    if valor == "fallida":
        return queryset.filter(procesada, registros_validos=0)
    return queryset


def filtrar_por_revision(queryset, valor):
    """Requiere que el queryset venga de anotar_avisos_pendientes."""
    con_datos = Q(estado="con_observaciones", registros_validos__gt=0)
    if valor == "en_revision":
        return queryset.filter(con_datos, avisos_pendientes_anotado__gt=0)
    if valor == "por_confirmar":
        return queryset.filter(con_datos, avisos_pendientes_anotado=0)
    if valor == "validada":
        return queryset.filter(estado="validado")
    if valor == "anulada":
        return queryset.filter(estado="anulada")
    return queryset
