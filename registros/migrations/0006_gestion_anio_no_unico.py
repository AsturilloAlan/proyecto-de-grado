from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registros", "0005_gestion_fechas"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gestion",
            name="anio",
            field=models.PositiveIntegerField(verbose_name="Año"),
        ),
        migrations.AddConstraint(
            model_name="gestion",
            constraint=models.UniqueConstraint(
                fields=("anio", "fecha_inicio", "fecha_fin"),
                name="gestion_periodo_unico",
            ),
        ),
    ]
