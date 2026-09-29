"""Pruebas automatizadas de la app registros."""
import shutil
import tempfile
from datetime import date

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import CargaArchivoForm, EmpresaAuditadaForm, GestionForm
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

    def test_nombre_puramente_numerico_no_es_valido(self):
        form = EmpresaAuditadaForm(data={"nombre": "154654651", "nit": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("nombre", form.errors)

    def test_nombre_con_numeros_y_letras_es_valido(self):
        # No se exige que sea SOLO letras: nombres reales de empresa pueden
        # traer números (ej. "3M Bolivia S.R.L.").
        form = EmpresaAuditadaForm(
            data={"nombre": "3M Bolivia S.R.L.", "nit": "", "categoria_cierre": "general"}
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_contacto_nombre_con_numeros_no_es_valido(self):
        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa X", "nit": "", "contacto_nombre": "s45345343"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("contacto_nombre", form.errors)

    def test_nit_con_letras_o_muy_corto_no_es_valido(self):
        form = EmpresaAuditadaForm(data={"nombre": "Empresa X", "nit": "ABC123"})
        self.assertFalse(form.is_valid())
        self.assertIn("nit", form.errors)

        form = EmpresaAuditadaForm(data={"nombre": "Empresa X", "nit": "123"})
        self.assertFalse(form.is_valid())
        self.assertIn("nit", form.errors)

    def test_nit_vacio_es_valido_por_ser_opcional(self):
        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa X", "nit": "", "categoria_cierre": "general"}
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_nit_valido_con_y_sin_digito_verificador_es_aceptado(self):
        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa X", "nit": "1023456021", "categoria_cierre": "general"}
        )
        self.assertTrue(form.is_valid(), form.errors)

        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa Y", "nit": "1023456021-0", "categoria_cierre": "general"}
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_nit_con_puntos_como_separador_es_aceptado(self):
        """Formato real usado en el sistema: el NIT se escribe con puntos de
        separación y dígito verificador.
        """
        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa Z", "nit": "619.7772.443-0", "categoria_cierre": "general"}
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_nit_de_solo_separadores_o_demasiado_largo_no_es_valido(self):
        form = EmpresaAuditadaForm(data={"nombre": "Empresa X", "nit": "..."})
        self.assertFalse(form.is_valid())
        self.assertIn("nit", form.errors)

        form = EmpresaAuditadaForm(
            data={"nombre": "Empresa X", "nit": "12345678901234567890"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("nit", form.errors)


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
                "categoria_cierre": "general",
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
                "categoria_cierre": "general",
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

    def test_crear_empresa_con_anio_de_gestion_la_crea_junto_con_la_empresa(self):
        """Atajo pedido: se puede indicar el año de la primera gestión a auditar
        directo en el formulario de registrar empresa, sin tener que ir a otra
        pantalla.
        """
        respuesta = self.client.post(
            reverse("registros:empresa_crear"),
            {
                "nombre": "Cooperativa Santa Rita R.L.",
                "nit": "123456",
                "rubro": "",
                "categoria_cierre": "general",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
                "anio_gestion_inicial": "2025",
            },
        )
        self.assertRedirects(respuesta, reverse("registros:empresas_lista"))
        gestion = Gestion.objects.get(anio=2025)
        self.assertEqual(gestion.fecha_inicio, date(2025, 1, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 12, 31))

    def test_crear_empresa_industrial_calcula_gestion_con_cierre_31_marzo(self):
        """Una empresa industrial cierra el 31 de marzo, no el 31 de diciembre: la
        gestión inicial debe calcularse con esas fechas, sin que quien registra la
        empresa tenga que saberlo de memoria.
        """
        respuesta = self.client.post(
            reverse("registros:empresa_crear"),
            {
                "nombre": "Fábrica Andina S.A.",
                "nit": "654321",
                "rubro": "Industrial",
                "categoria_cierre": "industrial",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
                "anio_gestion_inicial": "2025",
            },
        )
        self.assertRedirects(respuesta, reverse("registros:empresas_lista"))
        gestion = Gestion.objects.get(anio=2025)
        self.assertEqual(gestion.fecha_inicio, date(2024, 4, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 3, 31))

    def test_crear_empresa_sin_anio_de_gestion_no_crea_ninguna(self):
        self.client.post(
            reverse("registros:empresa_crear"),
            {
                "nombre": "Cooperativa Santa Rita R.L.",
                "nit": "123456",
                "rubro": "",
                "categoria_cierre": "general",
                "contacto_nombre": "",
                "contacto_email": "",
                "contacto_telefono": "",
                "anio_gestion_inicial": "",
            },
        )
        self.assertFalse(Gestion.objects.exists())


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
                "categoria_cierre": "general",
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
                "categoria_cierre": "general",
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
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X", nit="111222")
        self.client.post(
            reverse("registros:empresa_editar", args=[empresa.id]),
            {
                "nombre": "Empresa X",
                "nit": "111222",
                "rubro": "",
                "categoria_cierre": "general",
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
    """RF-01: solo se aceptan archivos .xlsx, .xls, .csv o .pdf."""

    def setUp(self):
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )

    def test_extension_no_permitida_es_rechazada(self):
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={"archivo": SimpleUploadedFile("registros.docx", b"contenido")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)

    def test_extension_pdf_es_aceptada(self):
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={"archivo": SimpleUploadedFile("libro_diario.pdf", b"%PDF-1.4 contenido")},
        )
        self.assertTrue(form.is_valid())

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

    def test_archivo_vacio_es_rechazado(self):
        form = CargaArchivoForm(
            data={"empresa": self.empresa.id, "gestion": self.gestion.id},
            files={"archivo": SimpleUploadedFile("registros.csv", b"")},
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
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
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
        # Cuentas ya existentes de antemano: así ninguna fila genera el aviso de "cuenta
        # nueva" y se puede probar el caso realmente limpio (sin errores NI avisos
        # pendientes) por separado del caso con avisos, que se prueba en otro test.
        CuentaContable.objects.create(codigo="1001", nombre="Cuenta 1001")
        CuentaContable.objects.create(codigo="2001", nombre="Cuenta 2001")
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
        self.assertEqual(carga.registros_con_aviso, 0)
        self.assertEqual(RegistroContable.objects.filter(carga=carga).count(), 2)
        # Trae columna "cuenta" por fila: se detecta como Libro Diario (tabla plana).
        self.assertEqual(carga.formato_detectado, "diario_plano")

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
        # La fila se guarda igual (no es un error real, solo un aviso), pero queda
        # "con_observaciones" en vez de "validado" directo: el aviso de cuenta nueva
        # todavía no fue revisado por el auditor.
        self.assertEqual(carga.estado, "con_observaciones")
        self.assertEqual(carga.registros_con_error, 0)
        self.assertEqual(carga.registros_con_aviso, 1)
        aviso = ErrorValidacion.objects.get(carga=carga, campo="cuenta")
        self.assertEqual(aviso.tipo, "aviso")

    def test_cuenta_nueva_deriva_tipo_del_primer_digito_del_codigo(self):
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,5001,Gasto nuevo,80,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)

        cuenta = CuentaContable.objects.get(codigo="5001")
        self.assertEqual(cuenta.tipo, "gasto")

    def test_ingreso_con_movimiento_en_el_debe_genera_aviso(self):
        CuentaContable.objects.create(codigo="4001", nombre="Ventas", tipo="ingreso")
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,4001,Venta rara,90,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_observaciones")
        self.assertTrue(
            ErrorValidacion.objects.filter(
                carga=carga, campo="naturaleza_cuenta", tipo="aviso"
            ).exists()
        )

    def test_gasto_con_movimiento_en_el_haber_genera_aviso(self):
        CuentaContable.objects.create(codigo="5001", nombre="Sueldos", tipo="gasto")
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,5001,Reverso raro,0,60\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_observaciones")
        self.assertTrue(
            ErrorValidacion.objects.filter(
                carga=carga, campo="naturaleza_cuenta", tipo="aviso"
            ).exists()
        )

    def test_activo_no_genera_aviso_de_naturaleza_cuenta(self):
        # Caso normal (sin nada raro): no debe dispararse ningún aviso de
        # naturaleza_cuenta para una cuenta de Activo con movimiento normal.
        CuentaContable.objects.create(codigo="1001", nombre="Caja", tipo="activo")
        csv = "fecha,cuenta,glosa,debe,haber\n01/01/2023,1001,Depósito,100,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "validado")
        self.assertFalse(
            ErrorValidacion.objects.filter(carga=carga, campo="naturaleza_cuenta").exists()
        )

    def test_mismo_comprobante_distinta_cuenta_no_es_duplicado(self):
        # Un comprobante contable reparte normalmente el monto entre varias cuentas
        # (debe en una, haber en otra), eso NO debe marcarse como fila duplicada, pedido
        # explícito de la auditora.
        CuentaContable.objects.create(codigo="1001", nombre="Caja", tipo="activo")
        CuentaContable.objects.create(codigo="2001", nombre="Proveedores", tipo="pasivo")
        csv = (
            "fecha,cuenta,comprobante,glosa,debe,haber\n"
            "01/01/2023,1001,C-001,Pago a proveedor,0,100\n"
            "01/01/2023,2001,C-001,Pago a proveedor,100,0\n"
        )
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertFalse(
            ErrorValidacion.objects.filter(carga=carga, campo="duplicado").exists()
        )

    def test_formato_libro_mayor_agrupado_por_cuenta(self):
        """Formato real de libro mayor exportado por un sistema contable: filas de
        título antes del encabezado, sin columna 'cuenta' por fila (se agrupa en
        bloques), y con subtotales intercalados que deben ignorarse en vez de
        tratarse como cuentas nuevas.
        """
        # Cuentas ya existentes de antemano, para que el resultado sea el caso limpio
        # (sin avisos de "cuenta nueva" de por medio) y se pueda comprobar el estado
        # "validado" sin ambigüedad.
        CuentaContable.objects.create(codigo="1-1-1-01-01", nombre="Caja moneda nacional")
        CuentaContable.objects.create(codigo="2-1-2-01", nombre="Cuentas por pagar")
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
        self.assertEqual(carga.registros_con_aviso, 0)
        # Sin columna "cuenta" por fila, agrupado en bloques: es Libro Mayor.
        self.assertEqual(carga.formato_detectado, "mayor")
        self.assertTrue(CuentaContable.objects.filter(codigo="1-1-1-01-01").exists())
        self.assertTrue(CuentaContable.objects.filter(codigo="2-1-2-01").exists())
        self.assertEqual(
            RegistroContable.objects.filter(cuenta__codigo="1-1-1-01-01").count(), 2
        )
        self.assertEqual(
            RegistroContable.objects.filter(cuenta__codigo="2-1-2-01").count(), 1
        )

    def test_transaccion_sin_cuenta_previa_se_marca_como_error(self):
        """Si el archivo agrupado trae una fila de transacción antes de cualquier
        encabezado de cuenta, no debe asignarse a ciegas, se registra como error
        para que el auditor lo revise.
        """
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

    def test_fecha_fuera_del_periodo_de_gestion_genera_aviso(self):
        """Una transacción fechada fuera del rango de la gestión seleccionada no se
        rechaza, pero se deja un aviso para que el auditor lo revise.
        """
        csv = "fecha,cuenta,glosa,debe,haber\n15/03/2024,1001,Fecha de otra gestion,100,0\n"
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.registros_validos, 1)
        self.assertEqual(carga.registros_con_aviso, 2)
        self.assertTrue(
            ErrorValidacion.objects.filter(
                carga=carga, campo="fecha", tipo="aviso"
            ).exists()
        )

    def test_carga_mayormente_valida_queda_con_observaciones_no_con_errores(self):
        """Si la mayoría de las filas se guardó bien y solo una puntual se rechazó,
        la carga no debe verse como un fracaso total: queda "con observaciones"
        (revisar esas filas puntuales), y "con errores" se reserva para cuando no
        se guardó nada en absoluto.
        """
        csv = (
            "fecha,cuenta,glosa,debe,haber\n"
            "01/01/2023,1001,Pago proveedor,100,0\n"
            "02/01/2023,2001,Cobro cliente,0,150\n"
            "03/01/2023,1001,Fila sin monto,0,0\n"
        )
        carga = self._crear_carga(csv)
        procesar_carga(carga)
        carga.refresh_from_db()

        self.assertEqual(carga.estado, "con_observaciones")
        self.assertEqual(carga.registros_validos, 2)
        self.assertEqual(carga.registros_con_error, 1)
        self.assertEqual(carga.registros_con_aviso, 2)


class CargaConfirmarValidacionViewTests(TestCase):
    """RF-02: confirmación manual de una carga "con pendientes" como definitiva, por
    un Administrador o Auditor.
    """

    def setUp(self):
        Group.objects.get_or_create(name="Administrador")
        Group.objects.get_or_create(name="Auditor")
        self.administrador = User.objects.create_user(
            username="admin", password="Clave-Segura123"
        )
        self.administrador.groups.add(Group.objects.get(name="Administrador"))
        self.auditor = User.objects.create_user(
            username="auditor", password="Clave-Segura123"
        )
        self.auditor.groups.add(Group.objects.get(name="Auditor"))

        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )

    def _crear_carga(self, estado, usuario=None):
        archivo = SimpleUploadedFile("carga.csv", b"contenido")
        return CargaArchivo.objects.create(
            archivo=archivo,
            usuario=usuario or self.auditor,
            empresa=self.empresa,
            gestion=self.gestion,
            estado=estado,
            total_registros=3,
            registros_validos=2,
            registros_con_error=1,
        )

    def test_administrador_puede_confirmar_carga_con_observaciones(self):
        carga = self._crear_carga("con_observaciones")
        self.client.login(username="admin", password="Clave-Segura123")

        respuesta = self.client.post(
            reverse("registros:carga_confirmar_validacion", args=[carga.id])
        )

        carga.refresh_from_db()
        self.assertRedirects(
            respuesta, reverse("registros:detalle_carga", args=[carga.id])
        )
        self.assertEqual(carga.estado, "validado")
        self.assertEqual(carga.revisado_por, self.administrador)
        self.assertIsNotNone(carga.fecha_revision)
        self.assertTrue(
            HistorialCambio.objects.filter(
                modelo="CargaArchivo", objeto_id=carga.id, campo="estado"
            ).exists()
        )

    def test_auditor_tambien_puede_confirmar(self):
        carga = self._crear_carga("con_observaciones")
        self.client.login(username="auditor", password="Clave-Segura123")

        self.client.post(reverse("registros:carga_confirmar_validacion", args=[carga.id]))

        carga.refresh_from_db()
        self.assertEqual(carga.estado, "validado")
        self.assertEqual(carga.revisado_por, self.auditor)

    def test_usuario_sin_rol_no_puede_confirmar(self):
        carga = self._crear_carga("con_observaciones")
        sin_rol = User.objects.create_user(username="nadie", password="Clave-Segura123")
        self.client.login(username="nadie", password="Clave-Segura123")

        self.client.post(reverse("registros:carga_confirmar_validacion", args=[carga.id]))

        carga.refresh_from_db()
        self.assertEqual(carga.estado, "con_observaciones")

    def test_no_se_puede_confirmar_una_carga_que_no_tiene_pendientes(self):
        carga = self._crear_carga("validado")
        self.client.login(username="admin", password="Clave-Segura123")

        self.client.post(reverse("registros:carga_confirmar_validacion", args=[carga.id]))

        carga.refresh_from_db()
        self.assertEqual(carga.estado, "validado")
        self.assertIsNone(carga.revisado_por)

    def test_get_no_esta_permitido(self):
        carga = self._crear_carga("con_observaciones")
        self.client.login(username="admin", password="Clave-Segura123")

        respuesta = self.client.get(
            reverse("registros:carga_confirmar_validacion", args=[carga.id])
        )

        self.assertEqual(respuesta.status_code, 405)


class CargaAnularViewTests(TestCase):
    """Corrección de un error humano al elegir empresa/gestión: se anula la carga y
    se sube de nuevo el archivo correcto.
    """

    def setUp(self):
        Group.objects.get_or_create(name="Administrador")
        Group.objects.get_or_create(name="Auditor")
        self.administrador = User.objects.create_user(
            username="admin", password="Clave-Segura123"
        )
        self.administrador.groups.add(Group.objects.get(name="Administrador"))
        self.auditor = User.objects.create_user(
            username="auditor", password="Clave-Segura123"
        )
        self.auditor.groups.add(Group.objects.get(name="Auditor"))

        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion_2025 = Gestion.objects.create(
            anio=2025, fecha_inicio=date(2025, 1, 1), fecha_fin=date(2025, 12, 31)
        )

    def _crear_carga(self, estado="validado"):
        archivo = SimpleUploadedFile("carga.csv", b"contenido")
        return CargaArchivo.objects.create(
            archivo=archivo,
            usuario=self.auditor,
            empresa=self.empresa,
            gestion=self.gestion_2025,
            estado=estado,
        )

    def test_administrador_puede_anular_con_motivo(self):
        carga = self._crear_carga()
        self.client.login(username="admin", password="Clave-Segura123")

        respuesta = self.client.post(
            reverse("registros:carga_anular", args=[carga.id]),
            {"motivo": "Se cargó con la gestión 2025, correspondía la 2022."},
        )

        carga.refresh_from_db()
        self.assertRedirects(
            respuesta, reverse("registros:detalle_carga", args=[carga.id])
        )
        self.assertEqual(carga.estado, "anulada")
        self.assertEqual(carga.anulado_por, self.administrador)
        self.assertIsNotNone(carga.fecha_anulacion)
        self.assertIn("gestión 2025", carga.motivo_anulacion)
        self.assertTrue(
            HistorialCambio.objects.filter(
                modelo="CargaArchivo", objeto_id=carga.id, campo="estado", valor_nuevo="anulada"
            ).exists()
        )

    def test_motivo_muy_corto_se_rechaza_y_no_anula(self):
        carga = self._crear_carga()
        self.client.login(username="admin", password="Clave-Segura123")

        self.client.post(reverse("registros:carga_anular", args=[carga.id]), {"motivo": "corto"})

        carga.refresh_from_db()
        self.assertEqual(carga.estado, "validado")
        self.assertIsNone(carga.anulado_por)

    def test_auditor_no_puede_anular(self):
        carga = self._crear_carga()
        self.client.login(username="auditor", password="Clave-Segura123")

        self.client.post(
            reverse("registros:carga_anular", args=[carga.id]),
            {"motivo": "Se cargó con la gestión equivocada."},
        )

        carga.refresh_from_db()
        self.assertEqual(carga.estado, "validado")

    def test_no_se_puede_anular_dos_veces(self):
        carga = self._crear_carga(estado="anulada")
        self.client.login(username="admin", password="Clave-Segura123")

        respuesta = self.client.post(
            reverse("registros:carga_anular", args=[carga.id]),
            {"motivo": "Intento de anular de nuevo."},
            follow=True,
        )

        self.assertContains(respuesta, "ya está anulada")


class GestionFormTests(TestCase):
    def test_anio_muy_adelantado_a_futuro_es_rechazado(self):
        """Una gestión a auditar no tiene sentido de negocio muy adelantada
        al año en curso (se audita un periodo ya cerrado o en curso)."""
        anio_muy_futuro = date.today().year + 5
        form = GestionForm(
            data={"anio": anio_muy_futuro, "fecha_inicio": "", "fecha_fin": ""}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("anio", form.errors)

    def test_anio_siguiente_al_actual_todavia_se_acepta(self):
        """No se bloquea el año siguiente al actual: permite cerrar una gestión que
        recién terminó, sin ser tan estricto como para exigir únicamente el año en
        curso.
        """
        anio_siguiente = date.today().year + 1
        form = GestionForm(
            data={"anio": anio_siguiente, "fecha_inicio": "", "fecha_fin": ""}
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_solo_con_anio_completa_las_fechas_solo(self):
        """Uso simple del día a día: escribir solo el año alcanza, sin
        tener que pensar en fechas de cierre."""
        form = GestionForm(data={"anio": 2025, "fecha_inicio": "", "fecha_fin": ""})
        self.assertTrue(form.is_valid())
        gestion = form.save()
        self.assertEqual(gestion.fecha_inicio, date(2025, 1, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 12, 31))

    def test_fecha_fin_anterior_a_inicio_es_rechazada(self):
        form = GestionForm(
            data={"anio": 2023, "fecha_inicio": "2023-12-31", "fecha_fin": "2023-01-01"}
        )
        self.assertFalse(form.is_valid())

    def test_periodo_valido_se_acepta(self):
        form = GestionForm(
            data={"anio": 2024, "fecha_inicio": "2023-04-01", "fecha_fin": "2024-03-31"}
        )
        self.assertTrue(form.is_valid())

    def test_mismo_anio_con_fechas_distintas_puede_coexistir(self):
        """Dos empresas con distinto rubro pueden necesitar una 'gestión 2024' con
        rangos de fechas distintos: el año repetido no debe rechazarse si las
        fechas son diferentes.
        """
        Gestion.objects.create(
            anio=2024, fecha_inicio=date(2023, 10, 1), fecha_fin=date(2024, 9, 30)
        )
        form = GestionForm(
            data={"anio": 2024, "fecha_inicio": "2024-01-01", "fecha_fin": "2024-12-31"}
        )
        self.assertTrue(form.is_valid())

    def test_gestion_exactamente_duplicada_es_rechazada(self):
        """Mismo año Y mismas fechas es un duplicado sin sentido, ese sí se
        rechaza."""
        Gestion.objects.create(
            anio=2024, fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 12, 31)
        )
        form = GestionForm(
            data={"anio": 2024, "fecha_inicio": "2024-01-01", "fecha_fin": "2024-12-31"}
        )
        self.assertFalse(form.is_valid())

    def test_con_empresa_industrial_autocompleta_segun_su_categoria(self):
        """Antes esta pantalla no sabía para qué empresa era la gestión, así que
        nunca podía sugerir fechas según su categoría SIN (solo pasaba al crear la
        empresa).
        """
        empresa = EmpresaAuditada.objects.create(
            nombre="Empresa Industrial", categoria_cierre="industrial"
        )
        form = GestionForm(
            data={"anio": 2025, "empresa": empresa.pk, "fecha_inicio": "", "fecha_fin": ""}
        )
        self.assertTrue(form.is_valid())
        gestion = form.save()
        self.assertEqual(gestion.fecha_inicio, date(2024, 4, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 3, 31))

    def test_fechas_manuales_prevalecen_sobre_la_sugerencia_de_la_empresa(self):
        """Elegir una empresa solo sugiere: si igual se escriben las fechas
        a mano, esas son las que se respetan (excepción real puntual)."""
        empresa = EmpresaAuditada.objects.create(
            nombre="Empresa Minera", categoria_cierre="minera"
        )
        form = GestionForm(
            data={
                "anio": 2025,
                "empresa": empresa.pk,
                "fecha_inicio": "2025-01-01",
                "fecha_fin": "2025-12-31",
            }
        )
        self.assertTrue(form.is_valid())
        gestion = form.save()
        self.assertEqual(gestion.fecha_inicio, date(2025, 1, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 12, 31))

    def test_sin_empresa_sigue_usando_anio_calendario(self):
        """Sin elegir empresa, el comportamiento de siempre no cambia."""
        form = GestionForm(
            data={"anio": 2025, "empresa": "", "fecha_inicio": "", "fecha_fin": ""}
        )
        self.assertTrue(form.is_valid())
        gestion = form.save()
        self.assertEqual(gestion.fecha_inicio, date(2025, 1, 1))
        self.assertEqual(gestion.fecha_fin, date(2025, 12, 31))


class GestionPermisosViewTests(TestCase):
    """Quién puede dar de alta/editar una gestión: a diferencia de las empresas
    clientes, una gestión es trabajo operativo del día a día, así que
    Administrador y Auditor pueden hacerlo, solo un usuario sin ninguno de esos
    dos roles queda afuera.
    """

    def setUp(self):
        Group.objects.get_or_create(name="Administrador")
        Group.objects.get_or_create(name="Auditor")
        self.auditor = User.objects.create_user(
            username="auditor", password="Clave-Segura123"
        )
        self.auditor.groups.add(Group.objects.get(name="Auditor"))
        self.sin_rol = User.objects.create_user(
            username="sinrol", password="Clave-Segura123"
        )

    def test_auditor_puede_crear_gestion(self):
        self.client.login(username="auditor", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("registros:gestion_crear"),
            {"anio": 2025, "fecha_inicio": "", "fecha_fin": ""},
        )
        self.assertRedirects(respuesta, reverse("registros:cargar"))
        self.assertTrue(Gestion.objects.filter(anio=2025).exists())

    def test_auditor_puede_editar_gestion(self):
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.client.login(username="auditor", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("registros:gestion_editar", args=[gestion.id]),
            {"anio": 2023, "fecha_inicio": "2023-04-01", "fecha_fin": "2024-03-31"},
        )
        self.assertRedirects(respuesta, reverse("registros:gestion_crear"))
        gestion.refresh_from_db()
        self.assertEqual(gestion.fecha_inicio, date(2023, 4, 1))

    def test_usuario_sin_rol_no_puede_crear_gestion(self):
        self.client.login(username="sinrol", password="Clave-Segura123")
        respuesta = self.client.post(
            reverse("registros:gestion_crear"),
            {"anio": 2025, "fecha_inicio": "", "fecha_fin": ""},
        )
        self.assertRedirects(respuesta, reverse("home"))
        self.assertFalse(Gestion.objects.filter(anio=2025).exists())


class GestionEditarViewTests(TestCase):
    """Corrección de errores humanos al registrar una gestión (RF-09, Administrador y
    Auditor).
    """

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )

    def test_editar_gestion_corrige_fechas(self):
        respuesta = self.client.post(
            reverse("registros:gestion_editar", args=[self.gestion.id]),
            {"anio": 2023, "fecha_inicio": "2023-04-01", "fecha_fin": "2024-03-31"},
        )
        self.assertRedirects(respuesta, reverse("registros:gestion_crear"))
        self.gestion.refresh_from_db()
        self.assertEqual(self.gestion.fecha_inicio, date(2023, 4, 1))
        self.assertEqual(self.gestion.fecha_fin, date(2024, 3, 31))

    def test_editar_gestion_registra_historial(self):
        self.client.post(
            reverse("registros:gestion_editar", args=[self.gestion.id]),
            {"anio": 2023, "fecha_inicio": "2023-04-01", "fecha_fin": "2024-03-31"},
        )
        cambios = HistorialCambio.objects.filter(
            modelo="Gestion", objeto_id=self.gestion.id, accion="edicion"
        )
        self.assertTrue(cambios.filter(campo="fecha_inicio").exists())
        self.assertTrue(cambios.filter(campo="fecha_fin").exists())

    def test_editar_gestion_sin_cambios_no_ensucia_el_historial(self):
        self.client.post(
            reverse("registros:gestion_editar", args=[self.gestion.id]),
            {"anio": 2023, "fecha_inicio": "2023-01-01", "fecha_fin": "2023-12-31"},
        )
        cambios = HistorialCambio.objects.filter(
            modelo="Gestion", objeto_id=self.gestion.id, accion="edicion"
        )
        self.assertEqual(cambios.count(), 0)


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class DetalleCargaIrAFilaTests(TestCase):
    """'Ir a la fila' en el detalle de carga: con archivos de miles de filas, ir
    clickeando página por página para llegar a una fila puntual es impracticable,
    se calcula directo en qué página cae esa posición.
    """

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin,
            empresa=empresa,
            gestion=gestion,
            estado="validado",
            total_registros=120,
            registros_validos=120,
        )
        cuenta = CuentaContable.objects.create(codigo="1001", nombre="Caja")
        # 120 filas (2.4 páginas a 50 por página), CON UN HUECO: se salta a propósito la
        # fila 61, para probar que el cálculo de página sigue siendo correcto aunque
        # haya huecos.
        registros = [
            RegistroContable(
                carga=self.carga,
                cuenta=cuenta,
                fecha=date(2023, 1, 1),
                fila_origen=fila,
                debe=100,
                haber=0,
            )
            for fila in range(1, 122)
            if fila != 61
        ]
        RegistroContable.objects.bulk_create(registros)

    def _get(self, fila=None):
        parametros = {"fila": fila} if fila is not None else {}
        return self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]), parametros
        )

    def test_sin_buscar_muestra_la_primera_pagina(self):
        respuesta = self._get()
        self.assertEqual(respuesta.context["registros_pagina"].number, 1)
        self.assertIsNone(respuesta.context["fila_buscada"])

    def test_buscar_fila_existente_calcula_la_pagina_correcta(self):
        respuesta = self._get(fila=110)
        # Fila 110 es la posición 109 entre las guardadas (se saltó la 61),
        # así que cae en la página 3 (ceil(109/50) = 3).
        self.assertEqual(respuesta.context["registros_pagina"].number, 3)
        self.assertEqual(respuesta.context["fila_buscada"], 110)
        self.assertTrue(respuesta.context["fila_encontrada"])
        self.assertContains(respuesta, 'id="fila-buscada"')

    def test_buscar_fila_que_no_quedo_guardada_igual_ubica_la_pagina_cercana(self):
        ErrorValidacion.objects.create(
            carga=self.carga, fila=61, campo="fecha", descripcion="Fecha inválida"
        )
        respuesta = self._get(fila=61)
        self.assertFalse(respuesta.context["fila_encontrada"])
        self.assertContains(respuesta, "fue rechazada al importar")

    def test_paginacion_despues_de_saltar_a_una_fila_no_queda_atrapada(self):
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]), {"fila": 52, "pagina": 3}
        )
        self.assertEqual(respuesta.context["registros_pagina"].number, 2)
        # Los enlaces de paginación ya no llevan "fila": el siguiente clic
        # navega de verdad.
        self.assertNotIn("fila=", respuesta.context["registros_querystring"])

    def test_buscar_fila_mas_alla_del_total_cae_en_la_ultima_pagina(self):
        respuesta = self._get(fila=99999)
        ultima_pagina = respuesta.context["registros_pagina"].paginator.num_pages
        self.assertEqual(respuesta.context["registros_pagina"].number, ultima_pagina)

    def test_texto_no_numerico_se_ignora_y_no_rompe_la_pagina(self):
        respuesta = self._get(fila="abc")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context["fila_buscada"])


