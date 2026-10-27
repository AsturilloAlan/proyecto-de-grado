from django.apps import AppConfig


class UsuariosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "usuarios"
    verbose_name = "Usuarios"

    def ready(self):
        # Registra la señal que pide emitir el JWT al iniciar sesión.
        from . import middleware  # noqa: F401
