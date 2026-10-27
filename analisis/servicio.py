"""Integración del modelo con el flujo de carga (actividad 14).

Cuando una carga termina de procesarse, el análisis entra en la misma cola de una en
una (registros/procesamiento.py). El trabajador aplica el modelo guardado por
entrenar_modelo, sin reentrenar, y guarda el puntaje de cada registro.
"""
import json
import logging
import time
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from registros.dataset import COLUMNAS_DATASET, filas_dataset

from . import explicacion, modelo
from .models import AnalisisCarga, ResultadoTransaccion

logger = logging.getLogger(__name__)

MINUTOS_MAXIMOS_ANALISIS = 10
TAMANO_LOTE = 2000

# El modelo se lee del disco una sola vez y se vuelve a leer si se reentrena.
_modelo_en_memoria = {"marca": None, "paquete": None}


def analisis_activado():
    return getattr(settings, "ANALIZAR_CON_MODELO", True)


def _paquete():
    ruta = modelo.carpeta_modelos() / modelo.ARCHIVO_MODELO
    marca = ruta.stat().st_mtime if ruta.exists() else None
    if marca is None or _modelo_en_memoria["marca"] != marca:
        _modelo_en_memoria["paquete"] = modelo.cargar()
        _modelo_en_memoria["marca"] = marca
    return _modelo_en_memoria["paquete"]


def se_puede_analizar(carga):
    return carga.estado not in ("pendiente", "anulada") and carga.registros_validos > 0


def encolar(carga, usuario=None):
    """Pone el análisis de la carga en la cola. Devuelve el AnalisisCarga o None."""
    if not se_puede_analizar(carga):
        return None
    analisis, _ = AnalisisCarga.objects.update_or_create(
        carga=carga,
        defaults={"estado": "en_cola", "mensaje": "", "inicio": None, "fecha_fin": None,
                  "solicitado_por": usuario},
    )
    return analisis


def siguiente_en_cola():
    return (AnalisisCarga.objects.filter(estado="en_cola")
            .order_by("fecha_solicitud", "id").values_list("id", flat=True).first())


def tomar(analisis_id):
    return AnalisisCarga.objects.filter(pk=analisis_id, estado="en_cola").update(
        estado="procesando", inicio=timezone.now()) == 1


def _terminar(analisis, estado, mensaje="", **campos):
    analisis.estado = estado
    analisis.mensaje = mensaje
    analisis.fecha_fin = timezone.now()
    for nombre, valor in campos.items():
        setattr(analisis, nombre, valor)
    analisis.save()
    return analisis


def ejecutar(analisis):
    """Aplica el modelo a la carga del análisis y guarda los resultados."""
    carga = analisis.carga
    if not se_puede_analizar(carga):
        return _terminar(analisis, "error", "La carga ya no tiene registros válidos para analizar.")
    try:
        paquete = _paquete()
    except FileNotFoundError:
        return _terminar(
            analisis, "sin_modelo",
            "Todavía no hay un modelo entrenado. Ejecutar: python manage.py entrenar_modelo")
    try:
        modelo.verificar_columnas(paquete)
    except modelo.ModeloDesactualizado as error:
        return _terminar(analisis, "sin_modelo", str(error))

    try:
        inicio = time.perf_counter()
        df = pd.DataFrame(list(filas_dataset(carga)), columns=COLUMNAS_DATASET)
        ids = list(carga.registros.order_by("fila_origen", "id").values_list("id", flat=True))
        resultado = modelo.puntuar(df, paquete)
        motivos = explicacion.explicar(df, paquete, resultado["atipica"].to_numpy() == 1)
        nuevos = [
            ResultadoTransaccion(
                analisis=analisis, registro_id=registro_id, puntaje=float(fila.puntaje),
                nivel=float(fila.nivel), atipica=bool(fila.atipica),
                motivos=json.dumps(motivos[posicion], ensure_ascii=False) if posicion in motivos else "",
                motivo_principal=motivos[posicion][0]["variable"] if motivos.get(posicion) else "",
            )
            for posicion, (registro_id, fila) in enumerate(zip(ids, resultado.itertuples(index=False)))
        ]
        with transaction.atomic():
            # Al volver a analizar se conserva la revisión que el auditor ya hizo.
            revisiones = {
                fila["registro_id"]: fila
                for fila in ResultadoTransaccion.objects.filter(registro__carga=carga)
                .exclude(revision="pendiente")
                .values("registro_id", "revision", "comentario_revision",
                        "revisado_por_id", "fecha_revision")
            }
            for nuevo in nuevos:
                previa = revisiones.get(nuevo.registro_id)
                if previa:
                    nuevo.revision = previa["revision"]
                    nuevo.comentario_revision = previa["comentario_revision"]
                    nuevo.revisado_por_id = previa["revisado_por_id"]
                    nuevo.fecha_revision = previa["fecha_revision"]
            ResultadoTransaccion.objects.filter(registro__carga=carga).delete()
            ResultadoTransaccion.objects.bulk_create(nuevos, batch_size=TAMANO_LOTE)
        meta = paquete["metadatos"]
        configuracion = meta.get("configuracion", {})
        return _terminar(
            analisis, "completado",
            algoritmo=meta.get("algoritmo", modelo.ALGORITMO),
            configuracion=f"nu={configuracion.get('nu')}, gamma={configuracion.get('gamma')}",
            fecha_modelo=meta.get("fecha", ""),
            total_registros=len(nuevos),
            total_atipicas=sum(1 for r in nuevos if r.atipica),
            segundos=round(time.perf_counter() - inicio, 2),
        )
    except Exception:
        logger.exception("Fallo al analizar la carga #%s", carga.id)
        return _terminar(analisis, "error",
                         "Ocurrió un error inesperado al aplicar el modelo. Volver a intentarlo.")


def analizar_ahora(carga, usuario=None):
    """Análisis inmediato (sin hilo): lo usan las pruebas y el procesamiento síncrono."""
    analisis = encolar(carga, usuario)
    if analisis is None or not tomar(analisis.id):
        return analisis
    analisis.refresh_from_db()
    return ejecutar(analisis)


def revisar_interrumpido(analisis):
    """Si el análisis quedó "Analizando" demasiado tiempo, lo marca como error."""
    limite = timezone.now() - timedelta(minutes=MINUTOS_MAXIMOS_ANALISIS)
    if analisis.estado == "procesando" and analisis.inicio and analisis.inicio < limite:
        actualizados = AnalisisCarga.objects.filter(pk=analisis.pk, estado="procesando").update(
            estado="error", fecha_fin=timezone.now(),
            mensaje="El análisis se interrumpió (por ejemplo, por un reinicio). Volver a analizar.")
        analisis.refresh_from_db()
        return bool(actualizados)
    return False