class DetalleCargaFiltrosTests(TestCase):
    """Filtro por código de cuenta (por grupo, usando el primer dígito) y
    por número de comprobante en la tabla de "Registros cargados"."""

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin, empresa=empresa, gestion=gestion, estado="validado",
        )
        cuenta_1 = CuentaContable.objects.create(codigo="1-1-1-01-01", nombre="Caja", tipo="activo")
        # Código con un "2" en medio, a propósito: no debería aparecer al
        # filtrar por grupo "2" (ver por qué se usa startswith, no icontains).
        cuenta_1b = CuentaContable.objects.create(codigo="1-1-2-01-01", nombre="Banco", tipo="activo")
        cuenta_2 = CuentaContable.objects.create(codigo="2-1-1-01", nombre="Proveedores", tipo="pasivo")
        RegistroContable.objects.create(
            carga=self.carga, cuenta=cuenta_1, fecha=date(2023, 1, 1),
            fila_origen=1, numero_comprobante="AD 001", debe=100, haber=0,
        )
        RegistroContable.objects.create(
            carga=self.carga, cuenta=cuenta_1b, fecha=date(2023, 1, 1),
            fila_origen=2, numero_comprobante="CI 002", debe=50, haber=0,
        )
        RegistroContable.objects.create(
            carga=self.carga, cuenta=cuenta_2, fecha=date(2023, 1, 1),
            fila_origen=3, numero_comprobante="AD 003", debe=0, haber=100,
        )

    def test_filtro_por_grupo_no_trae_cuentas_con_el_digito_en_medio(self):
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]), {"cuenta_codigo": "2"}
        )
        filas = list(respuesta.context["registros_pagina"])
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].cuenta.codigo, "2-1-1-01")

    def test_filtro_por_comprobante(self):
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]), {"numero_comprobante": "AD"}
        )
        filas = list(respuesta.context["registros_pagina"])
        self.assertEqual(len(filas), 2)


