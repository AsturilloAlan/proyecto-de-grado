"""Crea los roles (grupos) base del sistema: Administrador y Auditor.

RF-07: Gestión de usuarios con control de acceso por roles.
- Administrador: gestiona empresas clientes, gestiones y usuarios.
- Auditor: carga archivos, revisa validaciones, consulta análisis y reportes.
"""
from django.db import migrations

ROLES = ["Administrador", "Auditor"]


def crear_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in ROLES:
        Group.objects.get_or_create(name=nombre)


def eliminar_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_roles, eliminar_roles),
    ]
