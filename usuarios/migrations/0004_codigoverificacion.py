from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('usuarios', '0003_perfilusuario_avatar_preset'),
    ]

    operations = [
        migrations.CreateModel(
            name='CodigoVerificacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.CharField(max_length=6)),
                ('creado', models.DateTimeField(auto_now_add=True)),
                ('usado', models.BooleanField(default=False)),
                ('intentos', models.PositiveSmallIntegerField(default=0)),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='codigos_verificacion', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Código de verificación',
                'verbose_name_plural': 'Códigos de verificación',
                'ordering': ['-creado'],
            },
        ),
    ]
