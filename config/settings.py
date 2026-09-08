"""
Configuración del proyecto: Sistema de apoyo a la auditoría externa
(detección de transacciones atípicas con Machine Learning).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# ADVERTENCIA DE SEGURIDAD: esta clave es solo para desarrollo.
# Antes de desplegar en producción, moverla a una variable de entorno.
SECRET_KEY = "django-insecure-dev-key-cambiar-en-produccion"

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps del proyecto (una por cada bloque de la Iteración correspondiente)
    "usuarios",
    "registros",
    "analisis",
    "reportes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "usuarios.middleware.SinCacheMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "usuarios.context_processors.navegacion",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Base de datos: MySQL Server (instalación estándar con MySQL Workbench,
# ya no XAMPP). Crea la base "auditoria_db" en MySQL Workbench antes de
# correr las migraciones (ver instrucciones en el README).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "auditoria_db"),
        "USER": os.environ.get("DB_USER", "root"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "3307"),
        "OPTIONS": {
            "charset": "utf8mb4",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/La_Paz"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Archivos cargados por los usuarios (ej. archivos de registros contables, RF-01)
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Autenticación (RF-07: gestión de usuarios, con control de acceso por roles)
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Seguridad de sesión: al tratarse de información contable/financiera
# sensible (RNF-03), la sesión se cierra al cerrar el navegador y expira
# tras 30 minutos de inactividad, en vez de quedar abierta indefinidamente.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 30  # 30 minutos
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Envío de correo (RNF-03: verificación en dos pasos / 2FA por correo al
# iniciar sesión). Usa variables de entorno para no dejar la contraseña
# de la cuenta de correo escrita en el código. Para configurarlo:
#   1. Crea (o usa) una cuenta de Gmail para el sistema.
#   2. Activa la verificación en dos pasos de esa cuenta de Google.
#   3. Genera una "contraseña de aplicación" (myaccount.google.com/apppasswords).
#   4. Define en tu máquina las variables de entorno EMAIL_HOST_USER y
#      EMAIL_HOST_PASSWORD con esos datos (nunca las escribas aquí).
# Si no se configuran, EMAIL_BACKEND cae a la consola (los códigos se
# imprimen en la terminal donde corre `runserver`) para poder probar el
# flujo de 2FA sin depender de un correo real.
if os.environ.get("EMAIL_HOST_USER") and os.environ.get("EMAIL_HOST_PASSWORD"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "sistema@sts-auditores.local"
)

# Caché en memoria del proceso: se usa para el bloqueo temporal por
# intentos fallidos de login (RNF-03) y para los códigos de verificación
# 2FA. Alcanza para el volumen de esta firma (2 usuarias); en un
# despliegue con varios procesos/servidores convendría cambiar a un
# backend compartido (ej. Redis).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
