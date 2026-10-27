"""Variables de entrada de los modelos (CRISP-DM: Preparación de los datos).

Revisión tras la validación con la auditora (actividad 13): el día de la semana y el día
del mes como números hacían ver "lejanos" los asientos de cierre (el 30 o 31, aunque
caigan en domingo), que en contabilidad son normales. Se reemplazan por dos indicadores
con sentido contable: si la transacción es de cierre de mes (o apertura de la gestión) y
si se registró en un día no hábil fuera de esos casos.
"""
import numpy as np
import pandas as pd

COLUMNAS_BASE = [
    "monto", "monto_abs", "cierre_mes", "dia_no_habil",
    "cuenta_frecuencia", "monto_zscore_cuenta", "comprobante_vacio",
]
TIPOS_CUENTA = ["activo", "pasivo", "patrimonio", "ingreso", "gasto"]
# Columnas fijas del modelo final: una carga sin cuentas de algún tipo recibe esa columna en 0.
COLUMNAS_MODELO = COLUMNAS_BASE + [f"tipo_{tipo}" for tipo in TIPOS_CUENTA]

# Feriados nacionales de fecha fija en Bolivia (mes, día). Los móviles (Carnaval, Viernes
# Santo, Corpus Christi) no se incluyen.
FERIADOS_FIJOS = {(1, 1), (1, 22), (5, 1), (6, 21), (8, 6), (11, 2), (12, 25)}


def es_cierre_o_apertura(fechas):
    """Último día del mes (asientos de cierre) o 1 de enero (apertura de la gestión)."""
    return fechas.dt.is_month_end | ((fechas.dt.month == 1) & (fechas.dt.day == 1))


def es_dia_no_habil(fechas):
    """Sábado, domingo o feriado fijo, salvo que sea cierre de mes o apertura."""
    fin_de_semana = fechas.dt.dayofweek >= 5
    feriado = pd.Series(
        [(f.month, f.day) in FERIADOS_FIJOS for f in fechas], index=fechas.index, dtype=bool)
    return (fin_de_semana | feriado) & ~es_cierre_o_apertura(fechas)


def construir_variables(df):
    """Agrega al dataset de una carga las variables que usan los modelos."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])

    df["monto"] = df["debe"].fillna(0) - df["haber"].fillna(0)
    df["monto_abs"] = df["monto"].abs()
    df["cierre_mes"] = es_cierre_o_apertura(df["fecha"]).astype(int)
    df["dia_no_habil"] = es_dia_no_habil(df["fecha"]).astype(int)

    df = pd.get_dummies(df, columns=["cuenta_tipo"], prefix="tipo")
    df["cuenta_frecuencia"] = df.groupby("cuenta_codigo")["cuenta_codigo"].transform("count")

    # Monto respecto de lo habitual en su cuenta (z-score); 0 si la cuenta no varía.
    promedio = df.groupby("cuenta_codigo")["monto_abs"].transform("mean")
    desvio = df.groupby("cuenta_codigo")["monto_abs"].transform("std").replace(0, np.nan)
    df["monto_zscore_cuenta"] = ((df["monto_abs"] - promedio) / desvio).fillna(0.0)

    df["comprobante_vacio"] = df["comprobante"].fillna("").astype(str).str.strip().eq("").astype(int)
    return df
