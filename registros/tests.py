"""
Pruebas automatizadas de la app registros.

Cubren:
- RF-09 (uso interno): CRUD de empresas clientes, solo Administrador.
- RF-01/RF-02: carga y validación de registros contables.

Se ejecutan con:
    python manage.py test registros
"""
import shutil
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import CargaArchivoForm, EmpresaAuditadaForm
from .models import (
    CargaArchivo,
    CuentaContable,
    EmpresaAuditada,
    ErrorValidacion,
    Gestion,
    HistorialCambio,
    RegistroContable,
)
from .services import procesar_carga

MEDIA_TEMPORAL = tempfile.mkdtemp()


def _crear_administrador(username="admin"):
    Group.objects.get_or_create(name="Administrador")
    usuario = User.objects.create_user(username=username, password="Clave-Segura123")
    usuario.groups.add(Group.objects.get(name="Administrador"))
    return usuario


class EmpresaAuditadaFormTests(TestCase):
    def test_nombre_vacio_o_solo_espacios_no_es_valido(self):
        form = EmpresaAuditadaForm(data={"nombre": "   ", "nit": "123"})
        self.assertFalse(form.is_valid())
        self.assertIn("nombre", form.errors)


class EmpresasCrudTests(TestCase):
    """RF-09: gestión de empresas clientes, restringida a Administrador."""

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")

    def test_crear_empresa(self):
        respuesta = self.client.post(
            reverse("registros:empresa_crear"),
            {
                "nombre": "Cooperativa Santa Rita R.L.",
                "nit": "123456",
                "rubro": "Financiero",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
            },
        )
        self.assertRedirects(respuesta, reverse("registros:empresas_lista"))
        self.assertTrue(
            EmpresaAuditada.objects.filter(nombre="Cooperativa Santa Rita R.L.").exists()
        )

    def test_editar_empresa(self):
        empresa = EmpresaAuditada.objects.create(nombre="La razon", nit="8880320")
        respuesta = self.client.post(
            reverse("registros:empresa_editar", args=[empresa.id]),
            {
                "nombre": "La Razón S.R.L.",
                "nit": "8880320",
                "rubro": "",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
            },
        )
        self.assertRedirects(respuesta, reverse("registros:empresas_lista"))
        empresa.refresh_from_db()
        self.assertEqual(empresa.nombre, "La Razón S.R.L.")

    def test_busqueda_filtra_por_nombre_o_nit(self):
        EmpresaAuditada.objects.create(nombre="ST&S Auditores", nit="111")
        EmpresaAuditada.objects.create(nombre="Cooperativa Santa Rita", nit="222")
        respuesta = self.client.get(reverse("registros:empresas_lista"), {"q": "Rita"})
        nombres = [e.nombre for e in respuesta.context["empresas"]]
        self.assertEqual(nombres, ["Cooperativa Santa Rita"])

    def test_paginacion_muestra_maximo_10_por_pagina(self):
        for i in range(15):
            EmpresaAuditada.objects.create(nombre=f"Empresa {i:02d}")
        respuesta = self.client.get(reverse("registros:empresas_lista"))
        self.assertEqual(len(respuesta.context["empresas"]), 10)


class HistorialCambioTests(TestCase):
    """Pista de auditoría: cada creación/edición de una empresa debe quedar
    registrada con usuario, fecha y campo modificado."""

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")

    def test_crear_empresa_registra_historial_de_creacion(self):
        self.client.post(
            reverse("registros:empresa_crear"),
            {
                "nombre": "Cooperativa Santa Rita R.L.",
                "nit": "123456",
                "rubro": "",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
            },
        )
        empresa = EmpresaAuditada.objects.get(nombre="Cooperativa Santa Rita R.L.")
        cambio = HistorialCambio.objects.get(
            modelo="EmpresaAuditada", objeto_id=empresa.id
        )
        self.assertEqual(cambio.accion, "creacion")
        self.assertEqual(cambio.usuario, self.admin)

    def test_editar_empresa_registra_cambios_campo_por_campo(self):
        empresa = EmpresaAuditada.objects.create(nombre="La razon", nit="8880320")
        self.client.post(
            reverse("registros:empresa_editar", args=[empresa.id]),
            {
                "nombre": "La Razón S.R.L.",
                "nit": "8880320",
                "rubro": "",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
            },
        )
        cambios = HistorialCambio.objects.filter(
            modelo="EmpresaAuditada", objeto_id=empresa.id, accion="edicion"
        )
        self.assertEqual(cambios.count(), 1)
        cambio = cambios.first()
        self.assertEqual(cambio.campo, "nombre")
        self.assertEqual(cambio.valor_anterior, "La razon")
        self.assertEqual(cambio.valor_nuevo, "La Razón S.R.L.")
        self.assertEqual(cambio.usuario, self.admin)

    def test_editar_sin_cambios_no_agrega_historial(self):
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X", nit="111")
        self.client.post(
            reverse("registros:empresa_editar", args=[empresa.id]),
            {
                "nombre": "Empresa X",
                "nit": "111",
                "rubro": "",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
            },
        )
        self.assertFalse(
            HistorialCambio.objects.filter(
                modelo="EmpresaAuditada", objeto_id=empresa.id, accion="edicion"
            ).exists()
        )

    def test_vista_historial_lista_los_cambios(self):
        empresa = EmpresaAuditada.objects.create(nombre="Empresa Y")
        HistorialCambio.objects.create(
            modelo="EmpresaAuditada",
            objeto_id=empresa.id,
            objeto_descripcion="Empresa Y",
            accion="creacion",
            usuario=self.admin,
        )
        respuesta = self.client.get(
            reverse("registros:empresa_historial", args=[empresa.id])
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.context["cambios"]), 1)


