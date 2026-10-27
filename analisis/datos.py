"""Lectura de cargas como DataFrames para los comandos de análisis."""
import pandas as pd
from django.core.management.base import CommandError

from registros.dataset import COLUMNAS_DATASET, filas_dataset
from registros.models import CargaArchivo


def dataset_de_carga(carga_id):
    """(nombre, DataFrame) de una carga válida; error claro si no sirve."""
    try:
        carga = CargaArchivo.objects.select_related("empresa", "gestion").get(pk=carga_id)
    except CargaArchivo.DoesNotExist:
        raise CommandError(f"No existe ninguna carga con id={carga_id}.")
    if carga.estado == "anulada":
        raise CommandError(f"La carga #{carga_id} está anulada.")
    if carga.estado == "pendiente":
        raise CommandError(f"La carga #{carga_id} todavía se está procesando.")
    df = pd.DataFrame(list(filas_dataset(carga)), columns=COLUMNAS_DATASET)
    if df.empty:
        raise CommandError(f"La carga #{carga_id} no tiene registros guardados.")
    return f"carga_{carga_id}_{carga.gestion.anio}", df
