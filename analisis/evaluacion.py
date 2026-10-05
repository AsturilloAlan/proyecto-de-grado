"""Evaluación comparativa de Isolation Forest, LOF y One-Class SVM (CRISP-DM: Evaluación).

Criterios: tiempo de cómputo, distribución de puntajes y precisión frente a casos
conocidos (anomalías inyectadas, avisos de las reglas y observaciones del auditor).
"""
import time

import numpy as np
import pandas as pd

from .variables import construir_variables

MODELOS = ["isolation_forest", "lof", "one_class_svm"]
NOMBRES_MODELOS = {
    "isolation_forest": "Isolation Forest",
    "lof": "Local Outlier Factor",
    "one_class_svm": "One-Class SVM",
}

# Tipos de anomalía que se inyectan y qué representan en un registro contable.
TIPOS_INYECCION = {
    "monto_extremo": "Monto muy superior al habitual de la cuenta",
    "naturaleza_invertida": "Ingreso en el Debe o gasto en el Haber por un monto alto",
    "duplicado": "Copia exacta de un registro existente",
    "fecha_inusual": "Registro en domingo por un monto alto",
    "sin_comprobante": "Monto alto sin número de comprobante",
}

COLUMNAS_BASE = [
    "monto", "monto_abs", "dia_semana", "dia_mes",
    "cuenta_frecuencia", "monto_zscore_cuenta", "comprobante_vacio",
]


# --- Inyección de casos conocidos -------------------------------------------------

def _monto_alto(df, indice, rng, cuantil=0.95, factor=(1.5, 3.0)):
    """Monto por encima del cuantil de la cuenta del registro, para que sea atípico."""
    cuenta = df.at[indice, "cuenta_codigo"]
    montos = (df.loc[df["cuenta_codigo"] == cuenta, "debe"] +
              df.loc[df["cuenta_codigo"] == cuenta, "haber"])
    base = float(montos.quantile(cuantil)) if len(montos) else 0.0
    base = max(base, float(df.at[indice, "debe"] + df.at[indice, "haber"]), 1.0)
    return round(base * rng.uniform(*factor), 2)


def _domingo_cercano(fecha):
    fecha = pd.Timestamp(fecha)
    return (fecha + pd.Timedelta(days=(6 - fecha.dayofweek))).date().isoformat()


def inyectar_anomalias(df, proporcion_por_tipo=0.002, semilla=0):
    """Agrega filas atípicas (copias alteradas de registros reales al azar) y marca
    cada fila con `inyectada` (1/0) y `clase_anomalia`. Los originales no cambian.
    """
    rng = np.random.default_rng(semilla)
    base = df.reset_index(drop=True).copy()
    base["inyectada"] = 0
    base["clase_anomalia"] = ""
    cantidad = max(5, int(round(len(base) * proporcion_por_tipo)))
    nuevas = []
    siguiente_fila = int(base["fila_origen"].max()) + 1

    for tipo in TIPOS_INYECCION:
        if tipo == "naturaleza_invertida":
            candidatos = base.index[base["cuenta_tipo"].isin(["ingreso", "gasto"])]
        else:
            candidatos = base.index
        if len(candidatos) == 0:
            continue
        for indice in rng.choice(candidatos, size=cantidad, replace=True):
            fila = base.loc[indice].copy()
            if tipo == "monto_extremo":
                monto = _monto_alto(base, indice, rng, cuantil=0.99, factor=(3.0, 10.0))
                if fila["debe"] > 0:
                    fila["debe"] = monto
                else:
                    fila["haber"] = monto
            elif tipo == "naturaleza_invertida":
                monto = _monto_alto(base, indice, rng)
                if fila["cuenta_tipo"] == "ingreso":
                    fila["debe"], fila["haber"] = monto, 0.0
                else:
                    fila["debe"], fila["haber"] = 0.0, monto
            elif tipo == "duplicado":
                pass  # copia exacta
            elif tipo == "fecha_inusual":
                fila["fecha"] = _domingo_cercano(fila["fecha"])
                monto = _monto_alto(base, indice, rng)
                if fila["debe"] > 0:
                    fila["debe"] = monto
                else:
                    fila["haber"] = monto
            elif tipo == "sin_comprobante":
                fila["comprobante"] = ""
                monto = _monto_alto(base, indice, rng)
                if fila["debe"] > 0:
                    fila["debe"] = monto
                else:
                    fila["haber"] = monto
            fila["fila_origen"] = siguiente_fila
            siguiente_fila += 1
            fila["inyectada"] = 1
            fila["clase_anomalia"] = tipo
            for columna in ("aviso_duplicado", "aviso_naturaleza", "aviso_observado"):
                if columna in fila.index:
                    fila[columna] = 0
            nuevas.append(fila)

    return pd.concat([base, pd.DataFrame(nuevas)], ignore_index=True)


