"""Prueba de rendimiento (actividad 20, RNF-02).

Genera un libro sintético del tamaño indicado (por defecto 24.000 filas, el libro más
grande de la firma), lo procesa con el flujo real y mide cuánto tarda cada etapa. Todo se
hace dentro de una transacción que al final se deshace y con archivos en una carpeta
temporal: no queda ningún dato en la base ni en media/.

Uso:
    python manage.py medir_rendimiento
    python manage.py medir_rendimiento --filas 24000 --repeticiones 3 --csv rendimiento.csv
    python manage.py medir_rendimiento --carga 49 --repeticiones 3 --csv rendimiento.csv

Con --carga se mide con el archivo real de esa carga (recomendado para la tesis): el libro
sintético tiene cuentas y glosas que el modelo no conoce, así que casi todo sale atípico y
el PDF resulta de cientos de páginas, algo que no ocurre con un libro real.
"""
import csv
import random
import statistics
import tempfile
import time
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import override_settings

from analisis import servicio as servicio_analisis
from registros.models import CargaArchivo, EmpresaAuditada, Gestion
from registros.services import procesar_carga
from reportes import exportar, servicio


class _Deshacer(Exception):
    """Se lanza al final para deshacer todo lo creado durante la medición."""


def libro_sintetico(filas, semilla=7):
    rng = random.Random(semilla)
    cuentas = [("1101", 900), ("1103", 15000), ("2101", 6000), ("4101", 12000), ("5101", 2500),
               ("5102", 800), ("2103", 7000), ("1201", 40000)]
    lineas = ["fecha,cuenta,comprobante,glosa,debe,haber"]
    inicio = date(2023, 1, 1)
    for i in range(filas):
        cuenta, media = rng.choice(cuentas)
        monto = round(abs(rng.gauss(media, media * 0.3)), 2) or 1.0
        if rng.random() < 0.003:  # algunos importes extraordinarios
            monto *= rng.randint(20, 80)
        fecha = inicio + timedelta(days=rng.randint(0, 364))
        debe, haber = (monto, 0) if rng.random() < 0.5 else (0, monto)
        lineas.append(f"{fecha:%d/%m/%Y},{cuenta},C-{i // 3},Operación {i},{debe},{haber}")
    return "\n".join(lineas).encode("utf-8")


class Command(BaseCommand):
    help = "Mide los tiempos del flujo real con un libro sintético, sin dejar datos guardados."

    def add_arguments(self, parser):
        parser.add_argument("--filas", type=int, default=24000)
        parser.add_argument("--carga", type=int, default=None,
                            help="ID de una carga real: se mide con una copia de su archivo.")
        parser.add_argument("--repeticiones", type=int, default=1)
        parser.add_argument("--csv", type=str, default=None, help="Guardar los tiempos en CSV.")

    def _medir(self, etapa, funcion, tiempos):
        inicio = time.perf_counter()
        resultado = funcion()
        tiempos.setdefault(etapa, []).append(time.perf_counter() - inicio)
        return resultado

    def _una_corrida(self, filas, tiempos, original=None):
        usuario = get_user_model().objects.create_user(username="_medicion_rendimiento")
        if original is not None:
            # Copia del libro real, en la misma empresa y gestión (todo se deshace al final).
            empresa, gestion = original["empresa"], original["gestion"]
            nombre, contenido = original["nombre"], original["contenido"]
        else:
            empresa = EmpresaAuditada.objects.create(nombre="_Empresa de medición")
            gestion, _ = Gestion.objects.get_or_create(
                anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
            nombre, contenido = "medicion.csv", libro_sintetico(filas)
        carga = CargaArchivo(usuario=usuario, empresa=empresa, gestion=gestion)
        carga.archivo.save(nombre, ContentFile(contenido), save=True)

        self._medir("1. Lectura y validación del libro", lambda: procesar_carga(carga), tiempos)
        carga.refresh_from_db()
        analisis = self._medir("2. Análisis con el modelo (incluye motivos)",
                               lambda: servicio_analisis.analizar_ahora(carga), tiempos)
        if analisis is None or analisis.estado != "completado":
            tiempos.pop("2. Análisis con el modelo (incluye motivos)", None)
            self.stdout.write(self.style.WARNING(
                f"  Sin análisis: {analisis.mensaje if analisis else 'carga sin registros válidos'}"))
            return carga, None
        datos = self._medir("3. Reporte de atípicas (cálculo)",
                            lambda: servicio.reporte_atipicas([carga]), tiempos)
        self._medir("4. Gráficos de la gestión (datos)", lambda: servicio.panorama([carga]), tiempos)
        datos.update({"empresa": empresa, "gestion": gestion, "carga": carga, "grupo": None,
                      "emision": type("E", (), {"id": 0})(), "fecha_generacion": analisis.fecha_fin,
                      "generado_por": usuario})
        documento = exportar.documento_atipicas(datos)
        self._medir("5. Exportación a PDF", lambda: exportar.a_pdf(documento), tiempos)
        self._medir("6. Exportación a Excel", lambda: exportar.a_excel(documento), tiempos)
        return carga, analisis

    def handle(self, *args, **options):
        filas = options["filas"]
        tiempos = {}
        resumen = {}
        original = None
        if options["carga"]:
            import os

            base = CargaArchivo.objects.select_related("empresa", "gestion").get(pk=options["carga"])
            with base.archivo.open("rb") as archivo:
                original = {"empresa": base.empresa, "gestion": base.gestion,
                            "nombre": os.path.basename(base.archivo.name), "contenido": archivo.read()}
            self.stdout.write(f"Libro real de la carga N.º {base.id} ({base.empresa}, {base.gestion}), "
                              f"{options['repeticiones']} repetición(es).")
        else:
            self.stdout.write(f"Libro sintético de {filas:,} filas, {options['repeticiones']} repetición(es)."
                              .replace(",", "."))
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="medicion_")):
            for numero in range(options["repeticiones"]):
                try:
                    with transaction.atomic():
                        carga, analisis = self._una_corrida(filas, tiempos, original)
                        resumen = {"registros": carga.registros_validos,
                                   "atipicas": analisis.total_atipicas if analisis else 0}
                        raise _Deshacer()
                except _Deshacer:
                    pass
                self.stdout.write(f"  Repetición {numero + 1} terminada.")

        self.stdout.write(f"\nRegistros válidos: {resumen.get('registros', 0)} · "
                          f"atípicas: {resumen.get('atipicas', 0)}\n")
        self.stdout.write(f"{'Etapa':<46}{'Promedio (s)':>14}{'Máximo (s)':>12}")
        filas_csv = []
        total = 0.0
        for etapa, valores in tiempos.items():
            promedio, maximo = statistics.mean(valores), max(valores)
            total += promedio
            filas_csv.append({"etapa": etapa, "promedio_s": round(promedio, 3), "maximo_s": round(maximo, 3)})
            self.stdout.write(f"{etapa:<46}{promedio:>14.2f}{maximo:>12.2f}")
        self.stdout.write(f"{'Total del flujo':<46}{total:>14.2f}")
        self.stdout.write("No se guardó ningún dato: la medición se deshizo al terminar.")
        if options["csv"]:
            with open(options["csv"], "w", newline="", encoding="utf-8-sig") as archivo:
                escritor = csv.DictWriter(archivo, fieldnames=["etapa", "promedio_s", "maximo_s"])
                escritor.writeheader()
                escritor.writerows(filas_csv)
            self.stdout.write(self.style.SUCCESS(f"Tiempos guardados en {options['csv']}"))
