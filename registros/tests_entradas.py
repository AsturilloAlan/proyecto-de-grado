from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from .forms import EmpresaAuditadaForm, GestionForm, CargaArchivoForm, _validar_excel_comprimido
from .models import EmpresaAuditada, Gestion, CargaArchivo
from .validadores import entero_positivo


class EntradasAdversarialesTests(TestCase):
    def empresa_form(self, **campos):
        return EmpresaAuditadaForm({"nombre": "3M Bolivia S.R.L.", "categoria_cierre": "general", **campos})

    def test_telefonos_invalidos(self):
        for valor in ("------", "((()))", "++++++", "12-34-", "123\n456", "123\t456", "123+456", "123)456(", "12345678901234567890", "１２３４５６"):
            with self.subTest(valor=valor):
                form = self.empresa_form(contacto_telefono=valor)
                self.assertFalse(form.is_valid())
                self.assertIn("contacto_telefono", form.errors)

    def test_telefonos_y_nombres_reales(self):
        for telefono in ("76543210", "+591 76543210", "(02) 1234567", "2-123456"):
            self.assertTrue(self.empresa_form(contacto_telefono=telefono).is_valid(), telefono)
        for nombre in ("María-José O’Connor", "José Pérez", "D'Angelo", "李明", ""):
            self.assertTrue(self.empresa_form(contacto_nombre=nombre).is_valid(), nombre)

    def test_representantes_invalidos(self):
        for valor in ("---", "...", "💥", "Juan123", "<script>alert()</script>", "Juan\nPérez", "Ana\u200b"):
            with self.subTest(valor=valor):
                form = self.empresa_form(contacto_nombre=valor)
                self.assertFalse(form.is_valid())
                self.assertIn("contacto_nombre", form.errors)

    def test_limites_campos_y_correo(self):
        for campo, valor in (("nombre", "a" * 201), ("nombre", "  "), ("nit", "1" * 16), ("nit", "1e10"), ("contacto_email", "a@@b.com"), ("categoria_cierre", "inventada")):
            with self.subTest(campo=campo, valor=valor):
                self.assertFalse(self.empresa_form(**{campo: valor}).is_valid())

    def test_gestiones_invalidas(self):
        for anio in ("²", "NaN", "Infinity", "-2025", "2025.5", "9" * 5000, "2101"):
            self.assertFalse(GestionForm({"anio": anio}).is_valid())
        self.assertFalse(GestionForm({"anio": "2025", "fecha_inicio": "2025-12-31", "fecha_fin": "2025-01-01"}).is_valid())

    def test_conversion_acotada(self):
        for valor in ("²", "١٢", "１２", "9" * 5000, "9223372036854775808", "-1", "0", "1e3", "NaN", "1 OR 1=1", "1.0"):
            self.assertIsNone(entero_positivo(valor))
        self.assertEqual(entero_positivo("12"), 12)

    def test_filtros_hostiles_no_producen_500(self):
        cache.clear()
        usuario = User.objects.create_user("revision", password="Clave-Segura123")
        usuario.groups.add(Group.objects.get(name="Auditor"))
        self.client.force_login(usuario)
        empresa = EmpresaAuditada.objects.create(nombre="Empresa de prueba")
        gestion = Gestion.objects.create(anio=2025, fecha_inicio="2025-01-01", fecha_fin="2025-12-31")
        carga = CargaArchivo.objects.create(empresa=empresa, gestion=gestion, usuario=usuario, estado="validado")
        for valor in ("²", "9" * 5000, "9223372036854775808", "NaN", "1e999999", "-1"):
            with self.subTest(valor=valor[:30]):
                for url in (reverse("registros:cargar"), reverse("reportes:indice"), reverse("registros:detalle_carga", args=[carga.pk])):
                    respuesta = self.client.get(url, {k: valor for k in ("empresa", "gestion", "fila", "relacionada", "desde_aviso", "monto_min")})
                    self.assertEqual(respuesta.status_code, 200, url)

    def test_archivos_vacios_y_renombrados(self):
        for nombre, contenido in (("datos.csv", b""), ("datos.exe", b"MZ"), ("datos.xlsx", b"<script>alert(1)</script>"), ("datos.pdf", b"archivo falso")):
            form = CargaArchivoForm(files={"archivo": SimpleUploadedFile(nombre, contenido)})
            self.assertFalse(form.is_valid())
            self.assertIn("archivo", form.errors)

    def test_zip_falso_y_expansion_excesiva(self):
        with self.assertRaises(ValidationError):
            _validar_excel_comprimido(BytesIO(b"PK\x03\x04resto"))
        archivo = BytesIO()
        with ZipFile(archivo, "w", ZIP_DEFLATED) as paquete:
            paquete.writestr("[Content_Types].xml", "x" * 1024)
            paquete.writestr("xl/workbook.xml", "x" * 1024)
        archivo.seek(0)
        # Límite reducido: verifica la defensa sin generar una bomba real.
        with patch("registros.forms.TAMANO_MAXIMO_EXCEL_EXPANDIDO", 1000), self.assertRaises(ValidationError):
            _validar_excel_comprimido(archivo)
        self.assertEqual(archivo.tell(), 0)


