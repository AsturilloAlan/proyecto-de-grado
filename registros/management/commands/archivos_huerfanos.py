"""Archivos en media/cargas que no pertenecen a ninguna carga del sistema.

Suelen quedar por pruebas antiguas o por cargas interrumpidas. Nunca se borran: con
--confirmar se mueven a una carpeta de cuarentena dentro de media/, para revisarlos y
eliminarlos a mano si corresponde.

Uso:
    python manage.py archivos_huerfanos             # solo muestra cuáles son
    python manage.py archivos_huerfanos --confirmar # los mueve a media/_huerfanos_AAAAMMDD
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from registros.models import CargaArchivo


class Command(BaseCommand):
    help = "Lista (y opcionalmente aparta) los archivos de cargas que no usa ninguna carga."

    def add_arguments(self, parser):
        parser.add_argument("--confirmar", action="store_true",
                            help="Mover los archivos huérfanos a una carpeta de cuarentena.")

    def handle(self, *args, **options):
        raiz = Path(settings.MEDIA_ROOT)
        carpeta = raiz / "cargas"
        if not carpeta.exists():
            self.stdout.write("No existe la carpeta de cargas.")
            return
        en_uso = {Path(nombre).as_posix() for nombre in
                  CargaArchivo.objects.exclude(archivo="").values_list("archivo", flat=True)}
        huerfanos = [r for r in sorted(carpeta.rglob("*"))
                     if r.is_file() and r.relative_to(raiz).as_posix() not in en_uso]
        total = sum(r.stat().st_size for r in huerfanos)
        self.stdout.write(f"Archivos de cargas en uso: {len(en_uso)}")
        self.stdout.write(f"Archivos huérfanos: {len(huerfanos)} ({total / 1024:.1f} KB)")
        for ruta in huerfanos[:20]:
            self.stdout.write(f"  {ruta.relative_to(raiz).as_posix()}")
        if len(huerfanos) > 20:
            self.stdout.write(f"  ... y {len(huerfanos) - 20} más")
        if not huerfanos:
            return
        if not options["confirmar"]:
            self.stdout.write("Modo simulación: no se movió nada. Ejecutar con --confirmar.")
            return
        destino = raiz / f"_huerfanos_{timezone.localtime():%Y%m%d_%H%M}"
        for ruta in huerfanos:
            nuevo = destino / ruta.relative_to(carpeta)
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(ruta), str(nuevo))
        self.stdout.write(self.style.SUCCESS(
            f"{len(huerfanos)} archivos movidos a {destino.relative_to(raiz).as_posix()}. "
            "Revisarlos y borrar la carpeta a mano si no se necesitan."))
