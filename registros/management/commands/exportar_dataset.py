"""Exporta a CSV los registros ya validados de una carga, para usarlos como dataset
de entrada en el prototipo de los algoritmos de detección de anomalías (Isolation
Forest, LOF, One-Class SVM).
"""
import csv

from django.core.management.base import BaseCommand, CommandError

from registros.models import CargaArchivo


class Command(BaseCommand):
    help = "Exporta a CSV los registros contables ya guardados de una carga (dataset limpio)."

    def add_arguments(self, parser):
        parser.add_argument("carga_id", type=int, help="ID de la carga a exportar.")
        parser.add_argument(
            "--salida",
            type=str,
            default=None,
            help="Ruta del archivo CSV de salida (por defecto: dataset_carga_<id>.csv).",
        )

    def handle(self, *args, **options):
        carga_id = options["carga_id"]
        try:
            carga = CargaArchivo.objects.select_related("empresa", "gestion").get(pk=carga_id)
        except CargaArchivo.DoesNotExist:
            raise CommandError(f"No existe ninguna carga con id={carga_id}.")

        if carga.estado == "anulada":
            self.stdout.write(
                self.style.WARNING(
                    f"Aviso: la carga #{carga.id} está anulada. Sus registros ya no se "
                    "consideran válidos; revisa si es la carga correcta antes de usar este dataset."
                )
            )

        registros = (
            carga.registros.select_related("cuenta")
            .order_by("fila_origen")
        )
        total = registros.count()
        if total == 0:
            raise CommandError(f"La carga #{carga.id} no tiene registros guardados para exportar.")

        salida = options["salida"] or f"dataset_carga_{carga.id}.csv"

        with open(salida, "w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(
                ["fila_origen", "fecha", "cuenta_codigo", "cuenta_nombre", "cuenta_tipo",
                 "comprobante", "glosa", "debe", "haber", "saldo"]
            )
            for registro in registros.iterator():
                escritor.writerow(
                    [
                        registro.fila_origen,
                        registro.fecha.isoformat(),
                        registro.cuenta.codigo,
                        registro.cuenta.nombre,
                        registro.cuenta.tipo,
                        registro.numero_comprobante,
                        registro.glosa,
                        registro.debe,
                        registro.haber,
                        registro.saldo if registro.saldo is not None else "",
                    ]
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Listo: {total} registros de la carga #{carga.id} "
                f"({carga.empresa} - {carga.gestion}) exportados a {salida}."
            )
        )
