import io
"""Pruebas del modelo elegido (configuración, entrenamiento, guardado y puntuación)."""
import tempfile

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from analisis import modelo
from analisis.variables import COLUMNAS_MODELO


def dataset_sintetico(registros=400, semilla=0, tipos=("activo", "gasto", "ingreso")):
    """Libro pequeño con montos habituales por cuenta y columnas del dataset exportado."""
    rng = np.random.default_rng(semilla)
    cuentas = {"1101": "activo", "5101": "gasto", "4101": "ingreso", "2101": "pasivo"}
    cuentas = {codigo: tipo for codigo, tipo in cuentas.items() if tipo in tipos}
    codigos = rng.choice(list(cuentas), size=registros)
    montos = rng.normal(1000, 80, size=registros).round(2)
    debe = np.where(rng.random(registros) < 0.5, montos, 0.0)
    return pd.DataFrame({
        "fila_origen": np.arange(1, registros + 1),
        "fecha": pd.date_range("2023-01-02", periods=registros, freq="D").date.astype(str),
        "cuenta_codigo": codigos,
        "cuenta_nombre": [f"Cuenta {c}" for c in codigos],
        "cuenta_tipo": [cuentas[c] for c in codigos],
        "comprobante": [f"C-{i}" for i in range(registros)],
        "glosa": "Operación",
        "debe": debe,
        "haber": np.where(debe > 0, 0.0, montos),
        "aviso_duplicado": 0, "aviso_naturaleza": 0, "aviso_observado": 0,
    })


class MatrizVariablesTests(SimpleTestCase):
    def test_columnas_fijas_aunque_falte_un_tipo_de_cuenta(self):
        X = modelo.matriz_variables(dataset_sintetico(tipos=("activo",)))
        self.assertEqual(X.shape[1], len(COLUMNAS_MODELO))
        indice = COLUMNAS_MODELO.index("tipo_gasto")
        self.assertTrue((X[:, indice] == 0).all())

    def test_acepta_importes_como_texto(self):
        df = dataset_sintetico()
        df["debe"] = df["debe"].astype(str)
        self.assertFalse(np.isnan(modelo.matriz_variables(df)).any())


class ConfiguracionTests(SimpleTestCase):
    def test_elige_una_de_las_candidatas_y_la_guarda(self):
        modelo.VALORES_NU, modelo.VALORES_GAMMA = [0.05, 0.1], ["scale"]
        try:
            _, ranking, elegida = modelo.configurar(
                [("prueba", dataset_sintetico())], repeticiones=1, proporcion_por_tipo=0.01)
        finally:
            modelo.VALORES_NU, modelo.VALORES_GAMMA = [0.01, 0.05, 0.10], ["scale", 0.01, 0.5]
        self.assertEqual(len(ranking), 2)
        self.assertIn(elegida["nu"], (0.05, 0.1))
        with tempfile.TemporaryDirectory() as carpeta:
            modelo.guardar_configuracion(elegida, ranking, ["prueba"], carpeta)
            leida, existe = modelo.leer_configuracion(carpeta)
        self.assertTrue(existe)
        self.assertEqual(leida, elegida)

    def test_sin_configuracion_guardada_usa_la_base(self):
        with tempfile.TemporaryDirectory() as carpeta:
            configuracion, existe = modelo.leer_configuracion(carpeta)
        self.assertFalse(existe)
        self.assertEqual(configuracion, modelo.CONFIGURACION_BASE)