# --- Modelos ------------------------------------------------------------------------

def _matriz_variables(df_variables):
    columnas = COLUMNAS_BASE + [c for c in df_variables.columns if c.startswith("tipo_")]
    X = df_variables[columnas].astype(float).fillna(0.0).values
    from sklearn.preprocessing import StandardScaler
    return StandardScaler().fit_transform(X), columnas


def puntuar(nombre, X, contaminacion, semilla):
    """Ajusta el modelo y devuelve (puntajes, predicciones, segundos).

    Los puntajes quedan orientados para que un valor mayor sea más atípico.
    """
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor
    from sklearn.svm import OneClassSVM

    inicio = time.perf_counter()
    if nombre == "isolation_forest":
        modelo = IsolationForest(contamination=contaminacion, random_state=semilla)
        predicciones = modelo.fit_predict(X)
        puntajes = -modelo.score_samples(X)
    elif nombre == "lof":
        # LOF falla con puntos repetidos (distancia 0 entre vecinos): se ajusta sobre
        # los vectores únicos y cada registro hereda el puntaje de su vector.
        unicos, indice = np.unique(np.asarray(X, dtype=float), axis=0, return_inverse=True)
        modelo = LocalOutlierFactor(n_neighbors=min(20, len(unicos) - 1))
        modelo.fit(unicos)
        puntajes = -modelo.negative_outlier_factor_[np.ravel(indice)]
        umbral = np.quantile(puntajes, 1 - contaminacion)
        predicciones = np.where(puntajes > umbral, -1, 1)
    elif nombre == "one_class_svm":
        modelo = OneClassSVM(nu=contaminacion, kernel="rbf", gamma="scale")
        predicciones = modelo.fit_predict(X)
        puntajes = -modelo.score_samples(X)
    else:
        raise ValueError(f"Modelo desconocido: {nombre}")
    segundos = time.perf_counter() - inicio
    return np.asarray(puntajes, dtype=float), (predicciones == -1).astype(int), segundos


# --- Métricas -----------------------------------------------------------------------

def _metricas_binarias(etiquetas, predicciones):
    verdaderos = int(((etiquetas == 1) & (predicciones == 1)).sum())
    falsos_pos = int(((etiquetas == 0) & (predicciones == 1)).sum())
    falsos_neg = int(((etiquetas == 1) & (predicciones == 0)).sum())
    precision = verdaderos / (verdaderos + falsos_pos) if verdaderos + falsos_pos else 0.0
    recall = verdaderos / (verdaderos + falsos_neg) if verdaderos + falsos_neg else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision, "recall": recall, "f1": f1,
        "verdaderos_positivos": verdaderos, "falsos_positivos": falsos_pos,
        "falsos_negativos": falsos_neg,
    }


