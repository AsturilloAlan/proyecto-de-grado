"""Prototipo de caracterización/transformación de variables (CRISP-DM: Preparación de
los datos) + modelado con los tres algoritmos candidatos (CRISP-DM: Modelado).
"""
import sys

import numpy as np
import pandas as pd


def construir_variables(df):
    """Caracterización y transformación de variables."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])

    # Monto con signo a partir de Debe y Haber.
    df["monto"] = df["debe"].fillna(0) - df["haber"].fillna(0)
    df["monto_abs"] = df["monto"].abs()

    # Variables temporales: día de la semana y día del mes, por si hay patrones.
    df["dia_semana"] = df["fecha"].dt.dayofweek  # 0=lunes … 6=domingo
    df["dia_mes"] = df["fecha"].dt.day

    # Tipo de cuenta en one-hot (5 categorías sin orden).
    df = pd.get_dummies(df, columns=["cuenta_tipo"], prefix="tipo")

    # Frecuencia de la cuenta en el dataset.
    df["cuenta_frecuencia"] = df.groupby("cuenta_codigo")["cuenta_codigo"].transform("count")

    # z-score del monto dentro de su cuenta.
    promedio_por_cuenta = df.groupby("cuenta_codigo")["monto_abs"].transform("mean")
    desvio_por_cuenta = df.groupby("cuenta_codigo")["monto_abs"].transform("std").fillna(0.0)
    # Cuenta con una sola transacción: z-score 0.
    denominador = desvio_por_cuenta.replace(0, np.nan)
    df["monto_zscore_cuenta"] = ((df["monto_abs"] - promedio_por_cuenta) / denominador).fillna(0.0)

    # Bandera de comprobante vacío.
    df["comprobante_vacio"] = df["comprobante"].fillna("").astype(str).str.strip().eq("").astype(int)

    return df


def comparar_modelos(df, columnas_features, contaminacion=0.05):
    """Entrena los tres algoritmos candidatos sobre el mismo conjunto de variables y
    devuelve el dataframe con una columna de predicción por modelo.
    """
    from sklearn.ensemble import IsolationForest
    from sklearn.neighbors import LocalOutlierFactor
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import OneClassSVM

    X = df[columnas_features].fillna(0).values
    # Estandarización: IF y OC-SVM dependen de la escala.
    X_escalado = StandardScaler().fit_transform(X)

    resultados = df.copy()

    isolation_forest = IsolationForest(contamination=contaminacion, random_state=42)
    resultados["anomalia_isolation_forest"] = (
        isolation_forest.fit_predict(X_escalado) == -1
    ).astype(int)

    # LOF con novelty=False: analiza el mismo conjunto con el que se ajusta.
    lof = LocalOutlierFactor(contamination=contaminacion)
    resultados["anomalia_lof"] = (lof.fit_predict(X_escalado) == -1).astype(int)

    one_class_svm = OneClassSVM(nu=contaminacion, kernel="rbf", gamma="scale")
    resultados["anomalia_ocsvm"] = (
        one_class_svm.fit_predict(X_escalado) == -1
    ).astype(int)

    return resultados


def resumen_comparativo(resultados):
    columnas_modelos = ["anomalia_isolation_forest", "anomalia_lof", "anomalia_ocsvm"]
    print("\n--- Resumen comparativo ---")
    total = len(resultados)
    for columna in columnas_modelos:
        marcadas = int(resultados[columna].sum())
        print(f"{columna}: {marcadas} de {total} transacciones marcadas como atípicas "
              f"({marcadas / total:.1%})")

    # Coincidencia de los tres modelos (no mide aciertos).
    coincidencia_total = (resultados[columnas_modelos].sum(axis=1) == 3).sum()
    print(f"Coincidencia entre los 3 modelos: {coincidencia_total} transacciones "
          "marcadas por los tres a la vez.")


def main():
    if len(sys.argv) < 2:
        print("Uso: python prototipo_modelos.py dataset_carga_<id>.csv [--solo-variables]")
        sys.exit(1)

    ruta_entrada = sys.argv[1]
    # --solo-variables: solo prepara variables, sin modelar.
    solo_variables = "--solo-variables" in sys.argv

    df = pd.read_csv(ruta_entrada)
    df_variables = construir_variables(df)

    if solo_variables:
        ruta_salida = ruta_entrada.replace(".csv", "_variables.csv")
        df_variables.to_csv(ruta_salida, index=False)
        print(f"Variables construidas. Revisa el archivo: {ruta_salida}")
        print("Columnas nuevas agregadas:", [
            c for c in df_variables.columns
            if c not in ["fila_origen", "fecha", "cuenta_codigo", "cuenta_nombre",
                         "comprobante", "glosa", "debe", "haber", "saldo"]
        ])
        return

    columnas_features = [
        "monto", "monto_abs", "dia_semana", "dia_mes",
        "cuenta_frecuencia", "monto_zscore_cuenta", "comprobante_vacio",
    ] + [c for c in df_variables.columns if c.startswith("tipo_")]

    resultados = comparar_modelos(df_variables, columnas_features)
    resumen_comparativo(resultados)

    ruta_salida = ruta_entrada.replace(".csv", "_con_predicciones.csv")
    resultados.to_csv(ruta_salida, index=False)
    print(f"\nDataset con las predicciones de los 3 modelos guardado en: {ruta_salida}")


if __name__ == "__main__":
    main()
