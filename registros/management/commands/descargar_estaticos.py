"""Descarga Bootstrap y Bootstrap Icons a static/vendor para que el sistema se vea bien
sin internet (por ejemplo, en la defensa). Se ejecuta una sola vez con conexión:

    python manage.py descargar_estaticos
"""
from pathlib import Path
from urllib.request import urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

CDN = "https://cdn.jsdelivr.net/npm"
ARCHIVOS = {
    "bootstrap/bootstrap.min.css": f"{CDN}/bootstrap@5.3.3/dist/css/bootstrap.min.css",
    "bootstrap/bootstrap.bundle.min.js": f"{CDN}/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
    "bootstrap-icons/bootstrap-icons.min.css": f"{CDN}/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
    "bootstrap-icons/fonts/bootstrap-icons.woff2": f"{CDN}/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2",
    "bootstrap-icons/fonts/bootstrap-icons.woff": f"{CDN}/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff",
}


class Command(BaseCommand):
    help = "Descarga Bootstrap y sus íconos a static/vendor para uso sin internet."

    def handle(self, *args, **options):
        destino = Path(settings.BASE_DIR) / "static" / "vendor"
        for ruta, url in ARCHIVOS.items():
            archivo = destino / ruta
            archivo.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urlopen(url, timeout=30) as respuesta:
                    contenido = respuesta.read()
            except OSError as error:
                raise CommandError(f"No se pudo descargar {url}: {error}")
            archivo.write_bytes(contenido)
            self.stdout.write(f"  {ruta} ({len(contenido) // 1024} KB)")
        self.stdout.write(self.style.SUCCESS(f"Listo. Archivos en {destino}"))
