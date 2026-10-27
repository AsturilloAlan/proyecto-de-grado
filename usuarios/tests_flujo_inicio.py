from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from analisis.models import AnalisisCarga, ResultadoTransaccion
from registros.models import CargaArchivo, EmpresaAuditada, Gestion, ErrorValidacion, CuentaContable, RegistroContable


class InicioTrabajoPendienteTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user("auditor_flujo")
        self.usuario.groups.add(Group.objects.get(name="Auditor"))
        self.client.force_login(self.usuario)
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa de prueba")
        self.gestion = Gestion.objects.create(anio=2025, fecha_inicio="2025-01-01", fecha_fin="2025-12-31")

    def carga(self, estado="con_observaciones"):
        return CargaArchivo.objects.create(empresa=self.empresa, gestion=self.gestion, usuario=self.usuario, estado=estado, registros_validos=2)

    def test_destinos_distinguen_avisos_y_confirmacion(self):
        avisos, confirmar = self.carga(), self.carga()
        ErrorValidacion.objects.create(carga=avisos, tipo="aviso", fila=1, campo="cuenta", descripcion="Revisar")
        self.carga("anulada")
        respuesta = self.client.get(reverse("home"))
        tareas = respuesta.context["trabajo_pendiente"]
        self.assertEqual([c.id for c in tareas], [avisos.id, confirmar.id])
        self.assertContains(respuesta, f'/registros/cargar/{avisos.id}/?pestana=avisos')
        self.assertContains(respuesta, f'/registros/cargar/{confirmar.id}/#confirmar-carga')

    def test_atipicas_solo_en_carga_confirmada_y_no_duplicada(self):
        carga = self.carga("validado")
        analisis = AnalisisCarga.objects.create(carga=carga, estado="completado")
        cuenta = CuentaContable.objects.create(codigo="1", nombre="Caja", empresa=self.empresa)
        for fila in (1, 2):
            registro = RegistroContable.objects.create(carga=carga, cuenta=cuenta, fecha="2025-01-01", fila_origen=fila, debe=1, haber=0)
            ResultadoTransaccion.objects.create(analisis=analisis, registro=registro, puntaje=-1, nivel=.9, atipica=True)
        respuesta = self.client.get(reverse("home"))
        self.assertEqual([c.id for c in respuesta.context["cargas_atipicas_pendientes"]], [carga.id])
        carga.estado = "anulada"
        carga.save()
        self.assertFalse(self.client.get(reverse("home")).context["cargas_atipicas_pendientes"])

    def test_inicio_vacio_orienta_a_crear_empresa(self):
        # Solo el Administrador registra empresas; el Auditor no ve ese botón.
        self.empresa.delete()
        respuesta = self.client.get(reverse("home"))
        self.assertNotContains(respuesta, "Registrar primera empresa")
        administrador = User.objects.create_user("admin_flujo")
        administrador.groups.add(Group.objects.get(name="Administrador"))
        self.client.force_login(administrador)
        respuesta = self.client.get(reverse("home"))
        self.assertContains(respuesta, "Registrar primera empresa")
        self.assertNotContains(respuesta, 'id="titulo-continuar"')

    def test_error_carga_indica_reseleccionar_archivo(self):
        respuesta = self.client.post(reverse("registros:cargar"), {})
        self.assertContains(respuesta, "seleccionar nuevamente el archivo")

    def test_reporte_vacio_ofrece_siguiente_paso(self):
        respuesta = self.client.get(reverse("reportes:indice"))
        self.assertContains(respuesta, "Todavía no hay reportes disponibles")
        self.assertContains(respuesta, reverse("registros:cargar"))
