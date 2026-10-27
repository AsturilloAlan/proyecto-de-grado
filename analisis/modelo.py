"""Modelo elegido en la evaluación comparativa: One-Class SVM (CRISP-DM: Modelado).

Actividad 11: se fija el algoritmo y se elige su configuración (nu y gamma).
Actividad 12: se entrena una sola vez con las cargas oficiales y se guarda; después se
usa para puntuar cualquier carga sin volver a entrenar.
"""
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from django.conf import settings

from .evaluacion import _metricas_binarias, _metricas_ranking, inyectar_anomalias
from .variables import COLUMNAS_MODELO, construir_variables

ALGORITMO = "One-Class SVM"
CONFIGURACION_BASE = {"kernel": "rbf", "nu": 0.05, "gamma": "scale"}

VALORES_NU = [0.01, 0.05, 0.10]
VALORES_GAMMA = ["scale", 0.01, 0.5]

# La selección final usa el porcentaje de puntajes más altos, no la clasificación de nu.
PORCENTAJE_ATIPICAS = 2.0

# Reserva del 20 % para prueba; la semilla permite repetir la partición.
PROPORCION_PRUEBA = 0.20
SEMILLA_PARTICION = 42

ARCHIVO_MODELO = "one_class_svm.joblib"
ARCHIVO_METADATOS = "one_class_svm.json"
ARCHIVO_CONFIGURACION = "configuracion.json"


def carpeta_modelos():
    """Carpeta local del modelo. Contiene datos de clientes: no se sube al repositorio."""
    return Path(settings.BASE_DIR) / "modelos_ml"


def _numerico(df):
    df = df.copy()
    for columna in ("debe", "haber"):
        df[columna] = pd.to_numeric(df[columna], errors="coerce").fillna(0.0)
    return df


def matriz_variables(df):
    """Dataset de una carga → matriz con las columnas fijas del modelo.

    Las variables se calculan dentro de la propia carga (frecuencia y z-score por cuenta).
    """
    variables = construir_variables(_numerico(df))
    return variables.reindex(columns=COLUMNAS_MODELO, fill_value=0).astype(float).fillna(0.0).to_numpy()


def _nuevo_modelo(configuracion):
    from sklearn.svm import OneClassSVM

    return OneClassSVM(kernel=configuracion["kernel"], nu=configuracion["nu"],
                       gamma=configuracion["gamma"])


def _escalar(X):
    from sklearn.preprocessing import StandardScaler

    return StandardScaler().fit_transform(X)


# Actividad 11: configuración

def _candidatas():
    return [{"kernel": "rbf", "nu": nu, "gamma": gamma}
            for nu in VALORES_NU for gamma in VALORES_GAMMA]


def _etiqueta(configuracion):
    return f"nu={configuracion['nu']}, gamma={configuracion['gamma']}"


def configurar(datasets, repeticiones=2, proporcion_por_tipo=0.002):
    """Prueba cada configuración candidata con casos conocidos inyectados.

    `datasets`: lista de (nombre, DataFrame). Devuelve (resultados por corrida,
    ranking promedio, configuración elegida). Se elige por AUC-PR, luego F1 y luego tiempo.
    """
    filas = []
    for nombre, df in datasets:
        df = _numerico(df)
        for repeticion in range(repeticiones):
            semilla = 42 + repeticion
            datos = inyectar_anomalias(df, proporcion_por_tipo=proporcion_por_tipo, semilla=semilla)
            X = _escalar(matriz_variables(datos))
            etiquetas = datos["inyectada"].to_numpy(dtype=int)
            for configuracion in _candidatas():
                inicio = time.perf_counter()
                modelo = _nuevo_modelo(configuracion).fit(X)
                puntajes = -modelo.score_samples(X)
                predicciones = (modelo.predict(X) == -1).astype(int)
                segundos = time.perf_counter() - inicio
                fila = {
                    "dataset": nombre, "repeticion": repeticion + 1,
                    "configuracion": _etiqueta(configuracion),
                    "nu": configuracion["nu"], "gamma": str(configuracion["gamma"]),
                    "segundos": segundos, "marcadas": int(predicciones.sum()),
                }
                fila.update(_metricas_ranking(etiquetas, puntajes))
                fila.update(_metricas_binarias(etiquetas, predicciones))
                filas.append(fila)

    resultados = pd.DataFrame(filas)
    ranking = (resultados.groupby(["configuracion", "nu", "gamma"], sort=False)
               [["auc_pr", "auc_roc", "precision_en_k", "precision", "recall", "f1", "segundos"]]
               .mean().reset_index()
               .sort_values(["auc_pr", "f1", "segundos"], ascending=[False, False, True])
               .reset_index(drop=True))
    mejor = ranking.iloc[0]
    gamma = mejor["gamma"]
    elegida = {"kernel": "rbf", "nu": float(mejor["nu"]),
               "gamma": gamma if gamma == "scale" else float(gamma)}
    return resultados, ranking, elegida


