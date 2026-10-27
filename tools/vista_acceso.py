"""Vista de acceso con datos ficticios, SQLite en memoria y correo simulado."""
import os
import sys
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.argv = ["vista_acceso", "test"]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.conf import settings
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
settings.ALLOWED_HOSTS = ["testserver"]
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
call_command("migrate", verbosity=0)
usuario = User.objects.create_user("demostracion", email="demo@example.test", password="Original-Segura-2026!")
cliente = Client()
cliente.post(reverse("login"), {"username": usuario.username, "password": "Original-Segura-2026!"})
enlace = reverse("restablecer_clave", kwargs={"uidb64": urlsafe_base64_encode(force_bytes(usuario.pk)), "token": default_token_generator.make_token(usuario)})
paginas = {ruta: cliente.get(ruta).content for ruta in ("/login/", "/verificar-codigo/", "/recuperar-clave/")}
paginas["/restablecer-demo/"] = cliente.get(enlace, follow=True).content

class Vista(BaseHTTPRequestHandler):
    def do_GET(self):
        ruta = urlsplit(self.path).path
        if ruta in paginas:
            contenido, tipo = paginas[ruta], "text/html; charset=utf-8"
        elif ruta.startswith("/static/"):
            base = (RAIZ / "static").resolve()
            archivo = (base / ruta.removeprefix("/static/")).resolve()
            if not archivo.is_relative_to(base) or not archivo.is_file():
                self.send_error(404)
                return
            contenido, tipo = archivo.read_bytes(), mimetypes.guess_type(str(archivo))[0] or "application/octet-stream"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.end_headers()
        self.wfile.write(contenido)

print("Vista ficticia: http://127.0.0.1:8002/login/", flush=True)
HTTPServer(("127.0.0.1", 8002), Vista).serve_forever()