class EntrenamientoTests(SimpleTestCase):
    def test_excluye_observados_y_cuenta_registros(self):
        df = dataset_sintetico()
        df.loc[:9, "aviso_observado"] = 1
        paquete = modelo.entrenar([("a", df), ("b", dataset_sintetico(semilla=1))],
                                  modelo.CONFIGURACION_BASE, proporcion_prueba=0)
        meta = paquete["metadatos"]
        self.assertEqual(meta["excluidos_observados"], 10)
        self.assertEqual(meta["registros_entrenamiento"], 790)
        self.assertEqual(meta["columnas"], COLUMNAS_MODELO)
        self.assertNotIn("prueba", meta)

    def test_reserva_el_20_por_ciento_para_prueba_y_la_mide(self):
        paquete = modelo.entrenar([("a", dataset_sintetico()), ("b", dataset_sintetico(semilla=1))],
                                  modelo.CONFIGURACION_BASE)
        meta = paquete["metadatos"]
        self.assertEqual(meta["registros_entrenamiento"], 640)  # 80 % de 800
        self.assertEqual(meta["prueba"]["registros_normales"], 160)  # 20 % de 800
        self.assertGreater(meta["prueba"]["casos_conocidos"], 0)
        for clave in ("auc_pr", "auc_roc", "precision", "recall", "f1"):
            self.assertIn(clave, meta["prueba"]["metricas"])
        otra = modelo.entrenar([("a", dataset_sintetico()), ("b", dataset_sintetico(semilla=1))],
                               modelo.CONFIGURACION_BASE)["metadatos"]
        self.assertEqual(otra["prueba"]["metricas"], meta["prueba"]["metricas"])

    def test_particion_no_mezcla_entrenamiento_y_prueba(self):
        entrenamiento, prueba = modelo._particion(1000, 0.2, 42)
        self.assertEqual((len(entrenamiento), len(prueba)), (800, 200))
        self.assertFalse(set(entrenamiento) & set(prueba))

    def test_guardar_cargar_y_puntuar_sin_reentrenar(self):
        paquete = modelo.entrenar([("a", dataset_sintetico())], modelo.CONFIGURACION_BASE)
        nueva = dataset_sintetico(registros=60, semilla=5)
        nueva.loc[0, "debe"] = 900000.0  # monto fuera de lo habitual
        with tempfile.TemporaryDirectory() as carpeta:
            modelo.guardar(paquete, carpeta)
            resultado = modelo.puntuar(nueva, modelo.cargar(carpeta))
        self.assertEqual(len(resultado), 60)
        self.assertTrue(resultado["nivel"].between(0, 1).all())
        self.assertEqual(resultado.loc[resultado["nivel"].idxmax(), "fila_origen"], 1)
        self.assertEqual(int(resultado.loc[0, "atipica"]), 1)

    def test_excel_para_power_bi_tiene_una_hoja_por_grafico(self):
        from analisis.powerbi import exportar_evaluacion

        promedio = pd.DataFrame({
            "dataset": ["carga_1_2023"] * 2, "modelo": ["Isolation Forest", "One-Class SVM"],
            "registros": [100, 100], "segundos": [0.4, 4.1],
            "inyectadas_auc_pr": [0.17, 0.29], "inyectadas_f1": [0.16, 0.22],
            "inyectadas_precision": [0.1, 0.13], "inyectadas_recall": [0.5, 0.67],
        })
        por_tipo = pd.DataFrame({"dataset": ["carga_1_2023"] * 2, "repeticion": [1, 1],
                                 "modelo": ["Isolation Forest", "One-Class SVM"],
                                 "tipo": ["duplicado", "duplicado"], "recall": [0.04, 0.04]})
        puntajes = {"carga_1_2023": pd.DataFrame({
            "inyectada": [0, 0, 1], "clase_anomalia": ["", "", "monto_extremo"], "aviso": [0, 0, 0],
            "Isolation Forest": [0.1, 0.2, 0.9], "One-Class SVM": [0.2, 0.25, 1.0]})}
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = exportar_evaluacion(f"{carpeta}/datos.xlsx", promedio, por_tipo, puntajes)
            hojas = pd.read_excel(ruta, sheet_name=None)
        self.assertTrue({"Metricas", "Tiempo", "Recall_por_tipo", "Histograma_puntajes"} <= set(hojas))
        histograma = hojas["Histograma_puntajes"]
        normales = histograma[(histograma["Modelo"] == "One-Class SVM")
                              & (histograma["Grupo"] == "Registros normales")]
        self.assertAlmostEqual(normales["Proporción"].sum(), 1.0, places=3)
        self.assertEqual(set(hojas["Metricas"]["Gestión"].astype(str)), {"2023"})

    def test_cargar_sin_modelo_da_error_claro(self):
        with tempfile.TemporaryDirectory() as carpeta:
            with self.assertRaises(FileNotFoundError):
                modelo.cargar(carpeta)


# Actividad 14: integración del modelo con el flujo de carga

from datetime import date, timedelta  # noqa: E402
from unittest.mock import patch  # noqa: E402

from django.contrib.auth.models import Group, User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402
from django.utils import timezone  # noqa: E402

from analisis import servicio  # noqa: E402
from analisis.models import AnalisisCarga, ResultadoTransaccion  # noqa: E402
from registros import procesamiento  # noqa: E402
from registros.models import CargaArchivo, EmpresaAuditada, Gestion  # noqa: E402

MEDIA_PRUEBAS = tempfile.mkdtemp()
INDICE_MONTO = COLUMNAS_MODELO.index("monto_abs")


class _Identidad:
    def transform(self, X):
        return X


