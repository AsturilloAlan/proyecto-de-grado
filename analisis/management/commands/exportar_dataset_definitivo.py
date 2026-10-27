"""Respaldo del dataset definitivo: registros, variables del modelo, resultado y revisión.

Genera una carpeta con:
    dataset_definitivo.csv   una fila por registro de cada carga, con su uso
                             (entrenamiento o aplicación), las 12 variables del modelo,
                             el puntaje, si es atípica, sus motivos y la revisión del auditor.
    ficha_dataset.md         resumen: cargas, registros por gestión, modelo, métricas de prueba
                             y diccionario de columnas.
    muestra_dataset.csv      muestra corta para presentar (10 normales y 10 atípicas por carga).
    one_class_svm.json       copia de los metadatos del modelo entrenado.

Uso:
    python manage.py exportar_dataset_definitivo              # todas las cargas válidas
    python manage.py exportar_dataset_definitivo 45 44 50     # solo esas cargas
    python manage.py exportar_dataset_definitivo --salida D:/respaldo

Contiene datos de clientes: guardar fuera del repositorio (no subir a Git).
"""
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from analisis.modelo import ARCHIVO_METADATOS, _numerico, _particion, carpeta_modelos
from analisis.models import ResultadoTransaccion
from analisis.variables import COLUMNAS_MODELO, construir_variables
from registros.dataset import COLUMNAS_DATASET, filas_dataset
from registros.models import CargaArchivo
from registros.views import _celda_csv_segura

COLUMNAS_RESULTADO = ["puntaje", "nivel", "nivel_texto", "atipica", "motivo_principal",
                      "motivos", "revision", "comentario_revision", "revisado_por"]
COLUMNAS = (["carga_id", "empresa", "gestion", "uso", "particion"] + COLUMNAS_DATASET
            + [c for c in COLUMNAS_MODELO] + COLUMNAS_RESULTADO)

DICCIONARIO = {
    "uso": "entrenamiento: carga usada para entrenar el modelo; aplicacion: carga analizada con el modelo ya entrenado",
    "particion": "solo en cargas de entrenamiento: entrenamiento (el modelo aprendió con esa fila), prueba (reservada para medirlo) u observado (excluido por el auditor)",
    "aviso_duplicado / aviso_naturaleza": "1 si la regla del sistema marcó la fila",
    "aviso_observado": "1 si el auditor confirmó un aviso de la fila como observado (excluida del entrenamiento)",
    "monto": "debe - haber",
    "monto_abs": "valor absoluto del monto",
    "cierre_mes": "1 si es último día del mes o 1 de enero",
    "dia_no_habil": "1 si es sábado, domingo o feriado fijo (salvo cierre)",
    "cuenta_frecuencia": "cantidad de movimientos de la cuenta en la carga",
    "monto_zscore_cuenta": "desviación del monto respecto al promedio de su cuenta",
    "comprobante_vacio": "1 si no tiene número de comprobante",
    "tipo_*": "1 según el tipo de cuenta (activo, pasivo, patrimonio, ingreso, gasto)",
    "puntaje": "puntaje de anomalía del One-Class SVM (mayor = más atípico)",
    "nivel": "nivel de anomalía de 0 a 1; nivel_texto: Muy alto >= 0,9, Alto >= 0,7, Medio",
    "atipica": "1 si está en el porcentaje más atípico (umbral del modelo)",
    "motivos": "explicación en lenguaje de auditoría, separada por ' | '",
    "revision": "decisión del auditor: pendiente, relevante (observada: es hallazgo), seguimiento (útil, falta revisar con otros documentos) o no_relevante (válida, sin observación)",
}


def _cargas_entrenamiento(metadatos):
    """{id: (anio, registros)} de las cargas con que se entrenó el modelo."""
    salida = {}
    for nombre, registros in metadatos.get("cargas", {}).items():
        if m := re.match(r"carga_(\d+)_(\d{4})", nombre):
            salida[int(m.group(1))] = (int(m.group(2)), int(registros))
    return salida


def _uso(carga, total, entrenamiento, excluidos):
    """entrenamiento si es una carga de entrenamiento o el mismo libro vuelto a subir
    (misma gestión y misma cantidad de registros, salvo los observados excluidos)."""
    if carga.id in entrenamiento:
        return "entrenamiento", ""
    for carga_id, (anio, registros) in entrenamiento.items():
        if carga.gestion.anio == anio and 0 <= total - registros <= excluidos:
            return "entrenamiento", f"mismo libro que la carga #{carga_id}"
    return "aplicacion", ""


