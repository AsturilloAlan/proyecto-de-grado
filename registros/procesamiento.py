"""Procesamiento de una carga en segundo plano.

El archivo se procesa en un hilo aparte para que la página responda de inmediato y
muestre la carga "En proceso". No requiere servicios adicionales: con libros de hasta
unos 24.000 registros por gestión basta un hilo por carga.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from .models import CargaArchivo, ErrorValidacion
from .services import procesar_carga

logger = logging.getLogger(__name__)

# Pasado este tiempo "En proceso", la carga se considera interrumpida (p. ej., reinicio
# del servidor). 24.000 registros tardan menos de un minuto.
MINUTOS_MAXIMOS_PROCESO = 10


def _marcar_fallida(carga, descripcion):
    """Pasa la carga a "con errores" solo si sigue en proceso (nunca pisa una anulación
    ni duplica el mensaje si dos consultas llegan a la vez).
    """
    with transaction.atomic():
        actualizadas = CargaArchivo.objects.filter(pk=carga.pk, estado="pendiente").update(
            estado="con_errores"
        )
        if actualizadas:
            ErrorValidacion.objects.create(carga=carga, fila=0, campo="archivo", descripcion=descripcion)
    carga.refresh_from_db()
    return bool(actualizadas)


def procesar_con_manejo_de_errores(carga):
    try:
        procesar_carga(carga)
    except Exception:
        # procesar_carga no deja datos a medias; solo se marca la carga como fallida.
        logger.exception("Fallo al procesar la carga #%s", carga.id)
        try:
            _marcar_fallida(
                carga,
                "Ocurrió un error inesperado al procesar el archivo y no se guardó ninguna "
                "fila. Corresponde anular la carga y verificar el formato del archivo.",
            )
        except Exception:
            # Sin base de datos: queda en sistema.log y la carga se detecta como interrumpida.
            logger.critical("No se pudo marcar como fallida la carga #%s", carga.id, exc_info=True)


def _trabajo(carga_id):
    try:
        procesar_con_manejo_de_errores(CargaArchivo.objects.get(pk=carga_id))
    except Exception:
        logger.critical("No se pudo iniciar el proceso de la carga #%s", carga_id, exc_info=True)
    finally:
        # Cada hilo abre su propia conexión a la base de datos; se cierra al terminar.
        connections.close_all()


def iniciar_procesamiento(carga):
    """Procesa la carga en segundo plano, o en el momento si así está configurado
    (las pruebas automáticas lo necesitan para verificar el resultado).
    """
    if not getattr(settings, "PROCESAR_EN_SEGUNDO_PLANO", True):
        procesar_con_manejo_de_errores(carga)
        return
    transaction.on_commit(
        lambda: threading.Thread(
            target=_trabajo, args=(carga.id,), name=f"carga-{carga.id}", daemon=True
        ).start()
    )


def revisar_interrumpida(carga):
    """Si la carga quedó "En proceso" demasiado tiempo, la marca como fallida."""
    limite = timezone.now() - timedelta(minutes=MINUTOS_MAXIMOS_PROCESO)
    if carga.estado == "pendiente" and carga.fecha_carga < limite:
        return _marcar_fallida(
            carga,
            "El procesamiento se interrumpió antes de terminar (por ejemplo, por un "
            "reinicio del servidor). Corresponde anular la carga y subir el archivo de nuevo.",
        )
    return False