class _ModeloPorMonto:
    """Modelo de prueba: cuanto mayor el monto, más atípico; atípica si supera 10.000."""

    def score_samples(self, X):
        return -X[:, INDICE_MONTO]

    def predict(self, X):
        return np.where(X[:, INDICE_MONTO] > 10000, -1, 1)


def paquete_de_prueba():
    return {
        "escalador": _Identidad(),
        "modelo": _ModeloPorMonto(),
        "metadatos": {
            "algoritmo": "One-Class SVM",
            "configuracion": {"kernel": "rbf", "nu": 0.05, "gamma": "scale"},
            "puntaje_minimo": 0.0,
            "puntaje_p999": 100000.0,
            "fecha": "2026-10-09T12:22:00",
        },
    }


CSV_PRUEBA = (
    "fecha,cuenta,glosa,debe,haber\n"
    "02/01/2023,1101,Pago habitual,100,0\n"
    "03/01/2023,1101,Pago habitual,120,0\n"
    "04/01/2023,1101,Pago extraordinario,50000,0\n"
)


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS, ANALIZAR_CON_MODELO=True)
class IntegracionModeloTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        self.auditor = User.objects.create_user(username="auditor_ml", password="Clave-Segura123")
        self.auditor.groups.add(grupo)
        self.client.login(username="auditor_ml", password="Clave-Segura123")
        self.empresa = EmpresaAuditada.objects.create(nombre="Empresa ML")
        self.gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        servicio._modelo_en_memoria.update(marca=None, paquete=None)

    def _subir(self, contenido=CSV_PRUEBA):
        self.client.post(reverse("registros:cargar"), {
            "empresa": self.empresa.id, "gestion": self.gestion.id,
            "archivo": SimpleUploadedFile("libro.csv", contenido.encode("utf-8")),
        })
        return CargaArchivo.objects.latest("id")

    def test_al_terminar_la_carga_se_analiza_y_guarda_cada_puntaje(self):
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
        analisis = AnalisisCarga.objects.get(carga=carga)
        self.assertEqual(analisis.estado, "completado")
        self.assertEqual(analisis.total_registros, 3)
        self.assertEqual(analisis.total_atipicas, 1)
        self.assertEqual(analisis.configuracion, "nu=0.05, gamma=scale")
        atipica = ResultadoTransaccion.objects.get(analisis=analisis, atipica=True)
        self.assertEqual(atipica.registro.glosa, "Pago extraordinario")
        self.assertAlmostEqual(atipica.nivel, 0.5)

    def test_pestana_atipicas_muestra_las_transacciones_senaladas(self):
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[carga.id]), {"pestana": "atipicas"})
        self.assertContains(respuesta, "Transacciones atípicas")
        self.assertContains(respuesta, "Pago extraordinario")
        self.assertNotContains(respuesta, "Pago habitual")
        self.assertContains(respuesta, "1</strong> de 3")

    def test_exportar_dataset_definitivo_une_variables_resultado_y_revision(self):
        import csv as _csv, json as _json, tempfile as _tf
        from pathlib import Path as _P
        from django.core.management import call_command
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
        ResultadoTransaccion.objects.filter(atipica=True).update(revision="relevante")
        modelos = _tf.mkdtemp()
        _P(modelos, "one_class_svm.json").write_text(_json.dumps(
            {"algoritmo": "One-Class SVM", "cargas": {f"carga_{carga.id}_2023": 3}}), encoding="utf-8")
        salida = _tf.mkdtemp()
        with patch("analisis.management.commands.exportar_dataset_definitivo.carpeta_modelos",
                   return_value=modelos):
            call_command("exportar_dataset_definitivo", "--salida", salida, stdout=io.StringIO())
        with open(_P(salida, "dataset_definitivo.csv"), encoding="utf-8-sig") as f:
            filas = list(_csv.DictReader(f))
        self.assertEqual(len(filas), 3)
        self.assertEqual({f["uso"] for f in filas}, {"entrenamiento"})
        atipica = [f for f in filas if f["atipica"] == "1"]
        self.assertEqual(len(atipica), 1)
        self.assertEqual(atipica[0]["glosa"], "Pago extraordinario")
        self.assertEqual(atipica[0]["revision"], "relevante")
        self.assertEqual(float(atipica[0]["monto_abs"]), 50000.0)
        self.assertTrue(_P(salida, "ficha_dataset.md").exists())
        self.assertTrue(_P(salida, "one_class_svm.json").exists())
        self.assertTrue(_P(salida, "muestra_dataset.csv").exists())
        self.assertIn("Registros por gestión", _P(salida, "ficha_dataset.md").read_text(encoding="utf-8"))

    def test_sin_modelo_entrenado_se_informa_sin_romper_la_carga(self):
        with patch.object(servicio, "_paquete", side_effect=FileNotFoundError):
            carga = self._subir()
        carga.refresh_from_db()
        self.assertEqual(carga.registros_validos, 3)
        analisis = AnalisisCarga.objects.get(carga=carga)
        self.assertEqual(analisis.estado, "sin_modelo")
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[carga.id]), {"pestana": "atipicas"})
        self.assertContains(respuesta, "entrenar_modelo")

    def test_volver_a_analizar_reemplaza_los_resultados(self):
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
            respuesta = self.client.post(reverse("registros:carga_analizar", args=[carga.id]))
        self.assertRedirects(
            respuesta, reverse("registros:detalle_carga", args=[carga.id]) + "?pestana=atipicas",
            fetch_redirect_response=False)
        self.assertEqual(ResultadoTransaccion.objects.filter(registro__carga=carga).count(), 3)
        self.assertEqual(AnalisisCarga.objects.get(carga=carga).solicitado_por, self.auditor)

    def test_carga_anulada_no_se_vuelve_a_analizar(self):
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
            CargaArchivo.objects.filter(pk=carga.pk).update(estado="anulada")
            self.client.post(reverse("registros:carga_analizar", args=[carga.id]))
        self.assertIsNone(AnalisisCarga.objects.get(carga=carga).solicitado_por)

    def test_la_cola_analiza_despues_de_procesar_las_cargas(self):
        cargas = []
        with override_settings(ANALIZAR_CON_MODELO=False):
            for _ in range(2):
                cargas.append(CargaArchivo.objects.create(
                    archivo=SimpleUploadedFile("libro.csv", CSV_PRUEBA.encode("utf-8")),
                    usuario=self.auditor, empresa=self.empresa, gestion=self.gestion))
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            procesadas = procesamiento.procesar_cola()
        self.assertEqual(procesadas, 4)  # 2 cargas + 2 análisis
        for carga in cargas:
            self.assertEqual(AnalisisCarga.objects.get(carga=carga).estado, "completado")

    def test_analisis_interrumpido_se_marca_como_error(self):
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            carga = self._subir()
        AnalisisCarga.objects.filter(carga=carga).update(
            estado="procesando", inicio=timezone.now() - timedelta(minutes=11))
        analisis = AnalisisCarga.objects.get(carga=carga)
        self.assertTrue(servicio.revisar_interrumpido(analisis))
        self.assertEqual(analisis.estado, "error")


