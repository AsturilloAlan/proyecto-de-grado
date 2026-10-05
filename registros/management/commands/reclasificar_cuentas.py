"""Corrige el tipo de las cuentas según el primer dígito del código (1 activo ... 5 gasto).

Uso:
    python manage.py reclasificar_cuentas                                   # solo muestra
    python manage.py reclasificar_cuentas --confirmar --usuario SOCIO_PRINCIPAL

Cada cambio queda en el historial de cambios a nombre del usuario indicado. Los avisos de
naturaleza de cuenta de cargas ya procesadas no se recalculan: hay que volver a subirlas.
"""
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from registros.models import CuentaContable, HistorialCambio
from registros.services import GRUPO_CONTABLE_POR_PRIMER_DIGITO


class Command(BaseCommand):
    help = "Reclasifica el tipo de las cuentas contables según su código."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true")
        parser.add_argument("--usuario", help="Usuario a cuyo nombre queda el cambio en el historial.")

    def handle(self, *args, **options):
        usuario = None
        if options["confirmar"]:
            if not options["usuario"]:
                raise CommandError("Con --confirmar se debe indicar --usuario (queda en el historial).")
            try:
                usuario = get_user_model().objects.get(username=options["usuario"])
            except get_user_model().DoesNotExist:
                raise CommandError(f"No existe el usuario '{options['usuario']}'.")

        cambios = []
        sin_grupo = 0
        for cuenta in CuentaContable.objects.select_related("empresa"):
            tipo = GRUPO_CONTABLE_POR_PRIMER_DIGITO.get(str(cuenta.codigo).strip()[:1])
            if tipo is None:
                sin_grupo += 1
            elif tipo != cuenta.tipo:
                cambios.append((cuenta, cuenta.tipo, tipo))

        resumen = Counter(f"{anterior} -> {nuevo}" for _, anterior, nuevo in cambios)
        self.stdout.write(f"Cuentas a reclasificar: {len(cambios)}")
        for cambio, n in resumen.most_common():
            self.stdout.write(f"  {cambio}: {n}")
        if sin_grupo:
            self.stdout.write(f"Cuentas sin grupo reconocible (no se tocan): {sin_grupo}")

        if not options["confirmar"]:
            self.stdout.write("Modo simulación: no se guardó nada. Ejecutar con --confirmar --usuario <usuario>.")
            return
        with transaction.atomic():
            for cuenta, _, nuevo in cambios:
                cuenta.tipo = nuevo
            CuentaContable.objects.bulk_update([c for c, _, _ in cambios], ["tipo"])
            HistorialCambio.objects.bulk_create([
                HistorialCambio(
                    modelo="CuentaContable", objeto_id=cuenta.pk,
                    objeto_descripcion=f"{cuenta} ({cuenta.empresa or 'sin empresa'})"[:200],
                    accion="edicion", campo="tipo",
                    valor_anterior=anterior, valor_nuevo=nuevo, usuario=usuario,
                )
                for cuenta, anterior, nuevo in cambios
            ])
        self.stdout.write(self.style.SUCCESS(
            f"Listo: {len(cambios)} cuentas reclasificadas (registradas en el historial). "
            "Las cargas ya procesadas conservan sus avisos anteriores."
        ))
