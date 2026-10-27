# Generated manually on 2026-09-28
# Cambia solo la etiqueta visible; conserva el nombre del campo y la columna.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registros', '0014_registrocontable_saldo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='empresaauditada',
            name='contacto_nombre',
            field=models.CharField(
                blank=True,
                max_length=150,
                verbose_name='Nombre del representante legal',
            ),
        ),
    ]