# Actividad 13: validación preliminar con los auditores

from io import StringIO  # noqa: E402

from django.core.management import call_command  # noqa: E402

from registros.models import HistorialCambio  # noqa: E402

CSV_VALIDACION = "fecha,cuenta,glosa,debe,haber\n" + "".join(
    f"{dia:02d}/02/2023,1101,{'Pago extraordinario' if monto > 10000 else 'Pago habitual'} {dia},{monto},0\n"
    for dia, monto in enumerate([100, 110, 20000, 30000, 40000, 120, 130], start=1)
)


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS, ANALIZAR_CON_MODELO=True)
class ValidacionPreliminarTests(TestCase):
    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        self.auditor = User.objects.create_user(username="auditora", password="Clave-Segura123")
        self.auditor.groups.add(grupo)
        self.client.login(username="auditora", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa V")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        servicio._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": empresa.id, "gestion": gestion.id,
                "archivo": SimpleUploadedFile("libro.csv", CSV_VALIDACION.encode("utf-8")),
            })
        self.carga = CargaArchivo.objects.latest("id")
        self.analisis = AnalisisCarga.objects.get(carga=self.carga)
        self.atipicas = list(self.analisis.resultados.filter(atipica=True).order_by("-nivel"))

    def _revisar(self, resultado, decision, comentario="Revisión con respaldo comprobado"):
        return self.client.post(
            reverse("registros:atipica_revisar", args=[self.carga.id, resultado.id]),
            {"decision": decision, "comentario": comentario},
        )

    def test_el_auditor_marca_una_atipica_como_relevante(self):
        respuesta = self._revisar(self.atipicas[0], "relevante", "Monto sin respaldo")
        self.assertEqual(respuesta.status_code, 302)
        self.atipicas[0].refresh_from_db()
        self.assertEqual(self.atipicas[0].revision, "relevante")
        self.assertEqual(self.atipicas[0].comentario_revision, "Monto sin respaldo")
        self.assertEqual(self.atipicas[0].revisado_por, self.auditor)
        self.assertTrue(HistorialCambio.objects.filter(
            modelo="ResultadoTransaccion", objeto_id=self.atipicas[0].id,
            campo="revision", valor_nuevo="relevante").exists())

    def test_decisiones_exigen_justificacion_sin_truncarla(self):
        for decision in ("relevante", "no_relevante", "seguimiento"):
            for comentario in ("", "   ", "....!!", "x" * 301):
                with self.subTest(decision=decision, comentario=comentario[:10]):
                    self._revisar(self.atipicas[0], decision, comentario)
                    self.atipicas[0].refresh_from_db()
                    self.assertEqual(self.atipicas[0].revision, "pendiente")

    def test_metricas_de_precision_preliminar(self):
        self._revisar(self.atipicas[0], "relevante")
        self._revisar(self.atipicas[1], "no_relevante")
        m = self.analisis.metricas_validacion(primeras=2)
        self.assertEqual(m["atipicas"], 3)
        self.assertEqual(m["revisadas"], 2)
        self.assertEqual(m["relevantes"], 1)
        self.assertEqual(m["pendientes"], 1)
        self.assertAlmostEqual(m["precision"], 50.0)
        self.assertAlmostEqual(m["precision_primeras"], 50.0)

    def test_deshacer_vuelve_a_pendiente(self):
        self._revisar(self.atipicas[0], "relevante", "Monto sin respaldo")
        self._revisar(self.atipicas[0], "pendiente")
        self.atipicas[0].refresh_from_db()
        self.assertEqual(self.atipicas[0].revision, "pendiente")
        self.assertIsNone(self.atipicas[0].revisado_por)

    def test_carga_anulada_no_admite_validacion(self):
        CargaArchivo.objects.filter(pk=self.carga.pk).update(estado="anulada")
        self._revisar(self.atipicas[0], "relevante")
        self.atipicas[0].refresh_from_db()
        self.assertEqual(self.atipicas[0].revision, "pendiente")

    def test_no_se_valida_una_transaccion_que_no_es_atipica(self):
        normal = self.analisis.resultados.filter(atipica=False).first()
        respuesta = self._revisar(normal, "relevante")
        self.assertEqual(respuesta.status_code, 404)

    def test_volver_a_analizar_conserva_la_validacion(self):
        self._revisar(self.atipicas[0], "relevante", "Conservar")
        registro_id = self.atipicas[0].registro_id
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:carga_analizar", args=[self.carga.id]))
        nuevo = ResultadoTransaccion.objects.get(registro_id=registro_id)
        self.assertEqual(nuevo.revision, "relevante")
        self.assertEqual(nuevo.comentario_revision, "Conservar")

    def test_pestana_muestra_metricas_y_filtro(self):
        self._revisar(self.atipicas[0], "relevante")
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]),
            {"pestana": "atipicas", "validacion": "pendiente"})
        self.assertContains(respuesta, "validacion-preliminar")
        self.assertContains(respuesta, "1 / 3")
        self.assertEqual(len(respuesta.context["atipicas_pagina"].object_list), 2)

    def test_redireccion_solo_a_la_propia_carga(self):
        respuesta = self.client.post(
            reverse("registros:atipica_revisar", args=[self.carga.id, self.atipicas[0].id]),
            {"decision": "relevante", "siguiente": "https://sitio-externo.com/"},
        )
        self.assertTrue(respuesta["Location"].startswith(
            reverse("registros:detalle_carga", args=[self.carga.id])))

    def test_comando_resumen_validacion(self):
        self._revisar(self.atipicas[0], "relevante")
        self._revisar(self.atipicas[1], "relevante")
        salida = StringIO()
        call_command("resumen_validacion", stdout=salida)
        self.assertIn("100.0 %", salida.getvalue())
        self.assertIn("2 útiles (observadas o en seguimiento) de 2 revisadas", salida.getvalue())

    def test_comando_exporta_el_detalle_con_comentarios(self):
        self._revisar(self.atipicas[0], "no_relevante", "Es un pago anual normal")
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = f"{carpeta}/detalle.csv"
            call_command("resumen_validacion", detalle=ruta, stdout=StringIO())
            with open(ruta, encoding="utf-8-sig") as archivo:
                contenido = archivo.read()
        self.assertIn("Es un pago anual normal", contenido)
        self.assertIn("Válida", contenido)


