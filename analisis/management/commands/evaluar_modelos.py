"""Evaluación comparativa de Isolation Forest, LOF y One-Class SVM (CRISP-DM: Evaluación).

Uso:
    python manage.py evaluar_modelos 3 4
    python manage.py evaluar_modelos --csv dataset_carga_3.csv --repeticiones 5
"""
from datetime import datetime

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from analisis.evaluacion import evaluar
from analisis.informe_evaluacion import generar_informe
from registros.dataset import COLUMNAS_DATASET, filas_dataset
from registros.models import CargaArchivo


class Command(BaseCommand):
    help = "Compara los tres algoritmos candidatos sobre una o más cargas y genera un informe."

    def add_arguments(self, parser):
        parser.add_argument("cargas", nargs="*", type=int, help="ID de las cargas a evaluar.")
        parser.add_argument("--csv", nargs="*", default=[], help="Datasets CSV exportados.")
        parser.add_argument("--repeticiones", type=int, default=3)
        parser.add_argument("--contaminacion", type=float, default=0.05)
        parser.add_argument("--proporcion", type=float, default=0.002,
                            help="Anomalías inyectadas por tipo, como proporción de los registros.")
        parser.add_argument("--salida", type=str, default=None)

    def _datasets(self, options):
        for carga_id in options["cargas"]:
            try:
                carga = CargaArchivo.objects.select_related("empresa", "gestion").get(pk=carga_id)
            except CargaArchivo.DoesNotExist:
                raise CommandError(f"No existe ninguna carga con id={carga_id}.")
            if carga.estado == "anulada":
                raise CommandError(f"La carga #{carga_id} está anulada.")
            df = pd.DataFrame(list(filas_dataset(carga)), columns=COLUMNAS_DATASET)
            if df.empty:
                raise CommandError(f"La carga #{carga_id} no tiene registros guardados.")
            yield f"carga_{carga_id}_{carga.gestion.anio}", df
        for ruta in options["csv"]:
            df = pd.read_csv(ruta, encoding="utf-8-sig")
            yield ruta.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".csv", ""), df

    def handle(self, *args, **options):
        if not options["cargas"] and not options["csv"]:
            raise CommandError("Indica al menos una carga o un archivo con --csv.")
        if not 0 < options["contaminacion"] < 0.5:
            raise CommandError("La contaminación debe estar entre 0 y 0.5.")

        resumenes, tipos, puntajes, nombres = [], [], {}, []
        for nombre, df in self._datasets(options):
            for columna in ("debe", "haber"):
                df[columna] = pd.to_numeric(df[columna], errors="coerce").fillna(0.0)
            self.stdout.write(f"Evaluando {nombre} ({len(df)} registros)...")
            resumen, por_tipo, ultima = evaluar(
                df, nombre, repeticiones=options["repeticiones"],
                contaminacion=options["contaminacion"],
                proporcion_por_tipo=options["proporcion"],
            )
            resumenes.append(resumen)
            tipos.append(por_tipo)
            puntajes[nombre] = ultima
            nombres.append(nombre)

        carpeta = options["salida"] or f"evaluacion_modelos_{datetime.now():%Y%m%d_%H%M}"
        informe, elegido = generar_informe(
            carpeta, pd.concat(resumenes, ignore_index=True), pd.concat(tipos, ignore_index=True),
            puntajes, {
                "datasets": nombres, "repeticiones": options["repeticiones"],
                "contaminacion": options["contaminacion"],
                "proporcion_por_tipo": options["proporcion"],
            },
        )
        self.stdout.write(self.style.SUCCESS(
            f"Listo. Mejor desempeño: {elegido}. Informe en {informe}"))
