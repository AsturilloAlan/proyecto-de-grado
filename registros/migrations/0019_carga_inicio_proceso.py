from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("registros", "0018_cuenta_por_empresa"),
    ]

    operations = [
        migrations.AddField(
            model_name="cargaarchivo",
            name="inicio_proceso",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="Inicio del procesamiento"
            ),
        ),
    ]
