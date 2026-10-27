from datetime import date
from django.test import TestCase
from .forms import EmpresaAuditadaForm, AnulacionForm, GestionForm


class RevisionFormulariosTests(TestCase):
    def empresa(self, **cambios):
        return EmpresaAuditadaForm({"nombre": "Comercial Andina S.R.L.", "categoria_cierre": "general", **cambios})

    def test_texto_invisible_y_control_no_se_guardan(self):
        for campo in ("nombre", "rubro"):
            for valor in ("Empresa\u200b", "Empresa\x1b[0m", "Empresa\nOtra"):
                with self.subTest(campo=campo, valor=repr(valor)):
                    form = self.empresa(**{campo: valor})
                    self.assertFalse(form.is_valid())
                    self.assertIn(campo, form.errors)

    def test_nit_separadores_sin_digitos_adyacentes(self):
        for nit in ("...12345", "12345--", "12..345", "12 - 345"):
            with self.subTest(nit=nit):
                form = self.empresa(nit=nit)
                self.assertFalse(form.is_valid())
                self.assertIn("nit", form.errors)

    def test_telefono_parentesis_vacios(self):
        form = self.empresa(contacto_telefono="()1234567")
        self.assertFalse(form.is_valid())
        self.assertIn("contacto_telefono", form.errors)

    def test_anio_inicial_respeta_limite_de_gestiones(self):
        form = self.empresa(anio_gestion_inicial=date.today().year + 2)
        self.assertFalse(form.is_valid())
        self.assertIn("anio_gestion_inicial", form.errors)

    def test_anulacion_no_acepta_solo_puntuacion_o_invisibles(self):
        for detalle in ("..........", "\u200b" * 12, "1234567890"):
            with self.subTest(detalle=repr(detalle)):
                self.assertFalse(AnulacionForm({"tipo_motivo": "otro", "detalle": detalle}).is_valid())

    def test_datos_legitimos_con_tildes_separadores_y_fechas(self):
        form = self.empresa(nombre="Peña & Hijos S.A.", nit="123.456-7", contacto_nombre="María O’Connor", contacto_telefono="+591 (2) 1234567", rubro="Importación / exportación")
        self.assertTrue(form.is_valid(), form.errors)
        motivo = AnulacionForm({"tipo_motivo": "otro", "detalle": "Faltan asientos del mes de enero."})
        self.assertTrue(motivo.is_valid(), motivo.errors)
        gestion = GestionForm({"anio": 2024, "fecha_inicio": "2024-01-01", "fecha_fin": "2024-12-31"})
        self.assertTrue(gestion.is_valid(), gestion.errors)