# Por qué el modelo señala una transacción

import importlib.util  # noqa: E402
from unittest import skipUnless  # noqa: E402

from analisis import explicacion  # noqa: E402


class VariablesDeFechaTests(SimpleTestCase):
    def _fechas(self, *textos):
        return pd.Series(pd.to_datetime(list(textos)))

    def test_cierre_de_mes_y_apertura_no_son_dia_no_habil(self):
        from analisis.variables import es_cierre_o_apertura, es_dia_no_habil

        fechas = self._fechas("2023-04-30", "2023-12-31", "2023-01-01", "2023-04-23",
                              "2023-05-01", "2023-04-26")
        self.assertEqual(es_cierre_o_apertura(fechas).tolist(),
                         [True, True, True, False, False, False])
        # 30/04 domingo de cierre: hábil para el modelo; 23/04 domingo; 01/05 feriado.
        self.assertEqual(es_dia_no_habil(fechas).tolist(),
                         [False, False, False, True, True, False])


class ExplicacionTests(SimpleTestCase):
    def test_un_importe_extremo_tiene_motivo_principal_de_importe(self):
        df = dataset_sintetico(registros=50)
        df.loc[0, ["debe", "haber"]] = [50000.0, 0.0]
        mascara = np.zeros(len(df), dtype=bool)
        mascara[0] = True
        motivos = explicacion.explicar(df, paquete_de_prueba(), mascara)
        self.assertEqual(list(motivos), [0])
        principal = motivos[0][0]
        self.assertEqual(principal["variable"], "monto")
        self.assertIn("50.000,00 en el Debe", principal["texto"])
        self.assertIn("1 % más alto", principal["texto"])

    def test_nunca_da_como_motivo_el_cierre_de_mes_ni_un_dia_cualquiera(self):
        df = dataset_sintetico(registros=60)
        df["fecha"] = "2023-04-30"  # domingo de cierre de mes
        df.loc[0, ["debe", "haber"]] = [50000.0, 0.0]
        motivos = explicacion.explicar(df, paquete_de_prueba(), np.arange(60) == 0)
        textos = " ".join(m["texto"] for m in motivos[0])
        self.assertNotIn("no hábil", textos)
        self.assertNotIn("domingo", textos)

    def test_dia_no_habil_que_no_es_cierre_si_es_motivo(self):
        df = dataset_sintetico(registros=60)
        df.loc[0, "fecha"] = "2023-04-23"  # domingo a mitad de mes
        motivos = explicacion.explicar(df, paquete_de_prueba(), np.arange(60) == 0)
        self.assertIn("día no hábil (domingo)", " ".join(m["texto"] for m in motivos[0]))

    def test_sin_motivo_con_sentido_se_dice_honestamente(self):
        df = dataset_sintetico(registros=60)
        # Fila 3: jueves 05/01/2023, importe habitual, con comprobante.
        motivos = explicacion.explicar(df, paquete_de_prueba(), np.arange(60) == 3)
        self.assertEqual(motivos[3][0]["variable"], "combinacion")

    def test_sin_atipicas_no_hay_motivos(self):
        df = dataset_sintetico(registros=20)
        self.assertEqual(explicacion.explicar(df, paquete_de_prueba(), np.zeros(20, dtype=bool)), {})

    @skipUnless(importlib.util.find_spec("sklearn"), "requiere scikit-learn")
    def test_con_el_modelo_real_un_monto_extremo_se_explica_por_el_importe(self):
        paquete = modelo.entrenar([("a", dataset_sintetico())], modelo.CONFIGURACION_BASE)
        nueva = dataset_sintetico(registros=60, semilla=5)
        nueva.loc[0, ["debe", "haber"]] = [900000.0, 0.0]
        motivos = explicacion.explicar(nueva, paquete, np.arange(60) == 0)
        variables = [m["variable"] for m in motivos[0]]
        self.assertIn(variables[0], ("monto", "monto_cuenta"))
        self.assertLessEqual(len(variables), explicacion.MAXIMO_MOTIVOS)

    def test_modelo_entrenado_con_otras_variables_se_rechaza(self):
        paquete = paquete_de_prueba()
        paquete["metadatos"]["columnas"] = ["monto", "dia_semana"]
        with self.assertRaises(modelo.ModeloDesactualizado):
            modelo.puntuar(dataset_sintetico(registros=10), paquete)


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS, ANALIZAR_CON_MODELO=True)
class MotivosEnPantallaTests(TestCase):
    def test_la_pestana_muestra_por_que_es_atipica(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        usuario = User.objects.create_user(username="aud_motivos", password="Clave-Segura123")
        usuario.groups.add(grupo)
        self.client.login(username="aud_motivos", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa M")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        servicio._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": empresa.id, "gestion": gestion.id,
                "archivo": SimpleUploadedFile("libro.csv", CSV_PRUEBA.encode("utf-8")),
            })
        carga = CargaArchivo.objects.latest("id")
        atipica = ResultadoTransaccion.objects.get(registro__carga=carga, atipica=True)
        self.assertEqual(atipica.lista_motivos[0]["variable"], "monto")
        self.assertEqual(ResultadoTransaccion.objects.filter(
            registro__carga=carga, atipica=False).exclude(motivos="").count(), 0)
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[carga.id]), {"pestana": "atipicas"})
        self.assertContains(respuesta, "Por qué la señala el modelo")
        self.assertContains(respuesta, "Importe de 50.000,00 en el Debe")


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS, ANALIZAR_CON_MODELO=True)
class RevisionAgilTests(TestCase):
    """Revisar sin recargar, ver el asiento en línea y volver a la lista."""

    def setUp(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        usuario = User.objects.create_user(username="aud_agil", password="Clave-Segura123")
        usuario.groups.add(grupo)
        self.client.login(username="aud_agil", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa A")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        contenido = ("fecha,cuenta,comprobante,glosa,debe,haber\n"
                     "02/01/2023,1101,C-1,Pago habitual,100,0\n"
                     "03/01/2023,1101,C-2,Pago habitual,120,0\n"
                     "04/01/2023,1101,C-9,Pago extraordinario,50000,0\n"
                     "04/01/2023,2101,C-9,Pago extraordinario,0,50000\n")
        servicio._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": empresa.id, "gestion": gestion.id,
                "archivo": SimpleUploadedFile("libro.csv", contenido.encode("utf-8")),
            })
        self.carga = CargaArchivo.objects.latest("id")
        self.atipica = ResultadoTransaccion.objects.filter(
            registro__carga=self.carga, atipica=True).order_by("registro__fila_origen").first()

    def test_validar_por_fetch_devuelve_la_celda_y_las_metricas(self):
        respuesta = self.client.post(
            reverse("registros:atipica_revisar", args=[self.carga.id, self.atipica.id]),
            {"decision": "relevante", "comentario": "Revisar respaldo"},
            HTTP_X_REQUESTED_WITH="fetch",
        )
        datos = respuesta.json()
        self.assertTrue(datos["ok"])
        self.assertIn("Observada", datos["celda"])
        self.assertIn("Revisar respaldo", datos["celda"])
        self.assertIn("validacion-preliminar", datos["metricas"])

    def test_seguimiento_exige_comentario(self):
        url = reverse("registros:atipica_revisar", args=[self.carga.id, self.atipica.id])
        sin = self.client.post(url, {"decision": "seguimiento", "comentario": ""}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(sin.status_code, 400)
        con = self.client.post(url, {"decision": "seguimiento", "comentario": "Pedir contrato de préstamo"},
                               HTTP_X_REQUESTED_WITH="fetch")
        self.assertTrue(con.json()["ok"])
        self.atipica.refresh_from_db()
        self.assertEqual(self.atipica.revision, "seguimiento")
        self.assertEqual(self.atipica.get_revision_display(), "En seguimiento")

    def test_seguimiento_cuenta_como_util_y_no_como_hallazgo(self):
        from reportes import servicio as servicio_reportes

        self.atipica.revision = "seguimiento"
        self.atipica.comentario_revision = "Revisar con el mayor de bancos"
        self.atipica.save()
        metricas = self.atipica.analisis.metricas_validacion()
        self.assertEqual((metricas["seguimiento"], metricas["relevantes"], metricas["precision"]), (1, 1, 100.0))
        datos = servicio_reportes.reporte_hallazgos([self.carga])
        self.assertEqual(datos["hallazgos"], [])
        self.assertEqual([r.id for r in datos["en_seguimiento"]], [self.atipica.id])
        self.assertTrue(datos["es_borrador"])

    def test_ver_asiento_muestra_todas_las_lineas_del_comprobante(self):
        respuesta = self.client.get(
            reverse("registros:atipica_asiento", args=[self.carga.id, self.atipica.id]))
        self.assertContains(respuesta, "Comprobante <strong>C-9</strong>")
        self.assertContains(respuesta, "2 líneas")
        self.assertNotContains(respuesta, "Pago habitual")

    def test_desde_registros_se_puede_volver_a_las_atipicas(self):
        base = reverse("registros:detalle_carga", args=[self.carga.id])
        volver = f"{base}?pestana=atipicas&pagina_atipicas=1"
        respuesta = self.client.get(base, {"pestana": "registros", "fila": 3, "volver": volver})
        self.assertContains(respuesta, "Volver a las atípicas")
        self.assertEqual(respuesta.context["volver_atipicas"], volver)

    def test_volver_no_acepta_enlaces_externos(self):
        base = reverse("registros:detalle_carga", args=[self.carga.id])
        respuesta = self.client.get(
            base, {"pestana": "registros", "volver": "https://sitio-externo.com/?pestana=atipicas"})
        self.assertEqual(respuesta.context["volver_atipicas"], "")
        protocolo_relativo = self.client.get(base, {"pestana": "registros", "volver": "//sitio-externo.com/reportes/"})
        self.assertEqual(protocolo_relativo.context["volver_atipicas"], "")

    def test_desde_un_reporte_se_puede_volver_al_reporte(self):
        base = reverse("registros:detalle_carga", args=[self.carga.id])
        volver = "/reportes/empresa/1/gestion/1/atipicas/?pagina=2#listado"
        respuesta = self.client.get(base, {"pestana": "registros", "fila": 3, "volver": volver})
        self.assertContains(respuesta, "Volver al reporte")
        self.assertEqual(respuesta.context["volver_atipicas"], volver)

    def test_nivel_se_muestra_como_texto(self):
        respuesta = self.client.get(
            reverse("registros:detalle_carga", args=[self.carga.id]), {"pestana": "atipicas"})
        self.assertContains(respuesta, "Ver asiento")
        self.assertIn(self.atipica.nivel_texto, ("Muy alto", "Alto", "Medio"))


class UmbralYTiposTests(SimpleTestCase):
    def test_con_umbral_se_marcan_solo_los_puntajes_que_lo_superan(self):
        paquete = paquete_de_prueba()
        paquete["metadatos"]["umbral_atipica"] = 1000.0  # puntaje = importe en el modelo de prueba
        df = dataset_sintetico(registros=10)
        df["debe"], df["haber"] = [500.0] * 9 + [5000.0], 0.0
        resultado = modelo.puntuar(df, paquete)
        self.assertEqual(resultado["atipica"].tolist(), [0] * 9 + [1])

    @skipUnless(importlib.util.find_spec("sklearn"), "requiere scikit-learn")
    def test_entrenar_guarda_el_umbral_del_porcentaje_elegido(self):
        paquete = modelo.entrenar([("a", dataset_sintetico())], modelo.CONFIGURACION_BASE, 2.0)
        meta = paquete["metadatos"]
        self.assertEqual(meta["porcentaje_atipicas"], 2.0)
        marcadas = modelo.puntuar(dataset_sintetico(), paquete)["atipica"].mean()
        self.assertLessEqual(marcadas, 0.05)


@override_settings(MEDIA_ROOT=MEDIA_PRUEBAS, ANALIZAR_CON_MODELO=True)
class AtipicasPorTipoTests(TestCase):
    def test_se_agrupan_y_filtran_por_motivo_principal(self):
        grupo, _ = Group.objects.get_or_create(name="Auditor")
        usuario = User.objects.create_user(username="aud_tipo", password="Clave-Segura123")
        usuario.groups.add(grupo)
        self.client.login(username="aud_tipo", password="Clave-Segura123")
        empresa = EmpresaAuditada.objects.create(nombre="Empresa T")
        gestion = Gestion.objects.create(
            anio=2023, fecha_inicio=date(2023, 1, 1), fecha_fin=date(2023, 12, 31))
        servicio._modelo_en_memoria.update(marca=None, paquete=None)
        with patch.object(servicio, "_paquete", return_value=paquete_de_prueba()):
            self.client.post(reverse("registros:cargar"), {
                "empresa": empresa.id, "gestion": gestion.id,
                "archivo": SimpleUploadedFile("libro.csv", CSV_VALIDACION.encode("utf-8")),
            })
        carga = CargaArchivo.objects.latest("id")
        url = reverse("registros:detalle_carga", args=[carga.id])
        respuesta = self.client.get(url, {"pestana": "atipicas"})
        tipos = {t["clave"]: t["total"] for t in respuesta.context["tipos_atipica"]}
        # 40.000 es el importe más alto del libro; 20.000 y 30.000 son inusuales para la cuenta.
        self.assertEqual(tipos, {"monto": 1, "monto_cuenta": 2})
        self.assertContains(respuesta, "Importe alto")
        self.assertContains(respuesta, "Inusual para su cuenta")
        filtrada = self.client.get(url, {"pestana": "atipicas", "motivo": "dia_no_habil"})
        self.assertEqual(filtrada.context["motivo_filtro"], "")  # tipo inexistente: se ignora
        filtrada = self.client.get(url, {"pestana": "atipicas", "motivo": "monto_cuenta"})
        self.assertEqual(len(filtrada.context["atipicas_pagina"].object_list), 2)
