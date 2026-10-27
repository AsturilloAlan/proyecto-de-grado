"""Vista local de solo lectura con datos ficticios. Ejecutar desde la raíz del proyecto.

Sirve únicamente pantallas permitidas y estáticos; SQLite vive en memoria y no
se habilita ningún acceso autenticado sobre el servidor real. Ctrl+C termina.
"""
import os
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, unquote
import mimetypes

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.argv = ["vista_flujo", "test"]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.conf import settings
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
settings.ALLOWED_HOSTS = ["testserver", "127.0.0.1"]
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0)
from django.contrib.auth.models import User, Group
from django.test import Client
from registros.models import EmpresaAuditada, Gestion, CargaArchivo, ErrorValidacion
from analisis.models import AnalisisCarga
from analisis.models import ResultadoTransaccion
from registros.models import CuentaContable, RegistroContable

usuario = User.objects.create_user("auditor_demo")
usuario.groups.add(Group.objects.get(name="Auditor"))
gestion = Gestion.objects.create(anio=2025, fecha_inicio="2025-01-01", fecha_fin="2025-12-31")
for indice, nombre in enumerate(("Comercial Andina · Demostración", "Servicios del Sur · Demostración")):
    empresa = EmpresaAuditada.objects.create(nombre=nombre, categoria_cierre="general")
    carga = CargaArchivo.objects.create(empresa=empresa, gestion=gestion, usuario=usuario,
        estado="con_observaciones", registros_validos=2, total_registros=2)
    if indice == 0:
        analisis_demo = AnalisisCarga.objects.create(carga=carga, estado="completado", algoritmo="Demostración", total_registros=2, total_atipicas=2)
        cuenta_demo = CuentaContable.objects.create(empresa=empresa, codigo="5101", nombre="Gastos de demostración")
        for fila in (1, 2):
            registro_demo = RegistroContable.objects.create(carga=carga, cuenta=cuenta_demo, fecha="2025-01-15", fila_origen=fila, glosa="Servicio de mantenimiento ficticio", debe=5000 * fila)
            ResultadoTransaccion.objects.create(analisis=analisis_demo, registro=registro_demo, atipica=True, nivel=.8, puntaje=1)
        ErrorValidacion.objects.create(carga=carga, tipo="aviso", fila=1, campo="cuenta", descripcion="Cuenta por verificar en esta demostración.")
cliente = Client()
cliente.force_login(usuario)
RUTAS = {"/", "/registros/cargar/", "/registros/cargar/1/", "/registros/cargar/2/", "/registros/empresas/", "/reportes/"}
RUTAS.update({"/reportes/empresa/1/gestion/1/graficos/", "/reportes/empresa/1/gestion/1/graficos/datos/"})
from django.urls import reverse
RUTAS.add(reverse("registros:detalle_carga", args=[1]))


class Vista(BaseHTTPRequestHandler):
    def do_GET(self):
        ruta = unquote(urlsplit(self.path).path)
        if ruta.startswith("/static/"):
            base = (RAIZ / "static").resolve()
            archivo = (base / ruta.removeprefix("/static/")).resolve()
            if not archivo.is_relative_to(base) or not archivo.is_file():
                self.send_error(404)
                return
            contenido = archivo.read_bytes()
            tipo = mimetypes.guess_type(str(archivo))[0] or "application/octet-stream"
            estado = 200
        elif ruta in RUTAS:
            respuesta = cliente.get(self.path)
            contenido, tipo, estado = respuesta.content, respuesta.get("Content-Type", "text/html"), respuesta.status_code
        else:
            self.send_error(404)
            return
        self.send_response(estado)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(contenido)


print("Vista ficticia de solo lectura en http://127.0.0.1:8001/", flush=True)
HTTPServer(("127.0.0.1", 8001), Vista).serve_forever()
