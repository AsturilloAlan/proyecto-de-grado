from django.db.backends.mysql.base import DatabaseWrapper as MySQLDatabaseWrapper


class DatabaseWrapper(MySQLDatabaseWrapper):
    """Backend MySQL que acepta la versión 5.7 del servidor de hosting."""

    def check_database_version_supported(self):
        pass