def guardar_configuracion(configuracion, ranking, datasets, carpeta=None):
    carpeta = Path(carpeta or carpeta_modelos())
    carpeta.mkdir(parents=True, exist_ok=True)
    mejor = ranking.iloc[0]
    contenido = {
        "algoritmo": ALGORITMO,
        "configuracion": configuracion,
        "criterio": "Mayor AUC-PR frente a casos conocidos; desempate por F1 y tiempo.",
        "auc_pr": round(float(mejor["auc_pr"]), 4),
        "f1": round(float(mejor["f1"]), 4),
        "datasets": list(datasets),
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }
    ruta = carpeta / ARCHIVO_CONFIGURACION
    ruta.write_text(json.dumps(contenido, indent=2, ensure_ascii=False), encoding="utf-8")
    return ruta


def leer_configuracion(carpeta=None):
    """Configuración elegida en la actividad 11, o la base si aún no se eligió."""
    ruta = Path(carpeta or carpeta_modelos()) / ARCHIVO_CONFIGURACION
    if not ruta.exists():
        return dict(CONFIGURACION_BASE), False
    return json.loads(ruta.read_text(encoding="utf-8"))["configuracion"], True


# Actividad 12: entrenamiento y uso

def _particion(n, proporcion_prueba, semilla):
    """Índices de entrenamiento y de prueba al azar (reproducible con la semilla)."""
    orden = np.random.default_rng(semilla).permutation(n)
    en_prueba = int(round(n * proporcion_prueba))
    return np.sort(orden[en_prueba:]), np.sort(orden[:en_prueba])


def _observados(df):
    if "aviso_observado" not in df:
        return np.zeros(len(df), dtype=bool)
    return pd.to_numeric(df["aviso_observado"], errors="coerce").fillna(0).to_numpy() == 1


def entrenar(datasets, configuracion, porcentaje_atipicas=PORCENTAJE_ATIPICAS,
             proporcion_prueba=PROPORCION_PRUEBA, semilla=SEMILLA_PARTICION):
    """Entrena el modelo final y lo evalúa con datos que no vio.

    Cada carga se divide al azar en entrenamiento (por defecto 80 %) y prueba (20 %).
    Las variables se calculan sobre la carga completa, como cuando el sistema analiza un
    libro. El modelo aprende solo con la parte de entrenamiento, sin los registros que el
    auditor confirmó como observados, para aprender el comportamiento habitual.

    La parte de prueba se usa para medir: se le agregan casos atípicos conocidos (los
    mismos cinco tipos de la evaluación comparativa) y se calcula AUC-PR, AUC-ROC,
    precisión en los primeros k, y precisión/recall/F1 con el umbral del modelo.
    """
    from sklearn.preprocessing import StandardScaler

    bloques, excluidos, por_carga = [], 0, {}
    pruebas, etiquetas_prueba, tipos_prueba, por_carga_prueba = [], [], [], {}
    for nombre, df in datasets:
        df = _numerico(df).reset_index(drop=True)
        X = matriz_variables(df)
        observado = _observados(df)
        excluidos += int(observado.sum())
        entrenamiento, prueba = _particion(len(df), proporcion_prueba, semilla)
        entrenamiento = entrenamiento[~observado[entrenamiento]]
        bloques.append(X[entrenamiento])
        por_carga[nombre] = int(len(entrenamiento))
        if len(prueba):
            prueba = prueba[~observado[prueba]]
            # Casos conocidos para medir: variables recalculadas con esos casos incluidos.
            datos = inyectar_anomalias(df, semilla=semilla)
            X_datos = matriz_variables(datos)
            inyectadas = np.arange(len(df), len(datos))
            filas = np.concatenate([prueba, inyectadas])
            pruebas.append(X_datos[filas])
            etiquetas_prueba.append(datos["inyectada"].to_numpy(dtype=int)[filas])
            tipos_prueba.append(datos["clase_anomalia"].to_numpy()[filas])
            por_carga_prueba[nombre] = {"normales": int(len(prueba)), "casos_conocidos": int(len(inyectadas))}
    X = np.vstack(bloques)

    inicio = time.perf_counter()
    escalador = StandardScaler().fit(X)
    X_escalado = escalador.transform(X)
    modelo = _nuevo_modelo(configuracion).fit(X_escalado)
    segundos = time.perf_counter() - inicio

    puntajes = -modelo.score_samples(X_escalado)
    marcadas = int((modelo.predict(X_escalado) == -1).sum())
    umbral = float(np.quantile(puntajes, 1 - porcentaje_atipicas / 100))
    from sklearn import __version__ as version_sklearn

    metadatos = {
        "algoritmo": ALGORITMO,
        "configuracion": configuracion,
        "columnas": COLUMNAS_MODELO,
        "cargas": por_carga,
        "registros_entrenamiento": int(len(X)),
        "excluidos_observados": excluidos,
        "vectores_soporte": int(len(modelo.support_)),
        "marcadas_entrenamiento": marcadas,
        "segundos": round(segundos, 2),
        # Escala de 0 a 1 para el nivel de anomalía (se recorta el 0,1 % superior).
        "puntaje_minimo": float(puntajes.min()),
        "puntaje_p999": float(np.quantile(puntajes, 0.999)),
        # Umbral de atípica: el puntaje que supera solo el porcentaje elegido del entrenamiento.
        "porcentaje_atipicas": float(porcentaje_atipicas),
        "umbral_atipica": umbral,
        "proporcion_prueba": float(proporcion_prueba),
        "semilla_particion": int(semilla),
        "version_sklearn": version_sklearn,
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }
    if pruebas:
        X_prueba = escalador.transform(np.vstack(pruebas))
        etiquetas = np.concatenate(etiquetas_prueba)
        tipos = np.concatenate(tipos_prueba)
        puntajes_prueba = -modelo.score_samples(X_prueba)
        predicciones = (puntajes_prueba >= umbral).astype(int)
        metricas = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in {
            **_metricas_ranking(etiquetas, puntajes_prueba),
            **_metricas_binarias(etiquetas, predicciones)}.items()}
        recall_por_tipo = {
            tipo: round(float(predicciones[tipos == tipo].mean()), 4)
            for tipo in sorted(set(tipos) - {""})
        }
        metadatos["prueba"] = {
            "registros_normales": int((etiquetas == 0).sum()),
            "casos_conocidos": int(etiquetas.sum()),
            "por_carga": por_carga_prueba,
            "metricas": metricas,
            "recall_por_tipo": recall_por_tipo,
            "falsas_alarmas": round(float(predicciones[etiquetas == 0].mean()), 4),
        }
    return {"escalador": escalador, "modelo": modelo, "metadatos": metadatos}


