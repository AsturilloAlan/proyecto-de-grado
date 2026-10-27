"""Regresiones de entradas manuales y peticiones que omiten la interfaz."""
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.signals import request_finished
from django.db import close_old_connections
from django.test import Client, TestCase
from django.urls import reverse

from .forms import CodigoVerificacionForm
from .views import _clave_intentos_login, _clave_bloqueo_login


class AccesoAdversarialTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.usuario = User.objects.create_user("auditor", email="auditor@example.test", password="Clave-Segura123")

    def test_variantes_unicode_comparten_limite(self):
        for nombre in ("auditor", " auditor ", "AUDITOR", "ａｕｄｉｔｏｒ"):
            with self.subTest(nombre=nombre):
                self.assertEqual(_clave_intentos_login(nombre), _clave_intentos_login("auditor"))
                self.assertEqual(_clave_bloqueo_login(nombre), _clave_bloqueo_login("auditor"))

    def test_unicode_no_elude_bloqueo_con_clave_correcta(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "auditor", "password": "incorrecta"})
        with patch("usuarios.views.enviar_codigo") as enviar:
            respuesta = self.client.post(reverse("login"), {"username": "ａｕｄｉｔｏｒ", "password": "Clave-Segura123"})
        self.assertRedirects(respuesta, reverse("login"))
        enviar.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_clave_cache_acotada_sin_caracteres_de_control(self):
        clave = _clave_intentos_login("a" * 10000 + "\r\n\t")
        self.assertLess(len(clave), 100)
        self.assertNotIn("\n", clave)

    def test_codigos_invalidos(self):
        for codigo in ("abcdef", "12345", "1234567", "１２３４５６", "١٢٣٤٥٦", "12 456", "123\n45", "<img>", "00000²"):
            with self.subTest(codigo=codigo):
                self.assertFalse(CodigoVerificacionForm({"codigo": codigo}).is_valid())
        for codigo in ("000001", "123456", " 123456 "):
            self.assertTrue(CodigoVerificacionForm({"codigo": codigo}).is_valid())

    def test_login_requiere_csrf(self):
        respuesta = Client(enforce_csrf_checks=True).post(reverse("login"), {"username": "auditor", "password": "incorrecta"})
        self.assertEqual(respuesta.status_code, 403)

    def test_auditor_no_puede_crear_ni_editar_usuarios(self):
        self.usuario.groups.add(Group.objects.get(name="Auditor"))
        self.client.force_login(self.usuario)
        for url in (reverse("usuario_crear"), reverse("usuario_editar", args=[self.usuario.pk])):
            with self.subTest(url=url):
                self.assertRedirects(self.client.post(url, {"rol": "Administrador", "is_superuser": "1"}), reverse("home"))
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.is_superuser)
        self.assertFalse(self.usuario.groups.filter(name="Administrador").exists())
        self.assertEqual(User.objects.count(), 1)

    def test_login_escapa_html_y_conserva_autocompletado(self):
        respuesta = self.client.post(reverse("login"), {"username": '\"><script>alert(1)</script>', "password": "incorrecta"})
        self.assertNotContains(respuesta, '<script>alert(1)</script>')
        self.assertContains(respuesta, 'autocomplete="username"')
        self.assertContains(respuesta, 'autocomplete="current-password"')

    def test_avatar_no_permite_saltar_a_originales_contables(self):
        # Cuenta autenticada SIN rol: puede ver avatares, no archivos contables.
        self.client.force_login(self.usuario)
        with TemporaryDirectory() as carpeta, self.settings(MEDIA_ROOT=carpeta):
            raiz = Path(carpeta)
            (raiz / "cargas").mkdir()
            (raiz / "avatares").mkdir()
            (raiz / "cargas" / "privado.csv").write_text("dato contable ficticio", encoding="utf-8")
            for ruta in ("../cargas/privado.csv", "..%2Fcargas%2Fprivado.csv", "..%5Ccargas%5Cprivado.csv"):
                with self.subTest(ruta=ruta):
                    respuesta = self.client.get("/media/avatares/" + ruta)
                    self.assertIn(respuesta.status_code, (400, 404))

    def test_original_se_descarga_sin_renderizar_html(self):
        self.usuario.groups.add(Group.objects.get(name="Auditor"))
        self.client.force_login(self.usuario)
        with TemporaryDirectory() as carpeta, self.settings(MEDIA_ROOT=carpeta):
            raiz = Path(carpeta) / "cargas"
            raiz.mkdir()
            (raiz / "prueba.csv").write_text("contenido ficticio", encoding="utf-8")
            respuesta = self.client.get("/media/cargas/prueba.csv")
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta["Content-Disposition"], "attachment")
            # respuesta.close() dispara request_finished, que cierra la conexión a la base
            # y rompe la transacción de las pruebas siguientes; se desconecta mientras tanto.
            request_finished.disconnect(close_old_connections)
            try:
                respuesta.close()
            finally:
                request_finished.connect(close_old_connections)