class CsvBinarioTests(TestCase):
    def test_binario_renombrado_a_csv_se_rechaza(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from registros.forms import _contenido_coincide

        self.assertFalse(_contenido_coincide(SimpleUploadedFile("x.csv", b"MZ\x90\x00\x03\x00binario"), ".csv"))
        self.assertFalse(_contenido_coincide(SimpleUploadedFile("x.csv", b"%PDF-1.7 ..."), ".csv"))
        self.assertTrue(_contenido_coincide(SimpleUploadedFile("x.csv", "fecha,cuenta\n01/01/2023,1101\n".encode()), ".csv"))



class LibroMayorTests(TestCase):
    def setUp(self):
        from datetime import date
        from decimal import Decimal

        from django.contrib.auth.models import Group, User

        from registros.models import CargaArchivo, CuentaContable, EmpresaAuditada, Gestion, RegistroContable

        self.usuario = User.objects.create_user(username="aud_mayor", password="x")
        self.usuario.groups.add(Group.objects.get_or_create(name="Auditor")[0])
        self.client.force_login(self.usuario)
        empresa = EmpresaAuditada.objects.create(nombre="Mayor SRL")
        gestion = Gestion.objects.create(anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        self.carga = CargaArchivo.objects.create(empresa=empresa, gestion=gestion, usuario=self.usuario,
                                                 archivo="x.csv", estado="validado")
        self.caja = CuentaContable.objects.create(empresa=empresa, codigo="1101", nombre="Caja", tipo="activo")
        ventas = CuentaContable.objects.create(empresa=empresa, codigo="4101", nombre="Ventas", tipo="ingreso")
        for fila, (dia, cuenta, debe, haber) in enumerate([
                (5, self.caja, "100", "0"), (3, self.caja, "50", "0"), (9, self.caja, "0", "30"),
                (5, ventas, "0", "100")], start=1):
            RegistroContable.objects.create(carga=self.carga, cuenta=cuenta, fecha=date(2023, 1, dia),
                                            debe=Decimal(debe), haber=Decimal(haber), fila_origen=fila)
        self.url = reverse("registros:libro_mayor", args=[self.carga.id])

    def test_movimientos_en_orden_de_fecha(self):
        respuesta = self.client.get(self.url, {"cuenta": self.caja.id})
        self.assertEqual([m.fila_origen for m in respuesta.context["pagina"]], [2, 1, 3])  # 3, 5 y 9 ene
        self.assertFalse(respuesta.context["hay_saldo_libro"])
        self.assertNotContains(respuesta, "Saldo según el libro")

    def test_muestra_el_saldo_solo_si_viene_en_el_libro(self):
        self.carga.registros.filter(fila_origen=1).update(saldo="150.00")
        respuesta = self.client.get(self.url, {"cuenta": self.caja.id})
        self.assertContains(respuesta, "Saldo según el libro")

    def test_saltar_a_una_fila_elige_su_cuenta(self):
        respuesta = self.client.get(self.url, {"fila": "4"})
        self.assertEqual(respuesta.context["cuenta"].codigo, "4101")
        self.assertContains(respuesta, 'class="fila-elegida"')

    def test_filtro_de_fechas(self):
        respuesta = self.client.get(self.url, {"cuenta": self.caja.id, "desde": "2023-01-05", "hasta": "1900-01-01"})
        self.assertEqual([m.fila_origen for m in respuesta.context["pagina"]], [1, 3])

    def test_otra_carga_o_parametros_raros_no_rompen(self):
        self.assertEqual(self.client.get(self.url, {"cuenta": "abc", "fila": "-1", "pagina": "zz"}).status_code, 200)
        self.assertEqual(self.client.get(reverse("registros:libro_mayor", args=[99999])).status_code, 404)



class ContadorEmpresaTests(TestCase):
    def test_nombre_del_contador_se_valida(self):
        from registros.forms import EmpresaAuditadaForm
        base = {"nombre": "Empresa C", "categoria_cierre": "general"}
        bueno = EmpresaAuditadaForm(data={**base, "contador_nombre": "  Lic.  Ana  Pérez "})
        self.assertTrue(bueno.is_valid(), bueno.errors)
        self.assertEqual(bueno.cleaned_data["contador_nombre"], "Lic. Ana Pérez")
        malo = EmpresaAuditadaForm(data={**base, "contador_nombre": "Ana123"})
        self.assertFalse(malo.is_valid())
        self.assertIn("contador_nombre", malo.errors)
