"""Vacía los datos de negocio cargados durante las pruebas, sin tocar usuarios ni roles.

    python manage.py borrar_datos_prueba                          # solo muestra conteos
    python manage.py borrar_datos_prueba --confirmar               # borra todo
    python manage.py borrar_datos_prueba --confirmar --conservar-empresas --archivos
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from registros.models import (
    CargaArchivo,
    CuentaContable,
    EmpresaAuditada,
    ErrorValidacion,
    Gestion,
    HistorialCambio,
    RegistroContable,
)

MODELOS_DE_NEGOCIO = ["CargaArchivo", "ErrorValidacion", "CuentaContable", "EmpresaAuditada", "Gestion"]


class Command(BaseCommand):
    help = "Borra los datos de negocio de prueba (cargas, registros, avisos, cuentas). No toca usuarios."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true", help="Borra de verdad; sin esto solo muestra conteos.")
        parser.add_argument("--conservar-empresas", action="store_true", help="Mantiene empresas y gestiones.")
        parser.add_argument("--archivos", action="store_true", help="Borra también los archivos subidos de las cargas.")

    def handle(self, *args, **options):
        conservar = options["conservar_empresas"]
        modelos_historial = [m for m in MODELOS_DE_NEGOCIO if not (conservar and m in ("EmpresaAuditada", "Gestion"))]
        conteos = {
            "Cargas de archivo": CargaArchivo.objects.count(),
            "Registros contables": RegistroContable.objects.count(),
            "Errores y avisos": ErrorValidacion.objects.count(),
            "Cuentas contables": CuentaContable.objects.count(),
            "Historial de esos datos": HistorialCambio.objects.filter(modelo__in=modelos_historial).count(),
        }
        if not conservar:
            conteos["Empresas auditadas"] = EmpresaAuditada.objects.count()
            conteos["Gestiones"] = Gestion.objects.count()

        self.stdout.write("Se borrarán (usuarios y roles no se tocan):")
        for etiqueta, cantidad in conteos.items():
            self.stdout.write(f"  - {etiqueta}: {cantidad}")
        if options["archivos"]:
            self.stdout.write("  - Archivos subidos de las cargas")

        if not options["confirmar"]:
            self.stdout.write(self.style.WARNING("\nModo simulación: no se borró nada. Ejecutar con --confirmar."))
            return

        archivos = [c.archivo for c in CargaArchivo.objects.all()] if options["archivos"] else []
        with transaction.atomic():
            # Las cargas arrastran registros y avisos; las cuentas van antes que las empresas (PROTECT).
            CargaArchivo.objects.all().delete()
            CuentaContable.objects.all().delete()
            if not conservar:
                EmpresaAuditada.objects.all().delete()
                Gestion.objects.all().delete()
            HistorialCambio.objects.filter(modelo__in=modelos_historial).delete()

        borrados = 0
        for archivo in archivos:
            if archivo and archivo.storage.exists(archivo.name):
                archivo.storage.delete(archivo.name)
                borrados += 1
        mensaje = "\nListo: datos de prueba borrados."
        if options["archivos"]:
            mensaje += f" Archivos borrados: {borrados}."
        self.stdout.write(self.style.SUCCESS(mensaje))
