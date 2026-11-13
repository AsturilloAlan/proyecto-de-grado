# Estado "en seguimiento" para los avisos de validación.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registros', '0022_contactos_contador'),
    ]

    operations = [
        migrations.AlterField(
            model_name='errorvalidacion',
            name='estado_revision',
            field=models.CharField(
                choices=[
                    ('pendiente', 'Pendiente'),
                    ('valido', 'Válido (falso positivo)'),
                    ('seguimiento', 'En seguimiento (por cotejar)'),
                    ('observado', 'Observado (hallazgo confirmado)'),
                ],
                default='pendiente',
                max_length=12,
            ),
        ),
    ]
