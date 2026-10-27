"""Aplica el modelo ya entrenado a una carga, sin reentrenar.

Uso:
    python manage.py puntuar_carga 46
    python manage.py puntuar_carga 46 --top 20 --csv puntajes_carga_46.csv
"""
from django.core.management.base import BaseCommand, CommandError

from analisis.datos import dataset_de_carga
from analisis.modelo import cargar, puntuar


class Command(BaseCommand):
    help = "Puntúa una carga con el modelo guardado y muestra las transacciones más atípicas."

    def add_arguments(self, parser):
        parser.add_argument("carga", type=int)
        parser.add_argument("--top", type=int, default=15)
        parser.add_argument("--csv", type=str, default=None, help="Guardar todos los puntajes en CSV.")

    def handle(self, *args, **options):
        try:
            paquete = cargar()
        except FileNotFoundError as error:
            raise CommandError(str(error))
        nombre, df = dataset_de_carga(options["carga"])
        resultado = puntuar(df, paquete).merge(
            df[["fila_origen", "fecha", "cuenta_codigo", "cuenta_nombre", "debe", "haber", "glosa"]],
            on="fila_origen")
        atipicas = int(resultado["atipica"].sum())
        self.stdout.write(f"{nombre}: {len(resultado)} registros, {atipicas} atípicas "
                          f"({atipicas / len(resultado):.1%}).\n")

        self.stdout.write(f"Las {options['top']} más atípicas:")
        for _, fila in resultado.sort_values("nivel", ascending=False).head(options["top"]).iterrows():
            monto = float(fila["debe"] or 0) or float(fila["haber"] or 0)
            self.stdout.write(
                f"  fila {fila['fila_origen']:>6}  nivel {fila['nivel']:.2f}  {fila['fecha']}  "
                f"{fila['cuenta_codigo']} {str(fila['cuenta_nombre'])[:28]:<28}  {monto:>14,.2f}")
        if options["csv"]:
            resultado.sort_values("nivel", ascending=False).to_csv(
                options["csv"], index=False, encoding="utf-8-sig")
            self.stdout.write(self.style.SUCCESS(f"\nPuntajes guardados en {options['csv']}"))
