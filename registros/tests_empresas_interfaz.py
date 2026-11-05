from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import EmpresaAuditadaForm
from .models import EmpresaAuditada, HistorialCambio


class EmpresasInterfazTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_superuser("admin_empresa", password="prueba")
        self.client.force_login(self.usuario)
        self.empresa = EmpresaAuditada.objects.create(nombre="Peña & Hijos", nit="1234567")

    def test_edicion_invalida_conserva_datos_y_no_crea_historial(self):
        respuesta = self.client.post(reverse("registros:empresa_editar", args=[self.empresa.pk]), {
            "nombre": "Nombre nuevo", "nit": "abc", "categoria_cierre": "general",
            "contacto_email": "correo-invalido",
        })
        self.assertEqual(respuesta.status_code, 200)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.nombre, "Peña & Hijos")
        self.assertFalse(HistorialCambio.objects.filter(modelo="EmpresaAuditada", objeto_id=self.empresa.pk).exists())
        self.assertContains(respuesta, 'aria-invalid="true"')
        self.assertContains(respuesta, 'id="id_nit_errores"')
        self.assertContains(respuesta, 'value="Nombre nuevo"')

    def test_controles_en_nombres_no_se_normalizan_silenciosamente(self):
        for campo in ("contacto_nombre", "contador_nombre"):
            for valor in ("María\nPérez", "Ana\tLópez", "Juan\u200bPérez"):
                with self.subTest(campo=campo, valor=valor):
                    form = EmpresaAuditadaForm({"nombre": "Otra empresa", "categoria_cierre": "general", campo: valor})
                    self.assertFalse(form.is_valid())
                    self.assertIn(campo, form.errors)

    def test_campo_inicial_no_interfiere_al_editar(self):
        form = EmpresaAuditadaForm({"nombre": self.empresa.nombre, "nit": self.empresa.nit,
                                   "categoria_cierre": "general", "anio_gestion_inicial": "invalido"}, instance=self.empresa)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("anio_gestion_inicial", form.fields)

    def test_paginacion_conserva_busqueda_con_ampersand(self):
        for i in range(11):
            EmpresaAuditada.objects.create(nombre=f"Peña & Hijos {i}")
        respuesta = self.client.get(reverse("registros:empresas_lista"), {"q": "Peña & Hijos", "pagina": 1})
        self.assertContains(respuesta, "q=Pe%C3%B1a+%26+Hijos&pagina=2")

    def test_historial_escapa_valores_y_no_mezcla_empresas(self):
        otra = EmpresaAuditada.objects.create(nombre="Otra")
        for empresa, valor in ((self.empresa, "<script>alert(1)</script>"), (otra, "AJENO")):
            HistorialCambio.objects.create(modelo="EmpresaAuditada", objeto_id=empresa.pk,
                                          accion="edicion", campo="nombre", valor_nuevo=valor, usuario=self.usuario)
        respuesta = self.client.get(reverse("registros:empresa_historial", args=[self.empresa.pk]))
        self.assertContains(respuesta, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(respuesta, "AJENO")

    def test_listado_muestra_contacto_aunque_no_tenga_nombre(self):
        self.empresa.contacto_email = "representante@example.com"
        self.empresa.contador_telefono = "71234567"
        self.empresa.save()
        respuesta = self.client.get(reverse("registros:empresas_lista"))
        self.assertContains(respuesta, "representante@example.com")
        self.assertContains(respuesta, "71234567")
