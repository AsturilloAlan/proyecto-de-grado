"""Plan de cuentas por empresa.

Cada cuenta existente se asigna a la empresa de las cargas que la usan. Si la usan
cargas de varias empresas, se crea una copia por empresa y sus registros pasan a la
copia. No se borra ningún dato.
"""
from django.db import migrations, models
import django.db.models.deletion


def asignar_empresa(apps, schema_editor):
    CuentaContable = apps.get_model("registros", "CuentaContable")
    RegistroContable = apps.get_model("registros", "RegistroContable")
    CargaArchivo = apps.get_model("registros", "CargaArchivo")

    cargas_por_empresa = {}
    for carga_id, empresa_id in CargaArchivo.objects.values_list("id", "empresa_id"):
        cargas_por_empresa.setdefault(empresa_id, []).append(carga_id)

    for cuenta in CuentaContable.objects.all():
        empresas = sorted(set(
            RegistroContable.objects.filter(cuenta_id=cuenta.id)
            .values_list("carga__empresa_id", flat=True)
        ))
        if not empresas:
            continue  # Cuenta sin uso: queda sin empresa.
        cuenta.empresa_id = empresas[0]
        cuenta.save(update_fields=["empresa"])
        for empresa_id in empresas[1:]:
            copia = CuentaContable.objects.create(
                empresa_id=empresa_id, codigo=cuenta.codigo,
                nombre=cuenta.nombre, tipo=cuenta.tipo,
            )
            RegistroContable.objects.filter(
                cuenta_id=cuenta.id, carga_id__in=cargas_por_empresa[empresa_id]
            ).update(cuenta_id=copia.id)


class Migration(migrations.Migration):

    dependencies = [
        ("registros", "0017_etiquetas_categoria_cierre"),
    ]

    operations = [
        migrations.AddField(
            model_name="cuentacontable",
            name="empresa",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="cuentas", to="registros.empresaauditada",
            ),
        ),
        migrations.AlterField(
            model_name="cuentacontable",
            name="codigo",
            field=models.CharField(max_length=30),
        ),
        migrations.RunPython(asignar_empresa, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="cuentacontable",
            constraint=models.UniqueConstraint(
                fields=("empresa", "codigo"), name="cuenta_codigo_por_empresa"
            ),
        ),
    ]
