"""Aplica la regla actual de "posible duplicado" a cargas ya procesadas.

La regla ahora exige que también coincida la glosa. Las cargas subidas antes conservan
los avisos de la regla anterior; este comando quita los avisos de duplicado que la regla
actual ya no generaría, solo si siguen pendientes (los que el auditor ya revisó no se
tocan). No modifica registros contables.

Uso:
    python manage.py recalcular_duplicados             # muestra cuántos se quitarían
    python manage.py recalcular_duplicados --confirmar # los quita
    python manage.py recalcular_duplicados --carga 49 --confirmar
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from registros.models import CargaArchivo, ErrorValidacion
from registros.services import _concepto_de_glosa


def filas_duplicadas(carga):
    """Filas que la regla actual marca como posible duplicado."""
    vistas, duplicadas = set(), set()
    for r in carga.registros.select_related("cuenta").order_by("fila_origen", "id"):
        comprobante = str(r.numero_comprobante or "").strip()
        if not comprobante:
            continue
        clave = (comprobante, r.fecha, r.cuenta.codigo, round(r.debe, 2), round(r.haber, 2),
                 _concepto_de_glosa(r.glosa))
        if clave in vistas:
            duplicadas.add(r.fila_origen)
        else:
            vistas.add(clave)
    return duplicadas


class Command(BaseCommand):
    help = "Quita los avisos de duplicado pendientes que la regla actual ya no generaría."

    def add_arguments(self, parser):
        parser.add_argument("--carga", type=int, default=None)
        parser.add_argument("--confirmar", action="store_true")

    def handle(self, *args, **options):
        cargas = CargaArchivo.objects.exclude(estado__in=["pendiente", "anulada"])
        if options["carga"]:
            cargas = cargas.filter(pk=options["carga"])
        total = 0
        for carga in cargas.select_related("empresa", "gestion"):
            vigentes = filas_duplicadas(carga)
            sobran = ErrorValidacion.objects.filter(
                carga=carga, tipo="aviso", campo="duplicado", revisado=False,
            ).exclude(fila__in=vigentes)
            cantidad = sobran.count()
            antes = carga.errores.filter(campo="duplicado").count()
            self.stdout.write(f"Carga N.º {carga.id} ({carga.empresa}, {carga.gestion.anio}): "
                              f"{antes} avisos de duplicado, se quitarían {cantidad}")
            total += cantidad
            if options["confirmar"] and cantidad:
                with transaction.atomic():
                    sobran.delete()
                    carga.registros_con_aviso = carga.errores.filter(tipo="aviso").count()
                    carga.save(update_fields=["registros_con_aviso"])
        if not options["confirmar"]:
            self.stdout.write(f"Modo simulación: se quitarían {total} avisos. Ejecutar con --confirmar.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Se quitaron {total} avisos de duplicado pendientes."))
