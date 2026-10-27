"""Procesamiento de las cargas en segundo plano, con una cola de una en una.

Al subir un archivo, la carga entra en cola ("En cola") y la página responde de
inmediato. Al terminar, su análisis con el modelo entra en la misma cola. Un único trabajador, en un hilo aparte, toma las cargas en orden de llegada
y las procesa de una en una ("Procesando"). Así varias personas pueden subir archivos a
la vez sin que el servidor procese varios libros en paralelo. No requiere servicios
adicionales: la cola es la propia tabla de cargas, por lo que sobrevive a un reinicio
del servidor.
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

# Tiempo máximo para detectar una carga interrumpida tras un reinicio o fallo.
MINUTOS_MAXIMOS_PROCESO = 10

# Un solo trabajador por proceso del servidor; el candado evita arrancar dos.
_candado = threading.Lock()
_trabajador_activo = False


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


# Cola

def _en_cola():
    return CargaArchivo.objects.filter(estado="pendiente", inicio_proceso__isnull=True)


def _tomar(carga_id):
    """Marca la carga como tomada. Devuelve False si otro trabajador ya la tomó."""
    return CargaArchivo.objects.filter(
        pk=carga_id, estado="pendiente", inicio_proceso__isnull=True
    ).update(inicio_proceso=timezone.now()) == 1


def _analisis():
    # Importación diferida: la app analisis depende de registros.
    from analisis import servicio
    return servicio


def _hay_trabajo():
    return _en_cola().exists() or _analisis().siguiente_en_cola() is not None


def procesar_cola():
    """Procesa la cola de una en una: primero las cargas, en orden de llegada, y luego los
    análisis del modelo. Devuelve cuántas tareas procesó.
    """
    servicio = _analisis()
    procesadas = 0
    while True:
        carga_id = _en_cola().order_by("fecha_carga", "id").values_list("id", flat=True).first()
        if carga_id is not None:
            if not _tomar(carga_id):
                continue
            carga = CargaArchivo.objects.get(pk=carga_id)
            procesar_con_manejo_de_errores(carga)
            if servicio.analisis_activado():
                carga.refresh_from_db()
                servicio.encolar(carga)
            procesadas += 1
            continue
        analisis_id = servicio.siguiente_en_cola()
        if analisis_id is None:
            return procesadas
        if not servicio.tomar(analisis_id):
            continue
        servicio.ejecutar(servicio.AnalisisCarga.objects.select_related("carga").get(pk=analisis_id))
        procesadas += 1


def _bucle_trabajador():
    global _trabajador_activo
    while True:
        try:
            procesar_cola()
        except Exception:
            # La próxima consulta de estado reinicia el trabajador; la carga permanece en cola.
            logger.critical("El trabajador de la cola de cargas se detuvo", exc_info=True)
            with _candado:
                _trabajador_activo = False
            connections.close_all()
            return
        # El candado evita dejar sin trabajador una carga que llega al vaciarse la cola.
        with _candado:
            try:
                hay_mas = _hay_trabajo()
            except Exception:
                hay_mas = False
            if not hay_mas:
                _trabajador_activo = False
                connections.close_all()
                return


def asegurar_trabajador():
    """Arranca el trabajador de la cola si no está activo. Devuelve True si lo arrancó."""
    global _trabajador_activo
    with _candado:
        if _trabajador_activo:
            return False
        _trabajador_activo = True
    try:
        threading.Thread(target=_bucle_trabajador, name="cola-cargas", daemon=True).start()
    except Exception:
        with _candado:
            _trabajador_activo = False
        raise
    return True


def iniciar_procesamiento(carga):
    """Pone la carga en la cola, o la procesa en el momento si así está configurado
    (las pruebas automáticas lo necesitan para verificar el resultado).
    """
    if not getattr(settings, "PROCESAR_EN_SEGUNDO_PLANO", True):
        if _tomar(carga.id):
            carga.refresh_from_db(fields=["inicio_proceso"])
            procesar_con_manejo_de_errores(carga)
            servicio = _analisis()
            if servicio.analisis_activado():
                carga.refresh_from_db()
                servicio.analizar_ahora(carga)
        return
    transaction.on_commit(asegurar_trabajador)


def posicion_en_cola(carga):
    """Cuántas cargas se procesan antes que esta, contando la actual (0 si ya empezó)."""
    if carga.estado != "pendiente" or carga.inicio_proceso is not None:
        return 0
    antes = _en_cola().filter(fecha_carga__lt=carga.fecha_carga).count()
    empatadas = _en_cola().filter(fecha_carga=carga.fecha_carga, id__lt=carga.id).count()
    procesando = CargaArchivo.objects.filter(
        estado="pendiente", inicio_proceso__isnull=False
    ).exists()
    return antes + empatadas + int(procesando)


def revisar_interrumpida(carga):
    """Si la carga quedó "Procesando" demasiado tiempo, la marca como fallida. Si está
    en cola, se asegura de que el trabajador esté activo (p. ej., tras un reinicio).
    """
    if carga.estado != "pendiente":
        return False
    if carga.inicio_proceso is None:
        if getattr(settings, "PROCESAR_EN_SEGUNDO_PLANO", True):
            asegurar_trabajador()
        return False
    limite = timezone.now() - timedelta(minutes=MINUTOS_MAXIMOS_PROCESO)
    if carga.inicio_proceso < limite:
        return _marcar_fallida(
            carga,
            "El procesamiento se interrumpió antes de terminar (por ejemplo, por un "
            "reinicio del servidor). Corresponde anular la carga y subir el archivo de nuevo.",
        )
    return False
