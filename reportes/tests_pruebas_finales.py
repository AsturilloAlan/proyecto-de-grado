"""Pruebas finales (actividad 20): rendimiento, seguridad (OWASP Top 10) y páginas de error."""
import tempfile
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from analisis import servicio as servicio_analisis
from analisis.models import ResultadoTransaccion
from analisis.tests import paquete_de_prueba
from registros.models import CargaArchivo, EmpresaAuditada, Gestion, RegistroContable

LIBRO = ("fecha,cuenta,comprobante,glosa,debe,haber\n"
         "02/03/2023,1101,C-1,Pago,100,0\n03/03/2023,1101,C-2,Pago,110,0\n"
         "06/03/2023,1101,C-3,Pago,120,0\n"
         "09/03/2023,1101,C-9,<script>alert('xss')</script>,95000,0\n")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), ANALIZAR_CON_MODELO=True)
class PruebasFinalesBase(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        self.auditor = User.objects.create_user(username="aud_final", password="Clave-Segura123")
        self.auditor.groups.add(grupo)
        self.client.login(username="aud_final", password="Clave-Segura123")
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa Final")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        servicio_analisis._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio_analisis, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": self.empresa.id, "gestion": self.gestion.id,
                "archivo": SimpleUploadedFile("libro.csv", LIBRO.encode("utf-8")),
            })
        self.carga = CargaArchivo.objects.latest("id")
        self.atipica = ResultadoTransaccion.objects.get(registro__carga=self.carga, atipica=True)


class RendimientoTests(PruebasFinalesBase):
    def test_medicion_del_flujo_sin_dejar_datos(self):
        registros_antes = RegistroContable.objects.count()
        cargas_antes = CargaArchivo.objects.count()
        salida = StringIO()
        with patch.object(servicio_analisis, "_paquete", return_value=paquete_de_prueba()):
            call_command("medir_rendimiento", filas=300, stdout=salida)
        texto = salida.getvalue()
        for etapa in ("Lectura y validación", "Análisis con el modelo", "Reporte de atípicas",
                      "Gráficos de la gestión", "Exportación a PDF", "Exportación a Excel"):
            self.assertIn(etapa, texto)
        self.assertIn("Registros válidos: 300", texto)
        self.assertEqual(RegistroContable.objects.count(), registros_antes)
        self.assertEqual(CargaArchivo.objects.count(), cargas_antes)


