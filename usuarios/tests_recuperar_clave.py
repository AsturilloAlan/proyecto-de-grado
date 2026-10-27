"""Recuperación de contraseña por correo y bitácora de correos (RegistroCorreo)."""
import re

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from registros.models import HistorialCambio
from usuarios.models import RegistroCorreo

CLAVE_NUEVA = "Otra-Clave-Segura-2026"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RecuperarClaveTests(TestCase):
    def setUp(self):
        cache.clear()
        Group.objects.get_or_create(name="Auditor")
        self.usuario = User.objects.create_user(
            username="auditora", email="auditora@sts.bo", password="Clave-Inicial-2026")

    def _solicitar(self, correo="auditora@sts.bo"):
        return self.client.post(reverse("recuperar_clave"), {"email": correo})

    def _enlace(self):
        return re.search(r"https?://\S+/restablecer-clave/\S+/", mail.outbox[-1].body).group(0)

    def test_login_ofrece_recuperar_clave(self):
        self.assertContains(self.client.get(reverse("login")), reverse("recuperar_clave"))

    def test_correo_registrado_recibe_enlace_y_queda_en_bitacora(self):
        respuesta = self._solicitar("AUDITORA@sts.bo")
        self.assertRedirects(respuesta, reverse("recuperar_clave_enviado"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/restablecer-clave/", mail.outbox[0].body)
        registro = RegistroCorreo.objects.get(tipo="recuperar_clave")
        self.assertTrue(registro.enviado)
        self.assertEqual(registro.usuario, self.usuario)
        # La referencia del pie coincide con el identificador único del registro.
        self.assertIn(str(registro.identificador)[:8].upper(), mail.outbox[0].body)

    def test_correo_desconocido_recibe_la_misma_respuesta_sin_envio(self):
        respuesta = self._solicitar("nadie@sts.bo")
        self.assertRedirects(respuesta, reverse("recuperar_clave_enviado"))
        self.assertEqual(len(mail.outbox), 0)

    def test_cuenta_inactiva_no_recibe_enlace(self):
        self.usuario.is_active = False
        self.usuario.save()
        self._solicitar()
        self.assertEqual(len(mail.outbox), 0)

    def test_correo_repetido_en_dos_cuentas_no_envia(self):
        User.objects.create_user(username="otra", email="auditora@sts.bo", password="x-Clave-2026")
        self._solicitar()
        self.assertEqual(len(mail.outbox), 0)

    def test_limite_de_solicitudes(self):
        for _ in range(5):
            self._solicitar()
        self.assertEqual(len(mail.outbox), 3)

    def test_enlace_cambia_la_clave_registra_y_avisa(self):
        self._solicitar()
        enlace = self._enlace()
        formulario = self.client.get(enlace, follow=True)
        self.assertContains(formulario, "Crear una contraseña nueva")
        respuesta = self.client.post(formulario.redirect_chain[-1][0], {
            "new_password1": CLAVE_NUEVA, "new_password2": CLAVE_NUEVA})
        self.assertRedirects(respuesta, reverse("restablecer_clave_hecho"))
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(CLAVE_NUEVA))
        self.assertTrue(HistorialCambio.objects.filter(
            objeto_id=self.usuario.pk, campo="password", valor_nuevo="(restablecida por correo)").exists())
        self.assertTrue(RegistroCorreo.objects.filter(tipo="clave_restablecida", enviado=True).exists())

    def test_enlace_es_de_un_solo_uso(self):
        self._solicitar()
        enlace = self._enlace()
        formulario = self.client.get(enlace, follow=True)
        self.client.post(formulario.redirect_chain[-1][0], {
            "new_password1": CLAVE_NUEVA, "new_password2": CLAVE_NUEVA})
        otra_vez = self.client.get(enlace, follow=True)
        self.assertContains(otra_vez, "El enlace ya no es válido")

    def test_enlace_alterado_no_sirve(self):
        self._solicitar()
        enlace = self._enlace().rstrip("/") + "x/"
        self.assertContains(self.client.get(enlace, follow=True), "El enlace ya no es válido")

    def test_clave_debil_se_rechaza(self):
        self._solicitar()
        formulario = self.client.get(self._enlace(), follow=True)
        respuesta = self.client.post(formulario.redirect_chain[-1][0], {
            "new_password1": "123", "new_password2": "123"})
        self.assertEqual(respuesta.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertFalse(self.usuario.check_password("123"))

    def test_restablecer_cierra_sesiones_abiertas(self):
        otro_cliente = self.client_class()
        otro_cliente.force_login(self.usuario)
        self._solicitar()
        formulario = self.client.get(self._enlace(), follow=True)
        self.client.post(formulario.redirect_chain[-1][0], {
            "new_password1": CLAVE_NUEVA, "new_password2": CLAVE_NUEVA})
        respuesta = otro_cliente.get(reverse("perfil"))
        self.assertEqual(respuesta.status_code, 302)

    def test_codigo_2fa_queda_en_bitacora(self):
        self.client.post(reverse("login"), {"username": "auditora", "password": "Clave-Inicial-2026"})
        self.assertTrue(RegistroCorreo.objects.filter(tipo="codigo_2fa", usuario=self.usuario, enviado=True).exists())
