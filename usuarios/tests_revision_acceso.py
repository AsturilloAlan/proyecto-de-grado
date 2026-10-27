"""Casos adversariales de 2FA y recuperación; solo cuentas y correo de pruebas."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .forms import RestablecerClaveForm, RecuperarClaveForm
from .models import CodigoVerificacion


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RevisionAccesoTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.usuario = User.objects.create_user("revision_acceso", email="revision@example.test", password="Original-Segura-2026!")

    def iniciar(self, cliente=None):
        cliente = cliente or self.client
        cliente.post(reverse("login"), {"username": self.usuario.username, "password": "Original-Segura-2026!"})
        return CodigoVerificacion.objects.filter(usuario=self.usuario).latest("creado")

    def enlace(self):
        return reverse("restablecer_clave", kwargs={"uidb64": urlsafe_base64_encode(force_bytes(self.usuario.pk)), "token": default_token_generator.make_token(self.usuario)})

    def test_cambio_clave_invalida_paso_2fa_pendiente(self):
        codigo = self.iniciar()
        self.usuario.set_password("Diferente-Fiscal-2027!")
        self.usuario.save()
        respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": codigo.codigo})
        self.assertRedirects(respuesta, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cambio_correo_invalida_paso_2fa_pendiente(self):
        codigo = self.iniciar()
        self.usuario.email = "otro@example.test"
        self.usuario.save()
        self.assertRedirects(self.client.post(reverse("verificar_codigo"), {"codigo": codigo.codigo}), reverse("login"))

    def test_cancelar_descarta_paso_y_no_permite_volver(self):
        codigo = self.iniciar()
        self.assertRedirects(self.client.post(reverse("verificar_codigo"), {"cancelar": "1"}), reverse("login"))
        self.assertNotIn("sesion_2fa_pendiente", self.client.session)
        self.client.post(reverse("verificar_codigo"), {"codigo": codigo.codigo})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_codigo_de_otra_sesion_no_autentica_la_anterior(self):
        self.iniciar()
        otra = self.client_class()
        codigo_nuevo = self.iniciar(otra)
        self.client.post(reverse("verificar_codigo"), {"codigo": codigo_nuevo.codigo})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(otra.post(reverse("verificar_codigo"), {"codigo": codigo_nuevo.codigo}), reverse("home"))

    def test_codigos_malformados_tambien_agotan_el_limite(self):
        self.iniciar()
        for _ in range(10):
            respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": "abcdef"})
        self.assertRedirects(respuesta, reverse("login"))
        self.assertNotIn("sesion_2fa_pendiente", self.client.session)

    def test_recuperacion_no_permite_cambiar_cuenta_desactivada(self):
        enlace = self.enlace()
        self.usuario.is_active = False
        self.usuario.save()
        respuesta = self.client.get(enlace, follow=True)
        self.assertContains(respuesta, "El enlace ya no es válido")

    def test_codigo_expirado_no_autentica(self):
        codigo = self.iniciar()
        CodigoVerificacion.objects.filter(pk=codigo.pk).update(creado=timezone.now() - timedelta(minutes=6))
        self.client.post(reverse("verificar_codigo"), {"codigo": codigo.codigo})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_reenvio_muestra_espera_y_servidor_rechaza_adelanto(self):
        codigo = self.iniciar()
        respuesta = self.client.get(reverse("verificar_codigo"))
        self.assertGreater(respuesta.context["espera_reenvio"], 0)
        self.client.post(reverse("verificar_codigo"), {"reenviar": "1"})
        self.assertEqual(CodigoVerificacion.objects.filter(usuario=self.usuario).count(), 1)
        CodigoVerificacion.objects.filter(pk=codigo.pk).update(creado=timezone.now() - timedelta(seconds=61))
        self.client.post(reverse("verificar_codigo"), {"reenviar": "1"})
        self.assertEqual(CodigoVerificacion.objects.filter(usuario=self.usuario).count(), 2)
        self.assertEqual(self.client.get(reverse("verificar_codigo")).context["reenvios_restantes"], 2)

    def test_recuperacion_rechaza_correo_basura_sin_enviar(self):
        for correo in ("", " ", "a@@b.com", "<script>alert(1)</script>", "a\nb@example.com", "a" * 255 + "@b.com"):
            with self.subTest(correo=correo):
                self.assertFalse(RecuperarClaveForm({"email": correo}).is_valid())
        self.assertEqual(len(mail.outbox), 0)

    def test_salto_de_linea_y_simbolo_invisible_no_cumplen_complejidad(self):
        for clave in ("OtraClave123 ", "OtraClave123\n", "OtraClave123\t", "OtraClave123\u200b"):
            with self.subTest(clave=repr(clave)):
                form = RestablecerClaveForm(self.usuario, {"new_password1": clave, "new_password2": clave})
                self.assertFalse(form.is_valid())

    def test_claves_distintas_no_se_guardan(self):
        respuesta = self.client.get(self.enlace(), follow=True)
        destino = respuesta.redirect_chain[-1][0]
        self.client.post(destino, {"new_password1": "Nueva-Segura-2027!", "new_password2": "Diferente-Fiscal-2027!"})
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password("Original-Segura-2026!"))

    def test_enlace_expirado_no_se_acepta(self):
        with override_settings(PASSWORD_RESET_TIMEOUT=1):
            enlace = self.enlace()
            with patch.object(default_token_generator, "_now", return_value=default_token_generator._now() + timedelta(seconds=2)):
                self.assertContains(self.client.get(enlace, follow=True), "El enlace ya no es válido")

    def test_fallo_correo_no_revela_cuenta_en_recuperacion(self):
        with patch("usuarios.views.correos.enviar", side_effect=RuntimeError("fallo simulado")):
            respuesta = self.client.post(reverse("recuperar_clave"), {"email": self.usuario.email})
        self.assertRedirects(respuesta, reverse("recuperar_clave_enviado"))
