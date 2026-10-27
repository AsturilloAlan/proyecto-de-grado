"""Reglas de contraseña: largo, complejidad, parecido a la anterior e historial."""
import re

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from usuarios.models import HistorialClave
from usuarios.validadores import muy_parecidas, raiz

ACTUAL = "Auditoria-2025!"


class ReglasBasicasTests(TestCase):
    def test_rechaza_corta_y_sin_complejidad(self):
        for clave in ("Ab1-xyz", "solominusculas-2025", "SOLOMAYUS-2025", "SinNumeros-abc", "SinSimbolo2025x"):
            with self.subTest(clave=clave), self.assertRaises(ValidationError):
                validate_password(clave)

    def test_acepta_clave_fuerte(self):
        validate_password("Revision.Fiscal-77")

    def test_raiz_y_parecido(self):
        self.assertEqual(raiz("Cláve-2025!"), "clave")
        self.assertTrue(muy_parecidas("Auditoria-2025!", "Auditoria-2025!1"))
        self.assertTrue(muy_parecidas("Auditoria-2025!", "auditoria#2026"))
        self.assertFalse(muy_parecidas("Auditoria-2025!", "Revision.Fiscal-77"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class HistorialTests(TestCase):
    def setUp(self):
        cache.clear()
        # create_user no pasa por set_password; el formulario de alta y los cambios sí.
        self.usuario = User.objects.create_user(username="auditora", email="a@sts.bo")
        self.usuario.set_password(ACTUAL)
        self.usuario.save()

    def test_crear_usuario_guarda_raiz_cifrada(self):
        registro = HistorialClave.objects.get(usuario=self.usuario)
        self.assertNotIn("auditoria", registro.raiz_hash)

    def test_variante_de_la_anterior_se_rechaza(self):
        for clave in (ACTUAL, ACTUAL + "1", "Auditoria-2026#"):
            with self.subTest(clave=clave), self.assertRaises(ValidationError):
                validate_password(clave, self.usuario)

    def test_clave_distinta_se_acepta(self):
        validate_password("Revision.Fiscal-77", self.usuario)

    def test_guarda_solo_las_ultimas_cinco(self):
        for i, palabra in enumerate(["Pradera", "Montana", "Laguna", "Desierto", "Bosque", "Volcan"]):
            self.usuario.set_password(f"{palabra}-{i}0!x")
            self.usuario.save()
        self.assertEqual(HistorialClave.objects.filter(usuario=self.usuario).count(), 5)

    def test_cambio_desde_perfil_rechaza_parecida(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.post(reverse("cambiar_clave"), {
            "old_password": ACTUAL, "new_password1": ACTUAL + "1", "new_password2": ACTUAL + "1"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "muy parecida")
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(ACTUAL))

    def test_restablecer_por_correo_rechaza_variante(self):
        self.client.post(reverse("recuperar_clave"), {"email": "a@sts.bo"})
        enlace = re.search(r"https?://\S+/restablecer-clave/\S+/", mail.outbox[-1].body).group(0)
        formulario = self.client.get(enlace, follow=True)
        respuesta = self.client.post(formulario.redirect_chain[-1][0], {
            "new_password1": ACTUAL + "1", "new_password2": ACTUAL + "1"})
        self.assertEqual(respuesta.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(ACTUAL))


class HistorialDesdeLoginTests(TestCase):
    def test_cuenta_antigua_registra_su_clave_al_iniciar_sesion(self):
        cache.clear()
        usuario = User.objects.create_user(username="antigua", email="b@sts.bo", password=ACTUAL)
        self.assertFalse(HistorialClave.objects.filter(usuario=usuario).exists())
        self.client.post(reverse("login"), {"username": "antigua", "password": ACTUAL})
        self.assertTrue(HistorialClave.objects.filter(usuario=usuario).exists())
        with self.assertRaises(ValidationError):
            validate_password(ACTUAL + "1", usuario)


class DatosPersonalesTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="socio", email="s@sts.bo", password="x")
        self.client.force_login(self.usuario)

    def test_cambia_nombre_y_apellido_y_queda_en_historial(self):
        from registros.models import HistorialCambio
        respuesta = self.client.post(reverse("perfil"), {"accion": "personales", "first_name": "  María  José ",
                                                          "last_name": "Pérez-Ávila", "clave_actual": "x"})
        self.assertRedirects(respuesta, reverse("perfil"))
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.get_full_name(), "María José Pérez-Ávila")
        self.assertTrue(HistorialCambio.objects.filter(objeto_id=self.usuario.pk, campo="first_name").exists())

    def test_rechaza_numeros_en_el_nombre(self):
        respuesta = self.client.post(reverse("perfil"), {"accion": "personales", "first_name": "Juan2", "last_name": "Pérez", "clave_actual": "x"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "solo admite letras")

    def test_perfil_muestra_ultimo_inicio_de_sesion(self):
        self.assertContains(self.client.get(reverse("perfil")), "Último inicio de sesión")


    def test_pide_contrasena_y_limita_cambios_a_uno_cada_30_dias(self):
        sin_clave = self.client.post(reverse("perfil"), {"accion": "personales", "first_name": "Ana", "last_name": "Rojas", "clave_actual": "mal"})
        self.assertContains(sin_clave, "La contraseña no es correcta")
        self.client.post(reverse("perfil"), {"accion": "personales", "first_name": "Ana", "last_name": "Rojas", "clave_actual": "x"})
        otra_vez = self.client.post(reverse("perfil"), {"accion": "personales", "first_name": "Ana María", "last_name": "Rojas", "clave_actual": "x"})
        self.assertContains(otra_vez, "Se podrá cambiar de nuevo")
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.first_name, "Ana")
