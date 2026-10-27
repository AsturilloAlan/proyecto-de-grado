"""Actividad 12: entrena One-Class SVM con las cargas oficiales y lo guarda.

Uso:
    python manage.py entrenar_modelo 45 44          # configuración elegida en configurar_modelo
    python manage.py entrenar_modelo 45 44 --base   # configuración base (nu=0.05, gamma=scale)
    python manage.py entrenar_modelo 45 44 --porcentaje 1   # marcar solo el 1 % más atípico
    python manage.py entrenar_modelo 48 49 --prueba 20      # 80 % entrenamiento, 20 % prueba (por defecto)
"""
from django.core.management.base import BaseCommand, CommandError

from analisis.datos import dataset_de_carga
from analisis.modelo import (CONFIGURACION_BASE, PORCENTAJE_ATIPICAS, PROPORCION_PRUEBA,
                             _etiqueta, entrenar, guardar, leer_configuracion)


class Command(BaseCommand):
    help = "Entrena el modelo final (One-Class SVM) con las cargas indicadas y lo guarda."

    def add_arguments(self, parser):
        parser.add_argument("cargas", nargs="+", type=int, help="ID de las cargas oficiales.")
        parser.add_argument("--base", action="store_true",
                            help="Usar la configuración base aunque ya exista una elegida.")
        parser.add_argument("--porcentaje", type=float, default=PORCENTAJE_ATIPICAS,
                            help="Porcentaje de transacciones que se marcan como atípicas.")
        parser.add_argument("--prueba", type=float, default=PROPORCION_PRUEBA * 100,
                            help="Porcentaje de cada carga que se reserva para la prueba (0 a 50).")

    def handle(self, *args, **options):
        if options["base"]:
            configuracion, elegida = dict(CONFIGURACION_BASE), True
        else:
            configuracion, elegida = leer_configuracion()
        if not elegida:
            self.stdout.write(self.style.WARNING(
                "Aún no se ejecutó configurar_modelo: se usa la configuración base."))
        datasets = [dataset_de_carga(carga_id) for carga_id in options["cargas"]]
        self.stdout.write(f"Entrenando One-Class SVM ({_etiqueta(configuracion)}) con "
                          f"{sum(len(df) for _, df in datasets)} registros...")

        if not 0 <= options["prueba"] <= 50:
            raise CommandError("--prueba debe estar entre 0 y 50.")
        paquete = entrenar(datasets, configuracion, options["porcentaje"],
                           proporcion_prueba=options["prueba"] / 100)
        ruta = guardar(paquete)
        meta = paquete["metadatos"]
        self.stdout.write(
            f"  Registros de entrenamiento: {meta['registros_entrenamiento']} "
            f"(excluidos por estar observados: {meta['excluidos_observados']})\n"
            f"  Vectores de soporte: {meta['vectores_soporte']}\n"
            f"  Fuera de la frontera del SVM: {meta['marcadas_entrenamiento']} "
            f"({meta['marcadas_entrenamiento'] / meta['registros_entrenamiento']:.1%})\n"
            f"  Se marcarán como atípicas: el {meta['porcentaje_atipicas']:g} % con mayor puntaje\n"
            f"  Tiempo: {meta['segundos']} s")
        prueba = meta.get("prueba")
        if prueba:
            m = prueba["metricas"]
            self.stdout.write(
                f"\nPrueba ({meta['proporcion_prueba']:.0%} de cada carga, no usada para entrenar):\n"
                f"  Registros normales: {prueba['registros_normales']} · casos atípicos conocidos: {prueba['casos_conocidos']}\n"
                f"  AUC-PR: {m['auc_pr']}  AUC-ROC: {m['auc_roc']}  Precisión en k: {m['precision_en_k']}\n"
                f"  Con el umbral del {meta['porcentaje_atipicas']:g} %: precisión {m['precision']}, "
                f"recall {m['recall']}, F1 {m['f1']}\n"
                f"  Falsas alarmas sobre registros normales: {prueba['falsas_alarmas']:.2%}\n"
                "  Recall por tipo: " + ", ".join(f"{t} {v:.0%}" for t, v in prueba["recall_por_tipo"].items()))
        self.stdout.write(self.style.SUCCESS(f"Modelo guardado en {ruta}"))