class AvisoMarcarEstadoViewTests(TestCase):
    """Justificación individual de un aviso puntual (válido u observado), a
    diferencia del marcado en lote de `avisos_marcar_revisados`, pedido por la
    firma auditora para poder distinguir un falso positivo de una irregularidad
    real a documentar.
    """

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin,
            empresa=empresa,
            gestion=gestion,
            estado="con_observaciones",
        )
        self.aviso = ErrorValidacion.objects.create(
            carga=self.carga, fila=5, campo="cuenta", tipo="aviso",
            descripcion="Cuenta nueva, verificar clasificación.",
        )

    def _post(self, payload):
        import json
        return self.client.post(
            reverse("registros:aviso_marcar_estado", args=[self.carga.id, self.aviso.id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_marcar_observado_con_justificacion(self):
        respuesta = self._post({"estado": "observado", "comentario": "Monto fuera de patrón, sin soporte."})
        self.assertEqual(respuesta.status_code, 200)
        self.aviso.refresh_from_db()
        self.assertEqual(self.aviso.estado_revision, "observado")
        self.assertTrue(self.aviso.revisado)
        self.assertEqual(self.aviso.revisado_por, self.admin)
        self.assertEqual(self.aviso.comentario_revision, "Monto fuera de patrón, sin soporte.")

    def test_marcar_valido_con_justificacion(self):
        respuesta = self._post({"estado": "valido", "comentario": "Cuenta confirmada con el cliente."})
        self.assertEqual(respuesta.status_code, 200)
        self.aviso.refresh_from_db()
        self.assertEqual(self.aviso.estado_revision, "valido")

    def test_sin_comentario_no_se_marca(self):
        respuesta = self._post({"estado": "observado", "comentario": ""})
        self.assertEqual(respuesta.status_code, 400)
        self.aviso.refresh_from_db()
        self.assertEqual(self.aviso.estado_revision, "pendiente")

    def test_estado_invalido_se_rechaza(self):
        respuesta = self._post({"estado": "cualquiercosa", "comentario": "algo"})
        self.assertEqual(respuesta.status_code, 400)
        self.aviso.refresh_from_db()
        self.assertEqual(self.aviso.estado_revision, "pendiente")


class ReporteObservacionesViewTests(TestCase):
    """Reporte descargable de transacciones marcadas como "observado" (hallazgo
    confirmado), no debe incluir las marcadas como "válido" (falso positivo).
    """

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin,
            empresa=self.empresa,
            gestion=self.gestion,
            estado="con_observaciones",
        )
        cuenta = CuentaContable.objects.create(codigo="5001", nombre="Sueldos", tipo="gasto")
        RegistroContable.objects.create(
            carga=self.carga, cuenta=cuenta, fecha=date(2023, 1, 1),
            fila_origen=1, numero_comprobante="C-100", debe=500, haber=0,
        )
        self.observado = ErrorValidacion.objects.create(
            carga=self.carga, fila=1, campo="naturaleza_cuenta", tipo="aviso",
            descripcion="Gasto con movimiento en el haber.",
            estado_revision="observado", revisado=True, revisado_por=self.admin,
            comentario_revision="No corresponde, se solicitó reverso al cliente.",
        )
        self.valido = ErrorValidacion.objects.create(
            carga=self.carga, fila=2, campo="cuenta", tipo="aviso",
            descripcion="Cuenta nueva.",
            estado_revision="valido", revisado=True, revisado_por=self.admin,
            comentario_revision="Cuenta confirmada con el cliente.",
        )

    def test_reporte_solo_incluye_observados(self):
        respuesta = self.client.get(
            reverse("registros:reporte_observaciones", args=[self.carga.id])
        )
        self.assertEqual(respuesta.status_code, 200)
        filas = respuesta.context["filas_reporte"]
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["observacion"].id, self.observado.id)
        self.assertContains(respuesta, "C-100")
        self.assertNotContains(respuesta, "Cuenta confirmada con el cliente.")

    def test_reporte_vacio_sin_observaciones(self):
        self.observado.delete()
        respuesta = self.client.get(
            reverse("registros:reporte_observaciones", args=[self.carga.id])
        )
        self.assertEqual(len(respuesta.context["filas_reporte"]), 0)
        self.assertContains(respuesta, "Todavía no hay transacciones marcadas")


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class CargarRegistrosFiltroTests(TestCase):
    """Filtro por empresa/gestión/estado en 'Cargas anteriores': útil apenas
    hay más de una empresa o gestión con archivos cargados."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        self.empresa_a = EmpresaAuditada.objects.create(nombre="Empresa A")
        self.empresa_b = EmpresaAuditada.objects.create(nombre="Empresa B")
        self.gestion_2022 = Gestion.objects.create(
            anio=2022, fecha_inicio=date(2022, 1, 1), fecha_fin=date(2022, 12, 31)
        )
        self.gestion_2023 = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga_a = self._crear_carga(self.empresa_a, self.gestion_2023, "validado")
        self.carga_b = self._crear_carga(self.empresa_b, self.gestion_2022, "con_errores")

    def _crear_carga(self, empresa, gestion, estado):
        archivo = SimpleUploadedFile("carga.csv", b"fecha,cuenta,glosa,debe,haber\n")
        return CargaArchivo.objects.create(
            archivo=archivo,
            usuario=self.admin,
            empresa=empresa,
            gestion=gestion,
            estado=estado,
            registros_validos=0 if estado == "con_errores" else 10,
        )

    def test_sin_filtro_muestra_todas(self):
        respuesta = self.client.get(reverse("registros:cargar"))
        ids = [c.id for c in respuesta.context["cargas_anteriores"]]
        self.assertCountEqual(ids, [self.carga_a.id, self.carga_b.id])

    def test_filtro_por_empresa(self):
        respuesta = self.client.get(
            reverse("registros:cargar"), {"empresa": self.empresa_a.id}
        )
        ids = [c.id for c in respuesta.context["cargas_anteriores"]]
        self.assertEqual(ids, [self.carga_a.id])

    def test_filtro_por_gestion(self):
        respuesta = self.client.get(
            reverse("registros:cargar"), {"gestion": self.gestion_2022.id}
        )
        ids = [c.id for c in respuesta.context["cargas_anteriores"]]
        self.assertEqual(ids, [self.carga_b.id])

    def test_filtro_por_estado(self):
        respuesta = self.client.get(
            reverse("registros:cargar"), {"importacion": "fallida"}
        )
        ids = [c.id for c in respuesta.context["cargas_anteriores"]]
        self.assertEqual(ids, [self.carga_b.id])

    def test_filtros_combinados_sin_coincidencias(self):
        respuesta = self.client.get(
            reverse("registros:cargar"),
            {"empresa": self.empresa_a.id, "importacion": "fallida"},
        )
        self.assertEqual(len(respuesta.context["cargas_anteriores"]), 0)
        self.assertContains(respuesta, "Ninguna carga coincide con ese filtro.")

    def test_solo_se_listan_empresas_y_gestiones_con_cargas(self):
        """No tiene sentido ofrecer como filtro una empresa o gestión que
        nunca tuvo ninguna carga: siempre daría una lista vacía."""
        EmpresaAuditada.objects.create(nombre="Empresa Sin Cargas")
        Gestion.objects.create(
            anio=2020, fecha_inicio=date(2020, 1, 1), fecha_fin=date(2020, 12, 31)
        )
        respuesta = self.client.get(reverse("registros:cargar"))
        nombres_empresas = [e.nombre for e in respuesta.context["empresas_con_cargas"]]
        self.assertNotIn("Empresa Sin Cargas", nombres_empresas)
        anios_gestiones = [g.anio for g in respuesta.context["gestiones_con_cargas"]]
        self.assertNotIn(2020, anios_gestiones)

    def test_querystring_conserva_filtro_para_paginacion(self):
        respuesta = self.client.get(
            reverse("registros:cargar"), {"empresa": self.empresa_a.id}
        )
        self.assertEqual(
            respuesta.context["querystring"], f"empresa={self.empresa_a.id}"
        )


class BorrarDatosPruebaCommandTests(TestCase):
    """Comando de mantenimiento `borrar_datos_prueba` (ver
    registros/management/commands/borrar_datos_prueba.py)."""

    def setUp(self):
        self.usuario = User.objects.create_user(
            username="admin", password="Clave-Segura123"
        )
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        archivo = SimpleUploadedFile("carga.csv", b"contenido")
        self.carga = CargaArchivo.objects.create(
            archivo=archivo, usuario=self.usuario, empresa=self.empresa, gestion=self.gestion
        )
        self.cuenta = CuentaContable.objects.create(codigo="1001", nombre="Caja", tipo="activo")
        RegistroContable.objects.create(
            carga=self.carga, cuenta=self.cuenta, fecha=date(2023, 1, 1), fila_origen=1
        )

    def test_sin_confirmar_no_borra_nada(self):
        from django.core.management import call_command

        call_command("borrar_datos_prueba")

        self.assertEqual(CargaArchivo.objects.count(), 1)
        self.assertEqual(EmpresaAuditada.objects.count(), 1)
        self.assertEqual(User.objects.count(), 1)

    def test_con_confirmar_borra_datos_de_negocio_pero_no_usuarios(self):
        from django.core.management import call_command

        call_command("borrar_datos_prueba", "--confirmar")

        self.assertEqual(CargaArchivo.objects.count(), 0)
        self.assertEqual(RegistroContable.objects.count(), 0)
        self.assertEqual(EmpresaAuditada.objects.count(), 0)
        self.assertEqual(Gestion.objects.count(), 0)
        self.assertEqual(CuentaContable.objects.count(), 0)
        # Los usuarios NO se tocan.
        self.assertEqual(User.objects.count(), 1)



def _post_json(client, url, payload):
    import json
    return client.post(url, data=json.dumps(payload), content_type="application/json")


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class ImportacionIntegridadTests(TestCase):
    """Hallazgos H06, H07, H10 y H11 del informe de revisión externa."""

    def setUp(self):
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.usuario = User.objects.create_user(username="auditor", password="Clave-Segura123")

    def _carga(self, contenido):
        return CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", contenido.encode("utf-8")),
            usuario=self.usuario, empresa=self.empresa, gestion=self.gestion,
        )

    def test_importe_con_texto_se_rechaza_en_vez_de_convertirse_en_cero(self):
        carga = self._carga(
            "fecha,cuenta,glosa,debe,haber\n"
            "01/01/2023,1001,Pago,ABC,100\n"
        )
        procesar_carga(carga)
        carga.refresh_from_db()
        self.assertEqual(carga.registros_validos, 0)
        self.assertTrue(carga.errores.filter(tipo="error", campo="debe").exists())
        self.assertEqual(RegistroContable.objects.count(), 0)

    def test_importes_con_separadores_regionales_se_interpretan(self):
        carga = self._carga(
            "fecha,cuenta,glosa,debe,haber\n"
            '01/01/2023,1001,Pago,"1.234,56",0\n'
            '02/01/2023,1001,Cobro,0,"2,345.10"\n'
        )
        procesar_carga(carga)
        montos = sorted(
            float(r.debe or r.haber) for r in RegistroContable.objects.all()
        )
        self.assertEqual(montos, [1234.56, 2345.10])

    def test_importe_fuera_de_rango_se_rechaza(self):
        carga = self._carga(
            "fecha,cuenta,glosa,debe,haber\n"
            "01/01/2023,1001,Pago,99999999999999,0\n"
        )
        procesar_carga(carga)
        self.assertTrue(carga.errores.filter(tipo="error", campo="debe").exists())

    def test_fecha_invalida_en_libro_mayor_no_cambia_la_cuenta_del_bloque(self):
        carga = self._carga(
            "fecha,glosa,debe,haber\n"
            "1-1-1-01 Caja,,,\n"
            "fecha_mal,pago,100,0\n"
            "02/01/2023,cobro,0,50\n"
        )
        procesar_carga(carga)
        carga.refresh_from_db()
        self.assertEqual(carga.total_registros, 2)
        self.assertTrue(carga.errores.filter(tipo="error", campo="fecha", fila=3).exists())
        registro = RegistroContable.objects.get()
        self.assertEqual(registro.cuenta.codigo, "1-1-1-01")

    def test_archivo_sin_transacciones_no_queda_validado(self):
        carga = self._carga("fecha,cuenta,glosa,debe,haber\n")
        procesar_carga(carga)
        carga.refresh_from_db()
        self.assertEqual(carga.estado, "con_errores")
        self.assertTrue(carga.errores.filter(campo="archivo").exists())

    def test_fallo_al_guardar_no_deja_datos_a_medias(self):
        from unittest import mock
        carga = self._carga(
            "fecha,cuenta,glosa,debe,haber\n"
            "01/01/2023,1001,Pago,100,0\n"
        )
        with mock.patch(
            "registros.services.ErrorValidacion.objects.bulk_create",
            side_effect=RuntimeError("fallo simulado"),
        ):
            with self.assertRaises(RuntimeError):
                procesar_carga(carga)
        self.assertEqual(RegistroContable.objects.count(), 0)
        self.assertEqual(CuentaContable.objects.count(), 0)


class RevisionAvisosIntegridadTests(TestCase):
    """Hallazgos H04, H08, H09, H15, H17 y H22 del informe de revisión externa."""

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin, empresa=empresa, gestion=self.gestion,
            estado="con_observaciones",
        )
        self.observado = ErrorValidacion.objects.create(
            carga=self.carga, fila=5, campo="cuenta", tipo="aviso", descripcion="x",
            revisado=True, estado_revision="observado", comentario_revision="Sin soporte",
        )
        self.pendiente = ErrorValidacion.objects.create(
            carga=self.carga, fila=6, campo="cuenta", tipo="aviso", descripcion="y",
        )
        self.url_lote = reverse("registros:avisos_marcar_revisados", args=[self.carga.id])

    def test_lote_por_ids_no_pisa_una_decision_ya_tomada(self):
        respuesta = _post_json(self.client, self.url_lote, {
            "aviso_ids": [self.observado.id, self.pendiente.id], "comentario": "Revisado con el cliente",
        })
        self.assertEqual(respuesta.status_code, 200)
        self.observado.refresh_from_db()
        self.pendiente.refresh_from_db()
        self.assertEqual(self.observado.estado_revision, "observado")
        self.assertEqual(self.observado.comentario_revision, "Sin soporte")
        self.assertEqual(self.pendiente.estado_revision, "valido")
        self.assertTrue(
            HistorialCambio.objects.filter(modelo="ErrorValidacion", objeto_id=self.pendiente.id).exists()
        )

    def test_lote_sin_justificacion_se_rechaza(self):
        respuesta = _post_json(self.client, self.url_lote, {"aviso_ids": [self.pendiente.id]})
        self.assertEqual(respuesta.status_code, 400)
        self.pendiente.refresh_from_db()
        self.assertFalse(self.pendiente.revisado)

    def test_json_que_no_es_objeto_devuelve_400(self):
        respuesta = _post_json(self.client, self.url_lote, [])
        self.assertEqual(respuesta.status_code, 400)
        respuesta = _post_json(self.client, self.url_lote, {"aviso_ids": "1", "comentario": "x"})
        self.assertEqual(respuesta.status_code, 400)

    def test_rectificar_una_decision_conserva_la_anterior_en_el_historial(self):
        url = reverse("registros:aviso_marcar_estado", args=[self.carga.id, self.observado.id])
        _post_json(self.client, url, {"estado": "valido", "comentario": "El cliente trajo el soporte"})
        cambio = HistorialCambio.objects.get(modelo="ErrorValidacion", objeto_id=self.observado.id)
        self.assertIn("observado", cambio.valor_anterior)
        self.assertIn("Sin soporte", cambio.valor_anterior)

    def test_carga_anulada_no_admite_revision(self):
        self.carga.estado = "anulada"
        self.carga.save()
        url = reverse("registros:aviso_marcar_estado", args=[self.carga.id, self.pendiente.id])
        respuesta = _post_json(self.client, url, {"estado": "observado", "comentario": "x"})
        self.assertEqual(respuesta.status_code, 403)
        respuesta = _post_json(self.client, self.url_lote, {"campo": "cuenta", "comentario": "x"})
        self.assertEqual(respuesta.status_code, 403)
        self.pendiente.refresh_from_db()
        self.assertFalse(self.pendiente.revisado)

    def test_usuario_sin_rol_no_puede_ver_ni_revisar(self):
        User.objects.create_user(username="sinrol", password="Clave-Segura123")
        self.client.logout()
        self.client.login(username="sinrol", password="Clave-Segura123")
        respuesta = self.client.get(reverse("registros:detalle_carga", args=[self.carga.id]))
        self.assertEqual(respuesta.status_code, 302)
        url = reverse("registros:aviso_marcar_estado", args=[self.carga.id, self.pendiente.id])
        _post_json(self.client, url, {"estado": "observado", "comentario": "x"})
        self.pendiente.refresh_from_db()
        self.assertFalse(self.pendiente.revisado)

    def test_original_contable_no_se_descarga_sin_sesion(self):
        self.client.logout()
        respuesta = self.client.get("/media/" + self.carga.archivo.name)
        self.assertEqual(respuesta.status_code, 302)

    def test_exportacion_csv_neutraliza_formulas(self):
        cuenta = CuentaContable.objects.create(codigo="1001", nombre="Caja")
        RegistroContable.objects.create(
            carga=self.carga, cuenta=cuenta, fecha=date(2023, 1, 1),
            glosa="=1+1", debe=10, haber=0, fila_origen=2,
        )
        respuesta = self.client.get(reverse("registros:exportar_dataset_csv", args=[self.carga.id]))
        contenido = respuesta.content.decode("utf-8")
        self.assertIn("'=1+1", contenido)

    def test_gestion_usada_en_una_carga_no_se_puede_editar(self):
        url = reverse("registros:gestion_editar", args=[self.gestion.id])
        self.client.post(url, {"anio": 2023, "fecha_inicio": "2023-04-01", "fecha_fin": "2024-03-31"})
        self.gestion.refresh_from_db()
        self.assertEqual(self.gestion.fecha_inicio, date(2023, 1, 1))

    def test_reporte_indica_carga_anulada_y_pendientes(self):
        url = reverse("registros:reporte_observaciones", args=[self.carga.id])
        self.assertContains(self.client.get(url), "Revisión en curso")
        self.carga.estado = "anulada"
        self.carga.save()
        self.assertContains(self.client.get(url), "CARGA ANULADA")


class InterfazDetalleYReporteTests(TestCase):
    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )
        self.carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("carga.csv", b"contenido"),
            usuario=self.admin, empresa=empresa, gestion=gestion, estado="con_observaciones",
            registros_con_error=2,
        )
        ErrorValidacion.objects.create(carga=self.carga, fila=3, campo="cuenta", tipo="aviso", descripcion="x")

    def test_detalle_tiene_navegacion_flotante_con_contadores(self):
        respuesta = self.client.get(reverse("registros:detalle_carga", args=[self.carga.id]))
        self.assertContains(respuesta, 'id="nav-secciones"')
        self.assertContains(respuesta, 'href="#avisos"')
        self.assertContains(respuesta, 'id="errores"')

    def test_mensajes_se_muestran_como_notificacion_flotante(self):
        self.client.post(reverse("registros:carga_confirmar_validacion", args=[self.carga.id]), follow=False)
        respuesta = self.client.get(reverse("registros:detalle_carga", args=[self.carga.id]))
        self.assertContains(respuesta, "aviso-flotante error")

    def test_reporte_usa_logo_para_ambos_temas(self):
        respuesta = self.client.get(reverse("registros:reporte_observaciones", args=[self.carga.id]))
        self.assertContains(respuesta, 'class="reporte"')
        self.assertContains(respuesta, "logo-sts-oscuro.png")


class EstadoImportacionRevisionTests(TestCase):
    """Estado de la carga separado en importación del archivo y revisión del auditor."""

    def setUp(self):
        self.admin = _crear_administrador()
        self.client.login(username="admin", password="Clave-Segura123")
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa X")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31)
        )

    def _carga(self, estado, validos, errores, avisos_pendientes=0):
        carga = CargaArchivo.objects.create(
            archivo=SimpleUploadedFile("c.csv", b"x"), usuario=self.admin, empresa=self.empresa,
            gestion=self.gestion, estado=estado, registros_validos=validos, registros_con_error=errores,
        )
        for fila in range(avisos_pendientes):
            ErrorValidacion.objects.create(carga=carga, fila=fila + 2, campo="cuenta", tipo="aviso", descripcion="x")
        return carga

    def test_combinaciones(self):
        casos = [
            (self._carga("con_observaciones", 100, 0, avisos_pendientes=3), "completa", "en_revision"),
            (self._carga("con_observaciones", 100, 0), "completa", "por_confirmar"),
            (self._carga("con_observaciones", 90, 10, avisos_pendientes=1), "parcial", "en_revision"),
            (self._carga("validado", 90, 10), "parcial", "validada"),
            (self._carga("con_errores", 0, 5), "fallida", "no_aplica"),
            (self._carga("anulada", 100, 0), "completa", "anulada"),
        ]
        for carga, importacion, revision in casos:
            self.assertEqual(carga.estado_importacion, importacion, carga)
            self.assertEqual(carga.estado_revision_carga, revision, carga)

    def test_filtros_del_listado_coinciden_con_las_etiquetas(self):
        parcial = self._carga("con_observaciones", 90, 10, avisos_pendientes=2)
        por_confirmar = self._carga("con_observaciones", 100, 0)
        fallida = self._carga("con_errores", 0, 5)
        url = reverse("registros:cargar")

        def ids(parametros):
            return {c.id for c in self.client.get(url, parametros).context["cargas_anteriores"]}

        self.assertEqual(ids({"importacion": "parcial"}), {parcial.id})
        self.assertEqual(ids({"importacion": "fallida"}), {fallida.id})
        self.assertEqual(ids({"revision": "en_revision"}), {parcial.id})
        self.assertEqual(ids({"revision": "por_confirmar"}), {por_confirmar.id})

    def test_detalle_y_listado_muestran_ambas_etiquetas(self):
        carga = self._carga("con_observaciones", 90, 10, avisos_pendientes=2)
        respuesta = self.client.get(reverse("registros:detalle_carga", args=[carga.id]))
        self.assertContains(respuesta, "chip-parcial")
        self.assertContains(respuesta, "10 rechazadas")
        self.assertContains(respuesta, "2 pendientes")
        respuesta = self.client.get(reverse("registros:cargar"))
        self.assertContains(respuesta, "chip-en_revision")


class RevisarBaseDatosCommandTests(TestCase):
    def test_base_limpia_no_reporta_restos(self):
        from io import StringIO

        from django.core.management import call_command

        salida = StringIO()
        call_command("revisar_base_datos", stdout=salida)
        self.assertIn("No hay restos", salida.getvalue())
