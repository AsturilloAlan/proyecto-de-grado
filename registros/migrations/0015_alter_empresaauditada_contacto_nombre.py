# Generated manually on 2026-09-28
# Renombra la etiqueta del campo "contacto_nombre" a "Nombre del representante
# legal" (pedido explícito de una de las auditoras durante la revisión
# funcional). El nombre interno del campo Python/columna no cambia, solo el
# verbose_name que se muestra en formularios y en el panel de administración.

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
