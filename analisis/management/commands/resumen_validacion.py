"""Resumen de la validación preliminar del modelo (actividad 13).

Muestra, por carga analizada, cuántas transacciones atípicas revisó el auditor y qué
proporción consideró relevante (precisión preliminar).

Uso:
    python manage.py resumen_validacion
    python manage.py resumen_validacion --primeras 10 --csv validacion_preliminar.csv
    python manage.py resumen_validacion --detalle revisiones_atipicas.csv
"""
import csv

from django.core.management.base import BaseCommand

from analisis.models import AnalisisCarga, ResultadoTransaccion


def _porcentaje(valor):
    return "-" if valor is None else f"{valor:.1f} %"


class Command(BaseCommand):
    help = "Resume la validación preliminar del modelo hecha por los auditores."

    def add_arguments(self, parser):
        parser.add_argument("--primeras", type=int, default=20,
                            help="Cantidad de transacciones más atípicas para la precisión en k.")
        parser.add_argument("--csv", type=str, default=None, help="Guardar el resumen en CSV.")
        parser.add_argument("--detalle", type=str, default=None,
                            help="Guardar en CSV cada atípica revisada, con motivos y comentario.")

    def handle(self, *args, **options):
        primeras = options["primeras"]
        analisis = (AnalisisCarga.objects.filter(estado="completado")
                    .exclude(carga__estado="anulada")
                    .select_related("carga__empresa", "carga__gestion")
                    .order_by("carga__gestion__anio", "carga_id"))
        filas = []
        for item in analisis:
            m = item.metricas_validacion(primeras=primeras)
            filas.append({
                "carga": item.carga_id,
                "empresa": str(item.carga.empresa),
                "gestion": item.carga.gestion.anio,
                "registros": item.total_registros,
                "atipicas": m["atipicas"],
                "revisadas": m["revisadas"],
                "observadas": m["observadas"],
                "seguimiento": m["seguimiento"],
                "validas": m["no_relevantes"],
                "utiles": m["relevantes"],
                "precision": m["precision"],
                f"precision_primeras_{primeras}": m["precision_primeras"],
                f"revisadas_primeras_{primeras}": m["primeras_revisadas"],
            })

        if not filas:
            self.stdout.write("No hay cargas analizadas por el modelo.")
            return
        if options["detalle"]:
            self._exportar_detalle(options["detalle"])

        self.stdout.write(f"{'Carga':>6}  {'Gestión':>7}  {'Atípicas':>8}  {'Revisadas':>9}  "
                          f"{'Observ.':>7}  {'Seguim.':>7}  {'Válidas':>7}  {'Precisión':>9}  {'P@' + str(primeras):>9}")
        total_revisadas = total_relevantes = 0
        for f in filas:
            total_revisadas += f["revisadas"]
            total_relevantes += f["utiles"]
            self.stdout.write(
                f"{f['carga']:>6}  {f['gestion']:>7}  {f['atipicas']:>8}  {f['revisadas']:>9}  "
                f"{f['observadas']:>7}  {f['seguimiento']:>7}  {f['validas']:>7}  {_porcentaje(f['precision']):>9}  "
                f"{_porcentaje(f[f'precision_primeras_{primeras}']):>9}")
        global_ = total_relevantes * 100 / total_revisadas if total_revisadas else None
        self.stdout.write(f"\nPrecisión preliminar global: {_porcentaje(global_)} "
                          f"({total_relevantes} útiles (observadas o en seguimiento) de {total_revisadas} revisadas).")

        if options["csv"]:
            with open(options["csv"], "w", newline="", encoding="utf-8-sig") as archivo:
                escritor = csv.DictWriter(archivo, fieldnames=list(filas[0]))
                escritor.writeheader()
                escritor.writerows(filas)
            self.stdout.write(self.style.SUCCESS(f"Resumen guardado en {options['csv']}"))

    def _exportar_detalle(self, ruta):
        revisadas = (ResultadoTransaccion.objects.filter(atipica=True)
                     .exclude(revision="pendiente")
                     .exclude(analisis__carga__estado="anulada")
                     .select_related("registro__cuenta", "analisis__carga__gestion", "revisado_por")
                     .order_by("analisis__carga_id", "-nivel"))
        columnas = ["carga", "gestion", "fila", "fecha", "cuenta", "cuenta_nombre", "comprobante",
                    "glosa", "debe", "haber", "nivel", "motivos", "revision", "comentario",
                    "revisado_por"]
        with open(ruta, "w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(columnas)
            for r in revisadas:
                reg = r.registro
                motivos = " | ".join(f"{m['aporte']}% {m['texto']}" for m in r.lista_motivos)
                escritor.writerow([
                    r.analisis.carga_id, r.analisis.carga.gestion.anio, reg.fila_origen,
                    reg.fecha.isoformat(), reg.cuenta.codigo, reg.cuenta.nombre,
                    reg.numero_comprobante, reg.glosa, reg.debe, reg.haber, round(r.nivel, 3),
                    motivos, r.get_revision_display(), r.comentario_revision,
                    r.revisado_por.username if r.revisado_por else "",
                ])
        self.stdout.write(self.style.SUCCESS(f"Detalle de {revisadas.count()} revisiones guardado en {ruta}"))
