"""Por qué el modelo señala una transacción como atípica, en lenguaje de auditoría.

One-Class SVM con kernel RBF no entrega la importancia de cada variable. Se usa una
explicación por perturbación: se parte de una transacción "habitual" (todas las
características en su valor promedio del entrenamiento) y se le pone una característica
a la vez con el valor real de la transacción. Lo que sube el puntaje de anomalía mide
cuánto hace atípica a la transacción esa característica por sí sola. Así también las
transacciones extremas reciben motivo (al quitar una sola característica seguirían
siendo extremas, por eso no se mide quitando).

Solo se muestran motivos con sentido para el auditor y en la dirección sospechosa
(importe alto, no bajo; cuenta con pocos movimientos, no con muchos). El cierre de mes
nunca se presenta como motivo: es normal en contabilidad.
"""
import math

import numpy as np

from .modelo import _numerico
from .variables import COLUMNAS_MODELO, FERIADOS_FIJOS, construir_variables

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MAXIMO_MOTIVOS = 3

PERCENTIL_IMPORTE = 0.90       # importe dentro del 10 % más alto del libro
VECES_HABITUAL = 2.0           # al menos el doble del importe habitual de su cuenta
MAXIMO_MOVIMIENTOS = 5         # cuenta con muy pocos movimientos en la gestión
PROPORCION_TIPO_RARO = 0.05    # tipo de cuenta con menos del 5 % de los movimientos

ETIQUETAS = {
    "monto": "Importe alto",
    "monto_cuenta": "Inusual para su cuenta",
    "cuenta_frecuencia": "Cuenta poco usada",
    "dia_no_habil": "Día no hábil",
    "comprobante_vacio": "Sin comprobante",
    "tipo_cuenta": "Tipo de cuenta poco común",
    "combinacion": "Combinación poco común",
}

TIPOS = [c for c in COLUMNAS_MODELO if c.startswith("tipo_")]
GRUPOS = {
    "monto": ["monto", "monto_abs"],
    "monto_cuenta": ["monto_zscore_cuenta"],
    "cuenta_frecuencia": ["cuenta_frecuencia"],
    "dia_no_habil": ["dia_no_habil"],
    "comprobante_vacio": ["comprobante_vacio"],
    "tipo_cuenta": TIPOS,
    "cierre_mes": ["cierre_mes"],  # se mide, pero nunca se muestra como motivo
}
INDICES = {g: [COLUMNAS_MODELO.index(c) for c in cols] for g, cols in GRUPOS.items()}


def _formato(monto):
    """Formato boliviano: 1.234.567,80."""
    return f"{monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def aportes(X_escalado, modelo):
    """Dict grupo → vector con cuánto sube el puntaje al dar a una transacción habitual
    solo esa característica de la transacción real."""
    base = -modelo.score_samples(np.zeros((1, X_escalado.shape[1])))[0]
    resultado = {}
    for grupo, indices in INDICES.items():
        solo = np.zeros_like(X_escalado, dtype=float)
        solo[:, indices] = X_escalado[:, indices]
        resultado[grupo] = -modelo.score_samples(solo) - base
    return resultado


def _candidatos(i, datos, variables, contexto):
    """Motivos con sentido de auditoría para la fila i: {grupo: texto}."""
    fila, dato = variables.iloc[i], datos.iloc[i]
    debe, haber = float(dato["debe"]), float(dato["haber"])
    importe, lado = (debe, "Debe") if debe else (haber, "Haber")
    cuenta = dato["cuenta_codigo"]
    motivos = {}

    percentil = contexto["percentil"][i]
    if percentil >= PERCENTIL_IMPORTE:
        top = max(1, math.ceil((1 - percentil) * 100))
        motivos["monto"] = (f"Importe de {_formato(importe)} en el {lado}: está entre el "
                            f"{top} % más alto de todo el libro")

    habitual = contexto["mediana_cuenta"][i]
    movimientos = int(fila["cuenta_frecuencia"])
    if movimientos > 2 and habitual > 0 and abs(importe) / habitual >= VECES_HABITUAL:
        veces = abs(importe) / habitual
        cuantas = "más de 100 veces" if veces > 100 else f"{veces:,.0f} veces"
        motivos["monto_cuenta"] = (f"{cuantas} lo que suele moverse en la cuenta {cuenta} "
                                   f"(movimiento típico: {_formato(habitual)})")

    if movimientos <= MAXIMO_MOVIMIENTOS:
        plural = "s" if movimientos != 1 else ""
        motivos["cuenta_frecuencia"] = (f"La cuenta {cuenta} tiene solo {movimientos} "
                                        f"movimiento{plural} en toda la gestión")

    if int(fila["dia_no_habil"]):
        fecha = contexto["fechas"].iloc[i]
        if (fecha.month, fecha.day) in FERIADOS_FIJOS:
            cuando = f"feriado del {fecha:%d/%m}"
        else:
            cuando = DIAS[fecha.dayofweek]
        motivos["dia_no_habil"] = f"Registrada en día no hábil ({cuando}) y no es cierre de mes"

    if int(fila["comprobante_vacio"]):
        motivos["comprobante_vacio"] = "Sin número de comprobante"

    tipo = next((t for t in TIPOS if t in fila.index and int(fila[t])), None)
    if tipo and contexto["proporcion_tipo"].get(tipo, 1) < PROPORCION_TIPO_RARO:
        porcentaje = contexto["proporcion_tipo"][tipo] * 100
        motivos["tipo_cuenta"] = (f"Cuenta de {tipo[5:]}: ese tipo de cuenta reúne solo el "
                                  f"{porcentaje:.1f} % de los movimientos del libro")
    return motivos


def explicar(df, paquete, mascara):
    """Motivos para cada fila de `df` donde `mascara` es verdadera.

    Devuelve {posición: [{"variable", "aporte", "texto"}, ...]}; el primero es el motivo
    principal. `aporte` es el peso relativo (%) entre los motivos mostrados.
    """
    posiciones = np.flatnonzero(np.asarray(mascara))
    if len(posiciones) == 0:
        return {}
    datos = _numerico(df).reset_index(drop=True)
    variables = construir_variables(datos).reindex(columns=COLUMNAS_MODELO, fill_value=0)
    variables = variables.astype(float).fillna(0.0)
    X = paquete["escalador"].transform(variables.to_numpy())
    pesos = aportes(X[posiciones], paquete["modelo"])

    monto_abs = (datos["debe"] - datos["haber"]).abs()
    contexto = {
        "percentil": monto_abs.rank(pct=True).to_numpy(),
        "mediana_cuenta": monto_abs.groupby(datos["cuenta_codigo"]).transform("median").to_numpy(),
        "fechas": construir_variables(datos)["fecha"],
        "proporcion_tipo": {t: float(variables[t].mean()) for t in TIPOS},
    }

    motivos = {}
    for k, posicion in enumerate(posiciones):
        candidatos = _candidatos(posicion, datos, variables, contexto)
        ordenados = sorted(candidatos, key=lambda g: -pesos[g][k])[:MAXIMO_MOTIVOS]
        positivos = {g: max(pesos[g][k], 0.0) for g in ordenados}
        total = sum(positivos.values())
        lista = [
            {"variable": g,
             "aporte": round(positivos[g] * 100 / total) if total > 0 else round(100 / len(ordenados)),
             "texto": candidatos[g]}
            for g in ordenados
        ]
        if not lista:
            lista = [{"variable": "combinacion", "aporte": 100,
                      "texto": "Combinación poco común de importe, cuenta y fecha; ninguna "
                               "característica es extrema por sí sola"}]
        motivos[int(posicion)] = lista
    return motivos
