"""Prueba de integración de punta a punta (actividad 19).

Recorre el trabajo real de una auditora con todos los módulos unidos: inicio de sesión
con verificación en dos pasos y JWT, registro de la empresa y su gestión, carga del libro,
validación, análisis con el modelo, revisión de atípicas y avisos, reportes y descarga.
"""
import json
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from analisis import servicio as servicio_analisis
from analisis.models import AnalisisCarga, ResultadoTransaccion
from analisis.tests import paquete_de_prueba
from registros.models import CargaArchivo, EmpresaAuditada, ErrorValidacion, Gestion, HistorialCambio
from reportes.models import ReporteGenerado
from usuarios.models import CodigoVerificacion

LIBRO = (
    "fecha,cuenta,comprobante,glosa,debe,haber\n"
    "02/03/2023,1101,C-1,Venta al contado,100,0\n"
    "03/03/2023,1101,C-2,Venta al contado,110,0\n"
    "06/03/2023,1101,C-3,Venta al contado,120,0\n"
    "07/03/2023,1101,C-4,Venta al contado,130,0\n"
    "08/03/2023,1101,,Venta sin comprobante,140,0\n"
    "09/03/2023,1101,C-9,Depósito extraordinario,95000,0\n"
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), ANALIZAR_CON_MODELO=True)
class FlujoCompletoDeAuditoriaTests(TestCase):
    def test_de_la_carga_del_libro_al_reporte_de_hallazgos(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        auditora = User.objects.create_user(
            username="auditora", password="Clave-Segura123", email="auditora@sts.com")
        auditora.groups.add(grupo)

        # 1. Inicio de sesión: contraseña, código por correo y JWT.
        respuesta = self.client.post(reverse("login"), {"username": "auditora", "password": "Clave-Segura123"})
        self.assertRedirects(respuesta, reverse("verificar_codigo"))
        self.assertEqual(len(mail.outbox), 1)
        codigo = CodigoVerificacion.objects.filter(usuario=auditora, usado=False).latest("creado").codigo
        respuesta = self.client.post(reverse("verificar_codigo"), {"codigo": codigo})
        self.assertIn("token_acceso", respuesta.cookies)

        # 2. El Administrador registra la empresa con su gestión 2023 (RF-09: solo él gestiona empresas).
        socia = User.objects.create_user(username="socia", password="Clave-Segura123")
        socia.groups.add(Group.objects.get_or_create(name="Administrador")[0])
        cliente_socia = self.client_class()
        cliente_socia.force_login(socia)
        cliente_socia.post(reverse("registros:empresa_crear"), {
            "nombre": "Comercial Andina S.R.L.", "nit": "1020304", "rubro": "", "categoria_cierre": "general",
            "contacto_nombre": "", "contacto_email": "", "contacto_telefono": "", "anio_gestion_inicial": "2023",
        })
        empresa = EmpresaAuditada.objects.get(nit="1020304")
        gestion = Gestion.objects.get(anio=2023)
        self.assertTrue(HistorialCambio.objects.filter(modelo="EmpresaAuditada", usuario=socia).exists())

        # 3. Sube el libro: se valida y el modelo lo analiza.
        servicio_analisis._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio_analisis, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": empresa.id, "gestion": gestion.id,
                "archivo": SimpleUploadedFile("libro_2023.csv", LIBRO.encode("utf-8")),
            })
        carga = CargaArchivo.objects.get(empresa=empresa)
        self.assertEqual(carga.registros_validos, 6)
        aviso = ErrorValidacion.objects.get(carga=carga, campo="comprobante")
        analisis = AnalisisCarga.objects.get(carga=carga)
        self.assertEqual(analisis.estado, "completado")
        atipica = ResultadoTransaccion.objects.get(registro__carga=carga, atipica=True)
        self.assertEqual(atipica.registro.glosa, "Depósito extraordinario")
        self.assertTrue(atipica.lista_motivos)

        # 4. Revisa: la atípica es relevante y el aviso sin comprobante es un hallazgo.
        respuesta = self.client.post(
            reverse("registros:atipica_revisar", args=[carga.id, atipica.id]),
            {"decision": "relevante", "comentario": "Depósito sin respaldo bancario"},
            HTTP_X_REQUESTED_WITH="fetch")
        self.assertTrue(respuesta.json()["ok"])
        respuesta = self.client.post(
            reverse("registros:aviso_marcar_estado", args=[carga.id, aviso.id]),
            data=json.dumps({"estado": "observado", "comentario": "Venta sin comprobante de respaldo"}),
            content_type="application/json")
        self.assertEqual(respuesta.status_code, 200)

        # 5. Reportes de la gestión: atípicas, gráficos y hallazgos.
        atipicas = self.client.get(reverse("reportes:atipicas_gestion", args=[empresa.id, gestion.id]))
        self.assertContains(atipicas, "Depósito sin respaldo bancario")
        graficos = self.client.get(reverse("reportes:panorama_datos", args=[empresa.id, gestion.id])).json()
        self.assertEqual(graficos["resumen"]["relevantes"], 1)
        hallazgos = self.client.get(reverse("reportes:hallazgos_gestion", args=[empresa.id, gestion.id]))
        self.assertEqual(len(hallazgos.context["hallazgos"]), 2)
        self.assertEqual(hallazgos.context["hallazgos"][0]["importe"], 95000)

        # 6. Descarga el papel de trabajo y queda constancia de todo.
        pdf = self.client.get(reverse("reportes:hallazgos_gestion", args=[empresa.id, gestion.id]),
                              {"formato": "pdf"})
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(
            set(ReporteGenerado.objects.filter(usuario=auditora).values_list("tipo", "formato")),
            {("atipicas", "pantalla"), ("hallazgos", "pantalla"), ("hallazgos", "pdf")})
        self.assertTrue(HistorialCambio.objects.filter(
            modelo="ResultadoTransaccion", objeto_id=atipica.id, valor_nuevo="relevante").exists())

        # 7. El inicio refleja el avance.
        inicio = self.client.get(reverse("home"))
        self.assertEqual(inicio.context["atipicas_por_validar"], 0)
