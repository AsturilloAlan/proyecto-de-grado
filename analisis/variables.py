"""Variables de entrada de los modelos (CRISP-DM: Preparación de los datos)."""
import numpy as np
import pandas as pd


def construir_variables(df):
    """Agrega al dataset de una carga las variables que usan los modelos."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])

    df["monto"] = df["debe"].fillna(0) - df["haber"].fillna(0)
    df["monto_abs"] = df["monto"].abs()
    df["dia_semana"] = df["fecha"].dt.dayofweek  # 0 = lunes, 6 = domingo
    df["dia_mes"] = df["fecha"].dt.day

    df = pd.get_dummies(df, columns=["cuenta_tipo"], prefix="tipo")
    df["cuenta_frecuencia"] = df.groupby("cuenta_codigo")["cuenta_codigo"].transform("count")

    # Monto respecto de lo habitual en su cuenta (z-score); 0 si la cuenta no varía.
    promedio = df.groupby("cuenta_codigo")["monto_abs"].transform("mean")
    desvio = df.groupby("cuenta_codigo")["monto_abs"].transform("std").replace(0, np.nan)
    df["monto_zscore_cuenta"] = ((df["monto_abs"] - promedio) / desvio).fillna(0.0)

    df["comprobante_vacio"] = df["comprobante"].fillna("").astype(str).str.strip().eq("").astype(int)
    return df
