"""Pruebas automatizadas de la app usuarios."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import CodigoVerificacion


class LoginTests(TestCase):
    """RF-07: inicio de sesión."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="auditor1", password="Clave-Segura123", email="auditor1@sts.com"
        )

    def _codigo_pendiente_de(self, usuario):
        return (
            CodigoVerificacion.objects.filter(usuario=usuario, usado=False)
            .order_by("-creado")
            .first()
            .codigo
        )

    def _iniciar_sesion_completa(self, username, password):
        """Hace login + resuelve el 2FA con el código real que se
        'envió' (capturado del backend de correo de pruebas)."""
        respuesta = self.client.post(
            reverse("login"), {"username": username, "password": password}
        )
        self.assertRedirects(respuesta, reverse("verificar_codigo"))
        codigo = self._codigo_pendiente_de(self.usuario)
        return self.client.post(reverse("verificar_codigo"), {"codigo": codigo})

    def test_login_con_credenciales_validas_pide_codigo_de_verificacion(self):
        respuesta = self.client.post(
            reverse("login"), {"username": "auditor1", "password": "Clave-Segura123"}
        )
        self.assertRedirects(respuesta, reverse("verificar_codigo"))
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_codigo_de_verificacion_llega_por_correo(self):
        self.client.post(
            reverse("login"), {"username": "auditor1", "password": "Clave-Segura123"}
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("auditor1@sts.com", mail.outbox[0].to)

    def test_codigo_correcto_completa_el_login_y_va_a_inicio(self):
        respuesta = self._iniciar_sesion_completa("auditor1", "Clave-Segura123")
        self.assertRedirects(respuesta, reverse("home"))

    def test_codigo_incorrecto_no_autentica(self):
        self.client.post(
            reverse("login"), {"username": "auditor1", "password": "Clave-Segura123"}
        )
        respuesta = self.client.post(
            reverse("verificar_codigo"), {"codigo": "000000"}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_reenviar_codigo_genera_uno_nuevo_e_invalida_el_anterior(self):
        self.client.post(
            reverse("login"), {"username": "auditor1", "password": "Clave-Segura123"}
        )
        codigo_original = self._codigo_pendiente_de(self.usuario)
        # Hay una espera mínima entre reenvíos: se simula que ya pasó.
        CodigoVerificacion.objects.filter(usuario=self.usuario).update(
            creado=timezone.now() - timedelta(minutes=2)
        )
        self.client.post(reverse("verificar_codigo"), {"reenviar": "1"})
        respuesta = self.client.post(
            reverse("verificar_codigo"), {"codigo": codigo_original}
        )
        # El código original ya fue invalidado por `generar_para`, así
        # que no debe autenticar aunque sea el que se guardó primero.
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)

    def test_usuario_sin_correo_no_puede_completar_login(self):
        User.objects.create_user(username="sin_correo", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("login"), {"username": "sin_correo", "password": "Clave-Segura123"}
        )
        self.assertRedirects(respuesta, reverse("login"))

    def test_login_ignora_next_y_siempre_va_a_inicio(self):
        """Si el usuario llegó al login porque intentó abrir una URL protegida sin
        sesión (lo que agrega `?next=...`), al completar el login (usuario +
        contraseña + código) debe aterrizar en el inicio de todos modos, y no en
        esa URL intermedia.
        """
        url_con_next = reverse("login") + "?next=/registros/empresas/"
        self.client.post(
            url_con_next, {"username": "auditor1", "password": "Clave-Segura123"}
        )
        codigo = self._codigo_pendiente_de(self.usuario)
        respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": codigo})
        self.assertRedirects(respuesta, reverse("home"))

    def test_login_con_credenciales_invalidas_no_autentica(self):
        respuesta = self.client.post(
            reverse("login"), {"username": "auditor1", "password": "incorrecta"}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["user"].is_authenticated)

    def test_usuario_con_espacios_alrededor_se_normaliza(self):
        """Django recorta los espacios del campo usuario (UsernameField con
        strip=True) antes de autenticar, tanto al loguear como al crear cuentas.
        """
        respuesta = self.client.post(
            reverse("login"),
            {"username": "  auditor1  ", "password": "Clave-Segura123"},
        )
        self.assertRedirects(respuesta, reverse("verificar_codigo"))

    def test_usuario_no_autenticado_es_redirigido_al_login(self):
        respuesta = self.client.get(reverse("home"))
        self.assertRedirects(respuesta, f"{reverse('login')}?next={reverse('home')}")

    def test_superusuario_es_redirigido_al_panel_de_administracion(self):
        superusuario = User.objects.create_superuser(
            username="tecnico1", password="Clave-Segura123", email="tecnico1@example.com"
        )
        self.client.post(
            reverse("login"), {"username": "tecnico1", "password": "Clave-Segura123"}
        )
        codigo = (
            CodigoVerificacion.objects.filter(usuario=superusuario, usado=False)
            .order_by("-creado")
            .first()
            .codigo
        )
        respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": codigo})
        self.assertRedirects(respuesta, reverse("admin:index"))


class BloqueoPorIntentosTests(TestCase):
    """RNF-03: protección básica contra fuerza bruta en el login."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="auditor4", password="Clave-Segura123", email="auditor4@sts.com"
        )

    def test_se_bloquea_tras_varios_intentos_fallidos(self):
        for _ in range(5):
            self.client.post(
                reverse("login"), {"username": "auditor4", "password": "incorrecta"}
            )
        respuesta = self.client.post(
            reverse("login"),
            {"username": "auditor4", "password": "Clave-Segura123"},
            follow=True,
        )
        mensajes = [str(m) for m in respuesta.context["messages"]]
        self.assertTrue(any("Demasiados intentos" in m for m in mensajes))
        self.assertFalse(respuesta.context["user"].is_authenticated)

    def test_login_correcto_resetea_el_contador_de_intentos(self):
        self.client.post(
            reverse("login"), {"username": "auditor4", "password": "incorrecta"}
        )
        self.client.post(
            reverse("login"), {"username": "auditor4", "password": "Clave-Segura123"}
        )
        # El intento fallido anterior no debe acumularse tras un login
        # correcto: tres fallidos más no deberían alcanzar el límite de 5.
        for _ in range(3):
            respuesta = self.client.post(
                reverse("login"), {"username": "auditor4", "password": "incorrecta"}
            )
        mensajes = [str(m) for m in respuesta.context.get("messages", [])] if respuesta.context else []
        self.assertFalse(any("Demasiados intentos" in m for m in mensajes))


class GestionUsuariosTests(TestCase):
    """Panel de gestión de usuarios (solo Administrador), agregado a pedido de la
    tutora para que la Socia Principal no dependa de /admin/ de Django para
    altas/bajas de cuentas.
    """

    def setUp(self):
        Group.objects.get_or_create(name="Administrador")
        Group.objects.get_or_create(name="Auditor")

        self.administrador = User.objects.create_user(
            username="admin2", password="Clave-Segura123", email="admin2@sts.com"
        )
        self.administrador.groups.add(Group.objects.get(name="Administrador"))

        self.auditor = User.objects.create_user(
            username="auditor5", password="Clave-Segura123", email="auditor5@sts.com"
        )
        self.auditor.groups.add(Group.objects.get(name="Auditor"))

    def test_auditor_no_puede_ver_el_panel_de_usuarios(self):
        self.client.login(username="auditor5", password="Clave-Segura123")
        respuesta = self.client.get(reverse("usuarios_lista"), follow=True)
        self.assertRedirects(respuesta, reverse("home"))

    def test_administrador_puede_crear_usuario_con_rol(self):
        self.client.login(username="admin2", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("usuario_crear"),
            {
                "username": "nuevo_auditor",
                "email": "nuevo@sts.com",
                "rol": "Auditor",
                "password1": "OtraClave-456",
                "password2": "OtraClave-456",
            },
        )
        self.assertRedirects(respuesta, reverse("usuarios_lista"))
        creado = User.objects.get(username="nuevo_auditor")
        self.assertTrue(creado.groups.filter(name="Auditor").exists())

    def test_administrador_puede_cambiar_rol_de_otro_usuario(self):
        self.client.login(username="admin2", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("usuario_editar", args=[self.auditor.id]),
            {"email": self.auditor.email, "is_active": "on", "rol": "Administrador"},
        )
        self.assertRedirects(respuesta, reverse("usuarios_lista"))
        self.auditor.refresh_from_db()
        self.assertTrue(self.auditor.groups.filter(name="Administrador").exists())
        self.assertFalse(self.auditor.groups.filter(name="Auditor").exists())

    def test_administrador_no_puede_editarse_a_si_mismo_desde_el_panel(self):
        self.client.login(username="admin2", password="Clave-Segura123")
        respuesta = self.client.get(
            reverse("usuario_editar", args=[self.administrador.id]), follow=True
        )
        self.assertRedirects(respuesta, reverse("usuarios_lista"))

    def test_no_se_puede_crear_usuario_con_correo_ya_registrado(self):
        self.client.login(username="admin2", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("usuario_crear"),
            {
                "username": "otro_mas",
                "email": "auditor5@sts.com",  # ya es el correo de self.auditor
                "rol": "Auditor",
                "password1": "OtraClave-456",
                "password2": "OtraClave-456",
            },
        )
        self.assertEqual(respuesta.status_code, 200)  # no redirige: el form no es válido
        self.assertFalse(User.objects.filter(username="otro_mas").exists())

    def test_no_se_puede_editar_usuario_con_correo_de_otra_cuenta(self):
        self.client.login(username="admin2", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("usuario_editar", args=[self.auditor.id]),
            {"email": "admin2@sts.com", "is_active": "on", "rol": "Auditor"},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.auditor.refresh_from_db()
        self.assertEqual(self.auditor.email, "auditor5@sts.com")


class RolRequeridoTests(TestCase):
    """RF-07: acceso restringido por rol a las vistas de Administrador."""

    def setUp(self):
        Group.objects.get_or_create(name="Administrador")
        Group.objects.get_or_create(name="Auditor")

        self.administrador = User.objects.create_user(
            username="admin1", password="Clave-Segura123"
        )
        self.administrador.groups.add(Group.objects.get(name="Administrador"))

        self.auditor = User.objects.create_user(
            username="auditor2", password="Clave-Segura123"
        )
        self.auditor.groups.add(Group.objects.get(name="Auditor"))

        self.superusuario = User.objects.create_superuser(
            username="root1", password="Clave-Segura123", email="root1@example.com"
        )

    def test_administrador_puede_entrar_a_empresas(self):
        self.client.login(username="admin1", password="Clave-Segura123")
        respuesta = self.client.get(reverse("registros:empresas_lista"))
        self.assertEqual(respuesta.status_code, 200)

    def test_auditor_es_redirigido_con_mensaje_en_vez_de_error_403(self):
        self.client.login(username="auditor2", password="Clave-Segura123")
        respuesta = self.client.get(reverse("registros:empresas_lista"), follow=True)
        self.assertRedirects(respuesta, reverse("home"))
        mensajes = [str(m) for m in respuesta.context["messages"]]
        self.assertTrue(any("rol necesario" in m for m in mensajes))

    def test_superusuario_entra_sin_pertenecer_al_grupo(self):
        self.client.login(username="root1", password="Clave-Segura123")
        respuesta = self.client.get(reverse("registros:empresas_lista"))
        self.assertEqual(respuesta.status_code, 200)


class SeguridadDeSesionTests(TestCase):
    """RNF-03: la información contable/financiera es sensible; la sesión
    debe expirar en vez de quedar abierta indefinidamente."""

    def test_configuracion_de_expiracion_de_sesion(self):
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 30)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertTrue(settings.CSRF_COOKIE_HTTPONLY)


class PerfilTests(TestCase):
    """Perfil de usuario: la página no debe fallar aunque el usuario todavía no tenga
    un PerfilUsuario creado, y cambiar la contraseña debe exigir la contraseña
    actual.
    """

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="auditor3", password="Clave-Segura123"
        )
        self.client.login(username="auditor3", password="Clave-Segura123")

    def test_perfil_carga_sin_avatar_previo(self):
        respuesta = self.client.get(reverse("perfil"))
        self.assertEqual(respuesta.status_code, 200)

    def test_cambiar_clave_con_clave_actual_incorrecta_falla(self):
        respuesta = self.client.post(
            reverse("cambiar_clave"),
            {
                "old_password": "incorrecta",
                "new_password1": "OtraClave-456",
                "new_password2": "OtraClave-456",
            },
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(
            self.client.login(username="auditor3", password="OtraClave-456")
        )

    def test_cambiar_clave_correctamente(self):
        respuesta = self.client.post(
            reverse("cambiar_clave"),
            {
                "old_password": "Clave-Segura123",
                "new_password1": "OtraClave-456",
                "new_password2": "OtraClave-456",
            },
        )
        self.assertRedirects(respuesta, reverse("cambiar_clave_hecho"))
        self.assertTrue(
            self.client.login(username="auditor3", password="OtraClave-456")
        )

    def test_cambiar_clave_notifica_por_correo_y_registra_historial_sin_guardar_la_clave(self):
        from registros.models import HistorialCambio

        self.usuario.email = "auditor3@sts.com"
        self.usuario.save()

        respuesta = self.client.post(
            reverse("cambiar_clave"),
            {
                "old_password": "Clave-Segura123",
                "new_password1": "OtraClave-456",
                "new_password2": "OtraClave-456",
            },
        )
        self.assertRedirects(respuesta, reverse("cambiar_clave_hecho"))

        # Aviso por correo al dueño de la cuenta.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["auditor3@sts.com"])
        self.assertIn("contraseña", mail.outbox[0].subject.lower())

        # Constancia en el historial de auditoría, sin guardar la clave real.
        entrada = HistorialCambio.objects.get(
            modelo="User", objeto_id=self.usuario.pk, campo="password"
        )
        self.assertNotIn("OtraClave-456", entrada.valor_nuevo)
        self.assertNotIn("Clave-Segura123", entrada.valor_anterior)

    def test_cambiar_clave_sin_correo_no_falla_aunque_no_se_pueda_avisar(self):
        # self.usuario no tiene email (ver setUp): el cambio debe seguir
        # funcionando igual, solo sin enviar el correo de aviso.
        respuesta = self.client.post(
            reverse("cambiar_clave"),
            {
                "old_password": "Clave-Segura123",
                "new_password1": "OtraClave-456",
                "new_password2": "OtraClave-456",
            },
        )
        self.assertRedirects(respuesta, reverse("cambiar_clave_hecho"))
        self.assertEqual(len(mail.outbox), 0)

    def test_elegir_avatar_predefinido(self):
        respuesta = self.client.post(
            reverse("perfil"), {"accion": "avatar", "avatar_preset": "azul"}
        )
        self.assertRedirects(respuesta, reverse("perfil"))
        self.usuario.perfil.refresh_from_db()
        self.assertEqual(self.usuario.perfil.avatar_preset, "azul")

    def test_editar_correo_desde_perfil(self):
        respuesta = self.client.post(
            reverse("perfil"),
            {"accion": "datos", "email": "auditor3@sts.com", "clave_actual": "Clave-Segura123"},
        )
        self.assertRedirects(respuesta, reverse("perfil"))
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, "auditor3@sts.com")

    def test_no_se_puede_editar_correo_a_uno_ya_usado_por_otra_cuenta(self):
        User.objects.create_user(
            username="otro_usuario", password="Clave-Segura123", email="ocupado@sts.com"
        )
        respuesta = self.client.post(
            reverse("perfil"), {"accion": "datos", "email": "ocupado@sts.com"}
        )
        self.assertEqual(respuesta.status_code, 200)  # no redirige: el form no es válido
        self.usuario.refresh_from_db()
        self.assertNotEqual(self.usuario.email, "ocupado@sts.com")


class Revision2FAYCorreoTests(TestCase):
    """Hallazgos H03, H13 y H14 del informe de revisión externa."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="tecnico", password="Clave-Segura123", email="tecnico@sts.com",
            is_staff=True, is_superuser=True,
        )

    def test_admin_login_no_permite_entrar_sin_segundo_factor(self):
        respuesta = self.client.post(
            "/admin/login/", {"username": "tecnico", "password": "Clave-Segura123"}
        )
        self.assertFalse(respuesta.wsgi_request.user.is_authenticated)
        respuesta = self.client.get("/admin/")
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("login"), respuesta["Location"])

    def test_reenvio_inmediato_se_rechaza(self):
        self.client.post(reverse("login"), {"username": "tecnico", "password": "Clave-Segura123"})
        self.client.post(reverse("verificar_codigo"), {"reenviar": "1"})
        self.assertEqual(CodigoVerificacion.objects.filter(usuario=self.usuario).count(), 1)

    def test_demasiados_fallos_cierran_el_paso_de_verificacion(self):
        self.client.post(reverse("login"), {"username": "tecnico", "password": "Clave-Segura123"})
        for _ in range(10):
            respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": "000000"})
        self.assertRedirects(respuesta, reverse("login"))
        self.assertNotIn("sesion_2fa_pendiente", self.client.session)

    def test_codigo_no_numerico_no_rompe_la_pagina(self):
        self.client.post(reverse("login"), {"username": "tecnico", "password": "Clave-Segura123"})
        respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": "ñññññ1"})
        self.assertEqual(respuesta.status_code, 200)

    def test_correo_vacio_no_es_valido_en_perfil(self):
        from .forms import DatosCuentaForm
        form = DatosCuentaForm(
            {"email": "", "clave_actual": "Clave-Segura123"}, instance=self.usuario
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_cambiar_correo_exige_contrasena_y_avisa_al_anterior(self):
        self.client.login(username="tecnico", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("perfil"),
            {"accion": "datos", "email": "nuevo@sts.com", "clave_actual": "incorrecta"},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, "tecnico@sts.com")

        self.client.post(
            reverse("perfil"),
            {"accion": "datos", "email": "nuevo@sts.com", "clave_actual": "Clave-Segura123"},
        )
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, "nuevo@sts.com")
        self.assertTrue(any("tecnico@sts.com" in m.to for m in mail.outbox))


class BloqueoLoginContadorTests(TestCase):
    def setUp(self):
        User.objects.create_user(username="auditor9", password="Clave-Segura123", email="a9@sts.com")

    def test_al_bloquearse_muestra_el_contador(self):
        for _ in range(5):
            respuesta = self.client.post(
                reverse("login"), {"username": "auditor9", "password": "incorrecta"}, follow=True
            )
        self.assertGreater(respuesta.context["segundos_bloqueo"], 0)
        self.assertContains(respuesta, 'id="panel-bloqueo"')
        self.assertContains(respuesta, 'class="bloqueado"')

    def test_sin_bloqueo_no_hay_contador(self):
        respuesta = self.client.get(reverse("login"))
        self.assertNotIn("segundos_bloqueo", respuesta.context)
        self.assertNotContains(respuesta, 'id="panel-bloqueo"')
