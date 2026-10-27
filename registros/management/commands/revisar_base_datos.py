"""Revisa la base de datos en busca de restos que el sistema ya no usa.

Solo informa; no modifica nada. Uso: python manage.py revisar_base_datos
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Lista tablas y migraciones registradas que ya no corresponden al código."

    def handle(self, *args, **opciones):
        tablas_bd = set(connection.introspection.table_names())
        tablas_modelos = set()
        for modelo in apps.get_models(include_auto_created=True):
            tablas_modelos.add(modelo._meta.db_table)
        tablas_modelos.add("django_migrations")

        sobrantes = sorted(tablas_bd - tablas_modelos)
        faltantes = sorted(tablas_modelos - tablas_bd)

        executor = MigrationExecutor(connection)
        loader = executor.loader
        aplicadas = set(loader.applied_migrations)
        en_disco = set(loader.disk_migrations)
        reemplazadas = {r for m in loader.disk_migrations.values() for r in getattr(m, "replaces", [])}
        huerfanas = sorted(aplicadas - en_disco - reemplazadas)
        plan = executor.migration_plan(loader.graph.leaf_nodes())
        pendientes = sorted((m.app_label, m.name) for m, _ in plan)

        self.stdout.write(self.style.MIGRATE_HEADING("Tablas en la base que ningún modelo usa:"))
        self._listar(sobrantes)
        self.stdout.write(self.style.MIGRATE_HEADING("Tablas que el código espera y no existen:"))
        self._listar(faltantes)
        self.stdout.write(self.style.MIGRATE_HEADING("Migraciones registradas cuyo archivo ya no existe:"))
        self._listar([f"{app}.{nombre}" for app, nombre in huerfanas])
        self.stdout.write(self.style.MIGRATE_HEADING("Migraciones pendientes de aplicar:"))
        self._listar([f"{app}.{nombre}" for app, nombre in pendientes])

        # Correo único por cuenta: lo exigen el 2FA y la recuperación de contraseña.
        from django.contrib.auth.models import User
        from django.db.models import Count
        from django.db.models.functions import Lower

        repetidos = (User.objects.exclude(email="").annotate(correo=Lower("email"))
                     .values("correo").annotate(n=Count("id")).filter(n__gt=1))
        self.stdout.write(self.style.MIGRATE_HEADING("Correos usados por más de una cuenta:"))
        self._listar([f"{r['correo']} ({r['n']} cuentas)" for r in repetidos])
        sin_correo = User.objects.filter(email="", is_active=True).values_list("username", flat=True)
        self.stdout.write(self.style.MIGRATE_HEADING("Cuentas activas sin correo (no pueden usar 2FA ni recuperar la clave):"))
        self._listar(list(sin_correo))

        if sobrantes or huerfanas:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "SQL sugerido para limpiar (revísalo y haz un respaldo antes de ejecutarlo en MySQL Workbench):"
            ))
            for tabla in sobrantes:
                self.stdout.write(f"  DROP TABLE `{tabla}`;")
            for app, nombre in huerfanas:
                self.stdout.write(f"  DELETE FROM django_migrations WHERE app='{app}' AND name='{nombre}';")
        else:
            self.stdout.write(self.style.SUCCESS("\nNo hay restos: la base coincide con el código."))

    def _listar(self, elementos):
        if not elementos:
            self.stdout.write("  (ninguna)")
        for elemento in elementos:
            self.stdout.write(f"  - {elemento}")