class CargaArchivoFormTests(TestCase):
    """RF-01: solo se aceptan archivos .xlsx, .xls o .csv."""

    def setUp(self):
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(anio=2023)

    def test_extension_no_permitida_es_rechazada(self):
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={"archivo": SimpleUploadedFile("registros.pdf", b"contenido")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)

    def test_extension_csv_es_aceptada(self):
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={
                "archivo": SimpleUploadedFile(
                    "registros.csv", b"fecha,cuenta,debe,haber\n"
                )
            },
        )
        self.assertTrue(form.is_valid())

    def test_archivo_demasiado_pesado_es_rechazado(self):
        """Protección básica: un archivo más pesado que el límite (20 MB)
        se rechaza antes de intentar procesarlo."""
        contenido_grande = b"0" * (21 * 1024 * 1024)
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={"archivo": SimpleUploadedFile("registros.csv", contenido_grande)},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class ProcesarCargaTests(TestCase):
    """RF-02: validación fila por fila del archivo cargado."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)

    def setUp(self):
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(anio=2023)
        self.usuario = User.objects.create_user(
            username="auditor", password="Clave-Segura123"
        )

    def _crear_carga(self, contenido_csv):
        archivo = SimpleUploadedFile("carga.csv", contenido_csv.encode("utf-8"))
        return CargaArchivo.objects.create(
            archivo=archivo,
            usuario=self.usuario,
            empresa=self.empresa,
            gestion=self.gestion,
        )

    def test_filas_validas_se_guardan_y_carga_queda_validada(self):
        csv = (
            "fecha,cuenta,glosa,debe,haber\n"
            "01/01/2023,1001,Pago proveedor,100,0\n"
            "02/01/2023,2001,Cobro cliente,0,150\n"
        )
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "validado")
        self.assertEqual(carga.total_registros, 2)
        self.assertEqual(carga.registros_validos, 2)
        self.assertEqual(carga.registros_con_error, 0)
        self.assertEqual(RegistroContable.objects.filter(carga=carga).count(), 2)

    def test_fila_sin_debe_ni_haber_se_marca_como_error(self):
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,1001,Sin monto,0,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_errores")
        self.assertEqual(carga.registros_con_error, 1)
        self.assertTrue(
            ErrorValidacion.objects.filter(carga=carga, campo="debe/haber").exists()
        )

    def test_columnas_faltantes_se_reportan_como_error_de_archivo(self):
        csv = "columna_a,columna_b\n1,2\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_errores")
        self.assertTrue(
            ErrorValidacion.objects.filter(carga=carga, campo="archivo").exists()
        )

    def test_cuenta_nueva_se_crea_automaticamente_en_el_catalogo(self):
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,9999,Cuenta nueva,50,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertTrue(CuentaContable.objects.filter(codigo="9999").exists())
        # La fila se guarda igual (no es un error real, solo un aviso):
        self.assertEqual(carga.estado, "validado")
        self.assertEqual(carga.registros_con_error, 0)
        aviso = ErrorValidacion.objects.get(carga=carga, campo="cuenta")
        self.assertEqual(aviso.tipo, "aviso")

    def test_formato_libro_mayor_agrupado_por_cuenta(self):
        """Formato real de libro mayor exportado por un sistema contable:
        filas de título antes del encabezado, sin columna 'cuenta' por
        fila (se agrupa en bloques), y con subtotales intercalados que
        deben ignorarse en vez de tratarse como cuentas nuevas."""
        csv = (
            "EMPRESA DEMO S.R.L.\n"
            "LIBRO MAYOR\n"
            "FECHA,NUMERO,DETALLE,DEBE,HABER,SALDO\n"
            "1-1-1-01-01 CAJA MONEDA NACIONAL\n"
            "01/01/2023,AD001,Apertura,500,0,500\n"
            "02/01/2023,CI001,Venta del dia,200,0,700\n"
            "Total 01/2023,,,700,0,\n"
            "2-1-2-01 CUENTAS POR PAGAR\n"
            "03/01/2023,CE001,Pago proveedor,0,150,150\n"
        )
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "validado")
        self.assertEqual(carga.total_registros, 3)
        self.assertEqual(carga.registros_validos, 3)
        self.assertTrue(CuentaContable.objects.filter(codigo="1-1-1-01-01").exists())
        self.assertTrue(CuentaContable.objects.filter(codigo="2-1-2-01").exists())
        self.assertEqual(
            RegistroContable.objects.filter(cuenta__codigo="1-1-1-01-01").count(), 2
        )
        self.assertEqual(
            RegistroContable.objects.filter(cuenta__codigo="2-1-2-01").count(), 1
        )

    def test_transaccion_sin_cuenta_previa_se_marca_como_error(self):
        """Si el archivo agrupado trae una fila de transacción antes de
        cualquier encabezado de cuenta, no debe asignarse a ciegas — se
        registra como error para que el auditor lo revise."""
        csv = (
            "LIBRO MAYOR\n"
            "FECHA,NUMERO,DETALLE,DEBE,HABER,SALDO\n"
            "01/01/2023,AD001,Transaccion huerfana,500,0,500\n"
        )
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_errores")
        self.assertTrue(
            ErrorValidacion.objects.filter(carga=carga, campo="cuenta").exists()
        )
