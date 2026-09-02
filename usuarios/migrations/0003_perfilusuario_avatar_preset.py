from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usuarios', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilusuario',
            name='avatar_preset',
            field=models.CharField(
                blank=True,
                choices=[
                    ('guindo', 'guindo'),
                    ('guindo_oscuro', 'guindo_oscuro'),
                    ('azul', 'azul'),
                    ('verde', 'verde'),
                    ('teal', 'teal'),
                    ('naranja', 'naranja'),
                    ('gris', 'gris'),
                    ('dorado', 'dorado'),
                ],
                max_length=20,
                verbose_name='Avatar predefinido',
            ),
        ),
    ]