def guardar(paquete, carpeta=None):
    import joblib

    carpeta = Path(carpeta or carpeta_modelos())
    carpeta.mkdir(parents=True, exist_ok=True)
    joblib.dump(paquete, carpeta / ARCHIVO_MODELO)
    (carpeta / ARCHIVO_METADATOS).write_text(
        json.dumps(paquete["metadatos"], indent=2, ensure_ascii=False), encoding="utf-8")
    return carpeta / ARCHIVO_MODELO


def cargar(carpeta=None):
    # joblib usa pickle: aceptar solo modelos generados localmente por entrenar_modelo.
    import joblib

    ruta = Path(carpeta or carpeta_modelos()) / ARCHIVO_MODELO
    if not ruta.exists():
        raise FileNotFoundError(
            f"No hay modelo entrenado en {ruta}. Ejecutar primero: python manage.py entrenar_modelo")
    return joblib.load(ruta)


class ModeloDesactualizado(Exception):
    """El modelo guardado se entrenó con otras variables: hay que reentrenarlo."""


def verificar_columnas(paquete):
    columnas = paquete["metadatos"].get("columnas")
    if columnas is not None and list(columnas) != COLUMNAS_MODELO:
        raise ModeloDesactualizado(
            "El modelo guardado se entrenó con otras variables. Ejecutar: "
            "python manage.py configurar_modelo y python manage.py entrenar_modelo")


def puntuar(df, paquete):
    """Aplica el modelo guardado a una carga, sin reentrenar.

    Devuelve un DataFrame con fila_origen, puntaje, nivel (0 a 1) y atipica (1/0).
    """
    verificar_columnas(paquete)
    X = paquete["escalador"].transform(matriz_variables(df))
    modelo, meta = paquete["modelo"], paquete["metadatos"]
    puntajes = -modelo.score_samples(X)
    rango = meta["puntaje_p999"] - meta["puntaje_minimo"]
    nivel = np.clip((puntajes - meta["puntaje_minimo"]) / rango, 0, 1) if rango > 0 else np.zeros(len(X))
    return pd.DataFrame({
        "fila_origen": df["fila_origen"].to_numpy(),
        "puntaje": puntajes,
        "nivel": nivel,
        "atipica": (puntajes >= meta["umbral_atipica"]).astype(int) if "umbral_atipica" in meta
                   else (modelo.predict(X) == -1).astype(int),
    })
