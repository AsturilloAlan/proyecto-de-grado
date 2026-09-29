# Generated manually on 2026-09-28
# Agrega la clasificación individual del hallazgo (pendiente / válido /
# observado) a ErrorValidacion, pedida por la firma auditora para poder
# distinguir un aviso descartado como falso positivo de uno que sí es una
# irregularidad real a documentar.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registros', '0015_alter_empresaauditada_contacto_nombre'),
    ]

    operations = [
        migrations.AddField(
            model_name='errorvalidacion',
            name='estado_revision',
            field=models.CharField(
                choices=[
                    ('pendiente', 'Pendiente'),
                    ('valido', 'Válido (falso positivo)'),
                    ('observado', 'Observado (hallazgo confirmado)'),
                ],
                default='pendiente',
                max_length=10,
            ),
        ),
    ]
