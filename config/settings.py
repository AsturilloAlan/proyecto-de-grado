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

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-key-cambiar-en-produccion")

DEBUG = os.environ.get("DEBUG", "True") == "True"

PROCESAR_EN_SEGUNDO_PLANO = "test" not in sys.argv
ANALIZAR_CON_MODELO = "test" not in sys.argv

ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "usuarios",
    "registros",
    "analisis",
    "reportes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "usuarios.middleware.JWTMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "usuarios.middleware.SinCacheMiddleware",
]

try:
    import whitenoise  # noqa: F401
except ImportError:
    MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")

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

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.mysql"),
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
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "usuarios.validadores.ComplejidadValidator"},
    {"NAME": "usuarios.validadores.HistorialValidator", "OPTIONS": {"cantidad": 5}},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/La_Paz"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
PASSWORD_RESET_TIMEOUT = 60 * 30
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 30
SESSION_SAVE_EVERY_REQUEST = True

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_MINUTOS_VIGENCIA = SESSION_COOKIE_AGE // 60
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

USAR_HTTPS = os.environ.get("USAR_HTTPS", "False") == "True"
SESSION_COOKIE_SECURE = USAR_HTTPS
CSRF_COOKIE_SECURE = USAR_HTTPS
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if USAR_HTTPS:
    USE_X_FORWARDED_HOST = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

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

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

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

if "test" in sys.argv:
    import tempfile

    MEDIA_ROOT = tempfile.mkdtemp(prefix="pruebas_media_")
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    LOGGING["handlers"]["archivo"] = {"class": "logging.NullHandler"}
    LOGGING["handlers"]["consola"] = {"class": "logging.NullHandler"}
    JWT_SECRET = "clave-jwt-solo-para-pruebas-automaticas-0123456789"
