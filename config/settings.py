"""
Configuración del proyecto: Sistema de apoyo a la auditoría externa
(detección de transacciones atípicas con Machine Learning).
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# Clave, modo depuración y hosts se leen del entorno (.env, ver .env.example).
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-key-cambiar-en-produccion")

DEBUG = os.environ.get("DEBUG", "True") == "True"

# Las cargas se procesan en segundo plano; en las pruebas automáticas, en el momento.
PROCESAR_EN_SEGUNDO_PLANO = "test" not in sys.argv

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

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

# Base de datos: MySQL Server.
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

# La sesión expira al cerrar el navegador o tras 30 minutos de inactividad.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 30  # 30 minutos
# Renueva el plazo con cada petición: cuenta desde la última actividad, no desde el login.
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Cookies seguras solo si el sitio se sirve por HTTPS (USAR_HTTPS=True).
USAR_HTTPS = os.environ.get("USAR_HTTPS", "False") == "True"
SESSION_COOKIE_SECURE = USAR_HTTPS
CSRF_COOKIE_SECURE = USAR_HTTPS
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Envío de correo.
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

# Caché en memoria: bloqueo de login. Con varios procesos conviene Redis.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Registro de errores: consola y archivo sistema.log (ignorado por git). Los fallos del
# procesamiento en segundo plano quedan aquí aunque nadie esté mirando la consola.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "consola": {"class": "logging.StreamHandler", "formatter": "simple"},
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "sistema.log",
            "maxBytes": 2 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "simple",
        },
    },
    "loggers": {
        "registros": {"handlers": ["consola", "archivo"], "level": "WARNING"},
        "usuarios": {"handlers": ["consola", "archivo"], "level": "WARNING"},
        "analisis": {"handlers": ["consola", "archivo"], "level": "WARNING"},
    },
}