class SeguridadOWASPTests(PruebasFinalesBase):
    """Controles de la lista OWASP Top 10 (2021) aplicables al sistema."""

    def _urls_protegidas(self):
        e, g, c = self.empresa.id, self.gestion.id, self.carga.id
        return [
            reverse("home"), reverse("registros:cargar"), reverse("registros:detalle_carga", args=[c]),
            reverse("registros:empresas_lista"), reverse("reportes:indice"),
            reverse("reportes:atipicas_gestion", args=[e, g]), reverse("reportes:hallazgos_gestion", args=[e, g]),
            reverse("reportes:observaciones_gestion", args=[e, g]), reverse("reportes:panorama_datos", args=[e, g]),
            reverse("reportes:comparativo", args=[e]) + "?formato=excel", reverse("usuarios_lista"),
        ]

    def test_a01_control_de_acceso_sin_sesion(self):
        anonimo = Client()
        for url in self._urls_protegidas():
            respuesta = anonimo.get(url)
            self.assertEqual(respuesta.status_code, 302, url)
            self.assertIn(reverse("login"), respuesta["Location"], url)

    def test_a01_usuario_sin_rol_no_ve_informacion_contable(self):
        User.objects.create_user(username="sin_rol", password="Clave-Segura123")
        cliente = Client()
        cliente.login(username="sin_rol", password="Clave-Segura123")
        for url in self._urls_protegidas()[1:]:
            respuesta = cliente.get(url)
            self.assertNotEqual(respuesta.status_code, 200, url)

    def test_a01_csrf_obligatorio_en_acciones(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.login(username="aud_final", password="Clave-Segura123")
        respuesta = cliente.post(reverse("registros:atipica_revisar", args=[self.carga.id, self.atipica.id]),
                                 {"decision": "relevante"})
        self.assertEqual(respuesta.status_code, 403)
        self.atipica.refresh_from_db()
        self.assertEqual(self.atipica.revision, "pendiente")

    def test_a03_texto_del_cliente_no_se_ejecuta_en_pantalla(self):
        for url in (reverse("reportes:atipicas_gestion", args=[self.empresa.id, self.gestion.id]),
                    reverse("registros:detalle_carga", args=[self.carga.id]) + "?pestana=atipicas"):
            respuesta = self.client.get(url)
            self.assertNotContains(respuesta, "<script>alert('xss')</script>")
            self.assertContains(respuesta, "&lt;script&gt;")

    def test_a03_parametros_maliciosos_no_rompen_las_consultas(self):
        url = reverse("reportes:panorama_datos", args=[self.empresa.id, self.gestion.id])
        respuesta = self.client.get(url, {"cuenta": "1; DROP TABLE registros_registrocontable",
                                          "desde": "' OR 1=1 --"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(RegistroContable.objects.exists())

    def test_a05_encabezados_de_seguridad(self):
        respuesta = self.client.get(reverse("reportes:indice"))
        self.assertEqual(respuesta["X-Frame-Options"], "DENY")
        self.assertEqual(respuesta["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", respuesta["Cache-Control"])

    def test_a05_paginas_de_error_propias_sin_detalles_tecnicos(self):
        respuesta = self.client.get("/esta-pagina-no-existe/")
        self.assertEqual(respuesta.status_code, 404)
        self.assertContains(respuesta, "No encontramos esta página", status_code=404)
        self.assertNotContains(respuesta, "Traceback", status_code=404)

    def test_a09_las_decisiones_quedan_registradas(self):
        from registros.models import HistorialCambio

        self.client.post(reverse("registros:atipica_revisar", args=[self.carga.id, self.atipica.id]),
                         {"decision": "no_relevante", "comentario": "Pago habitual"})
        self.assertTrue(HistorialCambio.objects.filter(
            modelo="ResultadoTransaccion", objeto_id=self.atipica.id, usuario=self.auditor).exists())


class SegundaRondaUsabilidadTests(PruebasFinalesBase):
    """Decisiones de la segunda ronda de revisión con la usuaria."""

    def _subir(self, contenido):
        with patch.object(servicio_analisis, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": self.empresa.id, "gestion": self.gestion.id,
                "archivo": SimpleUploadedFile("libro2.csv", contenido.encode("utf-8")),
            })
        return CargaArchivo.objects.latest("id")

    def test_duplicado_exige_tambien_la_misma_glosa(self):
        carga = self._subir("fecha,cuenta,comprobante,glosa,debe,haber\n"
                            "02/03/2023,1101,C-1,Factura proveedor A,250,0\n"
                            "02/03/2023,1101,C-1,Factura proveedor B,250,0\n"
                            "02/03/2023,1101,C-1,Factura proveedor A,250,0\n")
        duplicados = list(carga.errores.filter(campo="duplicado").values_list("fila", flat=True))
        self.assertEqual(len(duplicados), 1)  # solo la tercera, idéntica a la primera

    def test_recalcular_duplicados_quita_solo_los_pendientes_que_sobran(self):
        from registros.models import ErrorValidacion

        carga = self._subir("fecha,cuenta,comprobante,glosa,debe,haber\n"
                            "02/03/2023,1101,C-1,Concepto A,250,0\n"
                            "02/03/2023,1101,C-1,Concepto B,250,0\n")
        # Aviso que habría generado la regla anterior (sin mirar la glosa).
        fila_b = carga.registros.get(glosa="Concepto B").fila_origen
        ErrorValidacion.objects.create(carga=carga, fila=fila_b, campo="duplicado", tipo="aviso",
                                       descripcion="Posible duplicado de la fila 2")
        salida = StringIO()
        call_command("recalcular_duplicados", carga=carga.id, stdout=salida)
        self.assertIn("se quitarían 1", salida.getvalue())
        self.assertEqual(carga.errores.filter(campo="duplicado").count(), 1)  # simulación
        call_command("recalcular_duplicados", carga=carga.id, confirmar=True, stdout=StringIO())
        self.assertEqual(carga.errores.filter(campo="duplicado").count(), 0)

    def test_paso_de_atipicas_se_cumple_con_las_mas_atipicas_revisadas(self):
        url = reverse("registros:detalle_carga", args=[self.carga.id])
        self.assertContains(self.client.get(url), "0 de las 1 más atípicas revisadas")
        self.atipica.revision = "relevante"
        self.atipica.save()
        self.assertContains(self.client.get(url), "Las 1 más atípicas revisadas")
