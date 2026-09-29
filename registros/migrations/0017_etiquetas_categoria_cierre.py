from django.db import migrations, models


class Migration(migrations.Migration):
    # Reemplaza a 0017_textos_sin_guiones (mismo contenido, nombre más claro).
    # Si esa ya estaba aplicada, Django da esta por aplicada sin repetir nada.
    replaces = [("registros", "0017_textos_sin_guiones")]

    dependencies = [
        ("registros", "0016_errorvalidacion_estado_revision"),
    ]

    operations = [
        migrations.AlterField(
            model_name="empresaauditada",
            name="categoria_cierre",
            field=models.CharField(
                choices=[
                    ("general", "Comercio, servicios, bancos y seguros (cierre 31 de diciembre)"),
                    ("industrial", "Industrial o petrolera (cierre 31 de marzo)"),
                    ("agropecuaria", "Agropecuaria o agroindustrial (cierre 30 de junio)"),
                    ("minera", "Minera (cierre 30 de septiembre)"),
                ],
                default="general",
                help_text=(
                    "Define en qué mes cierra el año fiscal de esta empresa. Se usa solo para "
                    "sugerir las fechas al crear una nueva gestión; siempre se pueden ajustar a "
                    "mano si hay una excepción real."
                ),
                max_length=20,
                verbose_name="Categoría de cierre de gestión (SIN)",
            ),
        ),
        migrations.AlterField(
            model_name="registrocontable",
            name="saldo",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    "Saldo tal como venía en el archivo original (columna 'Saldo' del Libro "
                    "Mayor), guardado como referencia. No se calcula ni se valida; el sistema "
                    "no conoce la naturaleza deudora/acreedora de cada cuenta, así que no "
                    "intenta recalcularlo."
                ),
                max_digits=14,
                null=True,
            ),
        ),
    ]
