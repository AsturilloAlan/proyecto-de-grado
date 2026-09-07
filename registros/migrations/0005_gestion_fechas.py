from datetime import date

from django.db import migrations, models


def backfill_fechas(apps, schema_editor):
    """Las gestiones creadas antes de este cambio no tenían fecha de
    inicio/fin explícita (se asumía año calendario). Se completan con
    01/01 - 31/12 de su año como valor razonable por defecto; si alguna
    de esas empresas tiene un cierre distinto según su rubro, se puede
    corregir después desde el panel de "Agregar gestión" o el admin."""
    Gestion = apps.get_model("registros", "Gestion")
    for gestion in Gestion.objects.all():
        gestion.fecha_inicio = date(gestion.anio, 1, 1)
        gestion.fecha_fin = date(gestion.anio, 12, 31)
        gestion.save(update_fields=["fecha_inicio", "fecha_fin"])


class Migration(migrations.Migration):

    dependencies = [
        ("registros", "0004_errorvalidacion_tipo"),
    ]

    operations = [
        migrations.AddField(
            model_name="gestion",
            name="fecha_inicio",
            field=models.DateField(
                default=date(2000, 1, 1), verbose_name="Fecha de inicio de la gestión"
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="gestion",
            name="fecha_fin",
            field=models.DateField(
                default=date(2000, 12, 31), verbose_name="Fecha de fin de la gestión"
            ),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_fechas, migrations.RunPython.noop),
    ]