def _metricas_ranking(etiquetas, puntajes):
    """AUC-ROC, AUC-PR y precisión en los k primeros (k = casos conocidos)."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    positivos = int(etiquetas.sum())
    if positivos == 0 or positivos == len(etiquetas):
        return {"auc_roc": np.nan, "auc_pr": np.nan, "precision_en_k": np.nan}
    orden = np.argsort(-puntajes)[:positivos]
    return {
        "auc_roc": float(roc_auc_score(etiquetas, puntajes)),
        "auc_pr": float(average_precision_score(etiquetas, puntajes)),
        "precision_en_k": float(etiquetas[orden].mean()),
    }


def _normalizar(puntajes):
    # Se recorta el 0,1 % superior para que un valor extremo no aplaste la escala.
    minimo, maximo = puntajes.min(), np.quantile(puntajes, 0.999)
    puntajes = np.minimum(puntajes, maximo)
    if maximo == minimo:
        return np.zeros_like(puntajes)
    return (puntajes - minimo) / (maximo - minimo)


# --- Evaluación completa ------------------------------------------------------------

def evaluar(df, nombre_dataset, repeticiones=3, contaminacion=0.05, proporcion_por_tipo=0.002):
    """Devuelve (resumen por modelo y repetición, recall por tipo de anomalía,
    puntajes normalizados de la última repetición).
    """
    filas_resumen, filas_tipo = [], []
    puntajes_ultima = None

    for repeticion in range(repeticiones):
        semilla = 42 + repeticion
        datos = inyectar_anomalias(df, proporcion_por_tipo=proporcion_por_tipo, semilla=semilla)
        variables = construir_variables(datos)
        X, _ = _matriz_variables(variables)

        inyectada = datos["inyectada"].to_numpy(dtype=int)
        originales = inyectada == 0
        aviso = np.zeros(len(datos), dtype=int)
        for columna in ("aviso_duplicado", "aviso_naturaleza"):
            if columna in datos:
                aviso |= datos[columna].fillna(0).to_numpy(dtype=int)
        observado = datos["aviso_observado"].fillna(0).to_numpy(dtype=int) \
            if "aviso_observado" in datos else np.zeros(len(datos), dtype=int)

        tabla_puntajes = pd.DataFrame({
            "inyectada": inyectada,
            "clase_anomalia": datos["clase_anomalia"].to_numpy(),
            "aviso": aviso,
        })

        for modelo in MODELOS:
            puntajes, predicciones, segundos = puntuar(modelo, X, contaminacion, semilla)
            fila = {
                "dataset": nombre_dataset, "repeticion": repeticion + 1,
                "modelo": NOMBRES_MODELOS[modelo], "registros": len(datos),
                "inyectadas": int(inyectada.sum()), "segundos": segundos,
                "marcadas": int(predicciones.sum()),
            }
            for clave, valor in _metricas_binarias(inyectada, predicciones).items():
                fila[f"inyectadas_{clave}"] = valor
            for clave, valor in _metricas_ranking(inyectada, puntajes).items():
                fila[f"inyectadas_{clave}"] = valor

            # Avisos por reglas: solo sobre los registros originales.
            if aviso[originales].sum():
                fila["avisos_reglas"] = int(aviso[originales].sum())
                fila["avisos_recall"] = float(predicciones[originales][aviso[originales] == 1].mean())
                fila["avisos_auc_roc"] = _metricas_ranking(
                    aviso[originales], puntajes[originales])["auc_roc"]
            if observado[originales].sum():
                fila["observados_auditor"] = int(observado[originales].sum())
                fila["observados_recall"] = float(
                    predicciones[originales][observado[originales] == 1].mean())
            filas_resumen.append(fila)

            for tipo in TIPOS_INYECCION:
                mascara = datos["clase_anomalia"].to_numpy() == tipo
                if mascara.any():
                    filas_tipo.append({
                        "dataset": nombre_dataset, "repeticion": repeticion + 1,
                        "modelo": NOMBRES_MODELOS[modelo], "tipo": tipo,
                        "recall": float(predicciones[mascara].mean()),
                    })
            tabla_puntajes[NOMBRES_MODELOS[modelo]] = _normalizar(puntajes)

        puntajes_ultima = tabla_puntajes

    return pd.DataFrame(filas_resumen), pd.DataFrame(filas_tipo), puntajes_ultima


def promediar(resumen):
    """Media por modelo (y dataset) de las repeticiones."""
    numericas = resumen.select_dtypes("number").columns.drop("repeticion", errors="ignore")
    return resumen.groupby(["dataset", "modelo"], sort=False)[list(numericas)].mean().reset_index()


def distribucion_puntajes(puntajes):
    """Percentiles del puntaje normalizado, separando registros normales e inyectados."""
    filas = []
    for modelo in NOMBRES_MODELOS.values():
        if modelo not in puntajes:
            continue
        for grupo, mascara in (("normales", puntajes["inyectada"] == 0),
                               ("inyectadas", puntajes["inyectada"] == 1)):
            valores = puntajes.loc[mascara, modelo]
            filas.append({
                "modelo": modelo, "grupo": grupo,
                "media": valores.mean(), "p50": valores.quantile(0.5),
                "p90": valores.quantile(0.9), "p99": valores.quantile(0.99),
            })
    return pd.DataFrame(filas)


def elegir_modelo(promedio):
    """Ordena los modelos por AUC-PR frente a inyectadas, luego F1 y luego tiempo."""
    general = promedio.groupby("modelo", sort=False)[
        ["inyectadas_auc_pr", "inyectadas_f1", "segundos"]].mean()
    general = general.sort_values(
        ["inyectadas_auc_pr", "inyectadas_f1", "segundos"], ascending=[False, False, True])
    return general