class Command(BaseCommand):
    help = "Exporta el dataset definitivo (registros + variables + resultado del modelo + revisión)."

    def add_arguments(self, parser):
        parser.add_argument("cargas", nargs="*", type=int,
                            help="ID de las cargas (por defecto, todas las no anuladas).")
        parser.add_argument("--salida", default=None,
                            help="Carpeta destino (por defecto dataset_definitivo_<fecha>).")

    def handle(self, *args, **options):
        ruta_meta = Path(carpeta_modelos()) / ARCHIVO_METADATOS
        metadatos = json.loads(ruta_meta.read_text(encoding="utf-8")) if ruta_meta.exists() else {}
        if not metadatos:
            self.stdout.write(self.style.WARNING("No hay modelo entrenado: se exporta sin metadatos."))
        de_entrenamiento = _cargas_entrenamiento(metadatos)

        cargas = CargaArchivo.objects.select_related("empresa", "gestion").order_by("gestion__anio", "id")
        if options["cargas"]:
            cargas = cargas.filter(pk__in=options["cargas"])
            faltan = set(options["cargas"]) - set(cargas.values_list("id", flat=True))
            if faltan:
                raise CommandError(f"No existen las cargas: {sorted(faltan)}")
        else:
            cargas = cargas.exclude(estado__in=["anulada", "pendiente"])
        if not cargas.exists():
            raise CommandError("No hay cargas para exportar.")

        carpeta = Path(options["salida"] or f"dataset_definitivo_{datetime.now():%Y%m%d_%H%M}")
        try:
            carpeta.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise CommandError(f"No se puede crear la carpeta {carpeta}. Revisar que la unidad "
                               "(por ejemplo D: o el USB) exista y esté conectada.")
        resumen = []

        with open(carpeta / "dataset_definitivo.csv", "w", newline="", encoding="utf-8-sig") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(COLUMNAS)
            for carga in cargas:
                filas = list(filas_dataset(carga))
                if not filas:
                    continue
                if carga.estado == "anulada":
                    self.stdout.write(self.style.WARNING(f"La carga #{carga.id} está anulada; se incluye igual."))
                df = pd.DataFrame(filas, columns=COLUMNAS_DATASET)
                # Mismo cálculo que usa el modelo (matriz_variables).
                variables = (construir_variables(_numerico(df))
                             .reindex(columns=COLUMNAS_MODELO, fill_value=0).astype(float).fillna(0.0))

                # Mismo orden que filas_dataset, para unir cada fila con su resultado.
                ids = list(carga.registros.order_by("fila_origen", "id").values_list("id", flat=True))
                resultados = {r.registro_id: r for r in ResultadoTransaccion.objects
                              .filter(registro__carga=carga).select_related("revisado_por")}
                uso, nota = _uso(carga, len(filas), de_entrenamiento,
                                 int(metadatos.get("excluidos_observados") or 0))
                # Misma división que usó entrenar_modelo (misma proporción y semilla).
                particion = [""] * len(filas)
                if uso == "entrenamiento":
                    _, prueba = _particion(len(filas), float(metadatos.get("proporcion_prueba") or 0),
                                           int(metadatos.get("semilla_particion") or 42))
                    en_prueba = set(prueba.tolist())
                    observado = df["aviso_observado"].astype(int).to_numpy() == 1
                    particion = ["observado" if observado[i] else "prueba" if i in en_prueba
                                 else "entrenamiento" for i in range(len(filas))]
                atipicas = revisadas = 0
                for i, (fila, (_, var), registro_id) in enumerate(zip(filas, variables.iterrows(), ids)):
                    r = resultados.get(registro_id)
                    if r:
                        atipicas += r.atipica
                        revisadas += r.atipica and r.revision != "pendiente"
                        res = [round(r.puntaje, 6), round(r.nivel, 4), r.nivel_texto, int(r.atipica),
                               r.motivo_principal,
                               _celda_csv_segura(" | ".join(m.get("texto", "") for m in r.lista_motivos)),
                               r.revision, _celda_csv_segura(r.comentario_revision),
                               r.revisado_por.username if r.revisado_por else ""]
                    else:
                        res = [""] * len(COLUMNAS_RESULTADO)
                    fila = [_celda_csv_segura(v) if isinstance(v, str) else v for v in fila]
                    valores = [round(float(var[c]), 6) for c in COLUMNAS_MODELO]
                    escritor.writerow([carga.id, _celda_csv_segura(carga.empresa.nombre),
                                       carga.gestion.anio, uso, particion[i]] + fila + valores + res)
                resumen.append((carga, uso + (f" ({nota})" if nota else ""), len(filas), len(resultados), atipicas, revisadas))
                self.stdout.write(f"  Carga #{carga.id} ({carga.gestion.anio}, {uso}"
                                  f"{', ' + nota if nota else ''}): {len(filas)} registros")

        self._muestra(carpeta)
        if ruta_meta.exists():
            shutil.copy(ruta_meta, carpeta / ARCHIVO_METADATOS)
        self._ficha(carpeta, resumen, metadatos)
        total = sum(r[2] for r in resumen)
        self.stdout.write(self.style.SUCCESS(f"Listo: {total} registros de {len(resumen)} cargas en {carpeta}"))
        self.stdout.write(self.style.WARNING("Contiene datos del cliente: no subirlo a Git ni compartirlo."))

    def _muestra(self, carpeta, por_grupo=10):
        """Muestra corta para presentar: por cada carga, 10 registros normales y 10 atípicos
        al azar (semilla fija), con las columnas principales."""
        df = pd.read_csv(carpeta / "dataset_definitivo.csv", dtype=str, encoding="utf-8-sig")
        columnas = ["carga_id", "empresa", "gestion", "uso", "particion", "fecha", "cuenta_codigo",
                    "cuenta_nombre", "comprobante", "glosa", "debe", "haber", "nivel_texto",
                    "atipica", "motivos", "revision"]
        partes = []
        for _, grupo in df.groupby("carga_id", sort=False):
            for valor in ("0", "1"):
                sub = grupo[grupo["atipica"].fillna("") == valor]
                partes.append(sub.sample(n=min(por_grupo, len(sub)), random_state=42))
        if partes:
            pd.concat(partes)[columnas].to_csv(carpeta / "muestra_dataset.csv", index=False, encoding="utf-8-sig")

    def _ficha(self, carpeta, resumen, meta):
        lineas = [f"# Dataset definitivo", "", f"Generado: {datetime.now():%d/%m/%Y %H:%M}", "",
                  "## Cargas", "",
                  "| Carga | Empresa | Gestión | Uso | Registros | Analizados | Atípicas | Atípicas revisadas |",
                  "|---|---|---|---|---|---|---|---|"]
        for c, uso, n, analizados, atip, rev in resumen:
            lineas.append(f"| {c.id} | {c.empresa.nombre} | {c.gestion.anio} | {uso} | {n} | {analizados} | {atip} | {rev} |")
        if meta:
            conf = meta.get("configuracion", {})
            lineas += ["", "## Modelo", "",
                       f"- Algoritmo: {meta.get('algoritmo')} ({', '.join(f'{k}={v}' for k, v in conf.items())})",
                       f"- Entrenado con: {', '.join(f'{k} ({v})' for k, v in meta.get('cargas', {}).items())}",
                       f"- Registros de entrenamiento: {meta.get('registros_entrenamiento')} "
                       f"(excluidos por observados: {meta.get('excluidos_observados')})",
                       f"- Vectores de soporte: {meta.get('vectores_soporte')}; tiempo: {meta.get('segundos')} s",
                       f"- Marca como atípicas el {meta.get('porcentaje_atipicas')} % con mayor puntaje "
                       f"(umbral {meta.get('umbral_atipica')})",
                       f"- scikit-learn {meta.get('version_sklearn')}; fecha {meta.get('fecha')}"]
            prueba = meta.get("prueba")
            if prueba:
                m = prueba["metricas"]
                lineas += [
                    f"- División: {100 - meta.get('proporcion_prueba', 0) * 100:.0f} % entrenamiento / "
                    f"{meta.get('proporcion_prueba', 0) * 100:.0f} % prueba (semilla {meta.get('semilla_particion')})",
                    f"- Prueba: {prueba['registros_normales']} registros normales + {prueba['casos_conocidos']} casos atípicos conocidos",
                    f"- Métricas en prueba: AUC-PR {m.get('auc_pr')}, AUC-ROC {m.get('auc_roc')}, "
                    f"precisión {m.get('precision')}, recall {m.get('recall')}, F1 {m.get('f1')}",
                    f"- Falsas alarmas sobre registros normales: {prueba.get('falsas_alarmas')}",
                ]
        lineas += ["", "## Registros por gestión", "",
                   "| Gestión | Registros |", "|---|---|"]
        por_anio = {}
        for c, _uso, n, *_ in resumen:
            por_anio[c.gestion.anio] = por_anio.get(c.gestion.anio, 0) + n
        lineas += [f"| {anio} | {n} |" for anio, n in sorted(por_anio.items())]
        lineas += ["", "## Columnas", ""] + [f"- **{k}**: {v}" for k, v in DICCIONARIO.items()]
        (carpeta / "ficha_dataset.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
