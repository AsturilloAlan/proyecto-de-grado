"""Comando de mantenimiento para vaciar los datos "de negocio" cargados durante las
pruebas del sistema, sin tocar las cuentas de usuario.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from registros.models import (
    CargaArchivo,
    CuentaContable,
    EmpresaAuditada,
    ErrorValidacion,
    Gestion,
    RegistroContable,
)


class Command(BaseCommand):
    help = (
        "Borra todos los datos de negocio (empresas, gestiones, cargas, "
        "registros contables, cuentas y errores/avisos) cargados durante "
        "las pruebas. No toca usuarios ni roles."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            action="store_true",
            help="Ejecuta el borrado de verdad. Sin este flag solo se muestra un conteo.",
        )

    def handle(self, *args, **options):
        conteos = {
            "Cargas de archivo": CargaArchivo.objects.count(),
            "Registros contables": RegistroContable.objects.count(),
            "Errores/avisos de validación": ErrorValidacion.objects.count(),
            "Empresas auditadas": EmpresaAuditada.objects.count(),
            "Gestiones": Gestion.objects.count(),
            "Cuentas contables": CuentaContable.objects.count(),
        }

        self.stdout.write("Se van a borrar (usuarios y roles NO se tocan):")
        for etiqueta, cantidad in conteos.items():
            self.stdout.write(f"  - {etiqueta}: {cantidad}")

        if not options["confirmar"]:
            self.stdout.write(
                self.style.WARNING(
                    "\nModo simulación: no se borró nada. "
                    "Vuelve a ejecutar con --confirmar para borrar de verdad."
                )
            )
            return

        with transaction.atomic():
            # CargaArchivo primero: arrastra RegistroContable y ErrorValidacion por
            # CASCADE, y libera el PROTECT que impedía borrar EmpresaAuditada/Gestion.
            CargaArchivo.objects.all().delete()
            EmpresaAuditada.objects.all().delete()
            Gestion.objects.all().delete()
            CuentaContable.objects.all().delete()

        self.stdout.write(self.style.SUCCESS("\nListo: datos de negocio borrados."))
