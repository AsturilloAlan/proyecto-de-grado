"""Actividad 11: fija One-Class SVM y elige su configuración (nu y gamma).

Uso:
    python manage.py configurar_modelo 45 44
    python manage.py configurar_modelo 45 44 --repeticiones 3
"""
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from analisis.datos import dataset_de_carga
from analisis.modelo import (CONFIGURACION_BASE, VALORES_GAMMA, VALORES_NU, _etiqueta,
                             configurar, guardar_configuracion)
from analisis.powerbi import exportar_configuracion


class Command(BaseCommand):
    help = "Prueba configuraciones de One-Class SVM con casos conocidos y guarda la mejor."

    def add_arguments(self, parser):
        parser.add_argument("cargas", nargs="+", type=int, help="ID de las cargas (2022 y 2023).")
        parser.add_argument("--repeticiones", type=int, default=2)
        parser.add_argument("--proporcion", type=float, default=0.002,
                            help="Anomalías inyectadas por tipo, como proporción de los registros.")

    def handle(self, *args, **options):
        if options["repeticiones"] < 1:
            raise CommandError("Las repeticiones deben ser al menos 1.")
        datasets = [dataset_de_carga(carga_id) for carga_id in options["cargas"]]
        total = len(VALORES_NU) * len(VALORES_GAMMA)
        self.stdout.write(f"Probando {total} configuraciones de One-Class SVM en "
                          f"{len(datasets)} cargas, {options['repeticiones']} repeticiones cada una...")

        resultados, ranking, elegida = configurar(
            datasets, repeticiones=options["repeticiones"],
            proporcion_por_tipo=options["proporcion"])

        carpeta = Path(f"configuracion_modelo_{datetime.now():%Y%m%d_%H%M}")
        carpeta.mkdir(exist_ok=True)
        resultados.to_csv(carpeta / "resultados_por_corrida.csv", index=False)
        ranking.to_csv(carpeta / "ranking_configuraciones.csv", index=False)
        exportar_configuracion(carpeta / "datos_powerbi.xlsx", ranking, CONFIGURACION_BASE)
        nombres = [nombre for nombre, _ in datasets]
        ruta = guardar_configuracion(elegida, ranking, nombres)

        self.stdout.write("\nRanking (promedio):")
        for posicion, fila in ranking.iterrows():
            marca = "  <- base" if fila["configuracion"] == _etiqueta(CONFIGURACION_BASE) else ""
            self.stdout.write(
                f"  {posicion + 1}. {fila['configuracion']:<22} AUC-PR {fila['auc_pr']:.3f}  "
                f"F1 {fila['f1']:.3f}  {fila['segundos']:.1f} s{marca}")
        self.stdout.write(self.style.SUCCESS(
            f"\nConfiguración elegida: {_etiqueta(elegida)}. Guardada en {ruta}. "
            f"Detalle en {carpeta}"))
