"""Excel con los datos de cada gráfico del informe, listo para Power BI.

Se usa Excel y no CSV para que Power BI no confunda los decimales con la
configuración regional de Windows.
"""
import numpy as np
import pandas as pd

from .evaluacion import TIPOS_INYECCION

NOMBRES_TIPO = {
    "monto_extremo": "Monto extremo",
    "naturaleza_invertida": "Naturaleza invertida",
    "duplicado": "Duplicado",
    "fecha_inusual": "Fecha inusual (domingo)",
    "sin_comprobante": "Sin comprobante",
}
METRICAS = {
    "inyectadas_auc_pr": "AUC-PR", "inyectadas_f1": "F1",
    "inyectadas_precision": "Precisión", "inyectadas_recall": "Recall",
    "inyectadas_auc_roc": "AUC-ROC", "inyectadas_precision_en_k": "Precisión@k",
}
ANCHO_INTERVALO = 0.05


def _gestion(nombre):
    """'carga_44_2023' -> '2023'; si no tiene ese formato, se deja igual."""
    ultimo = str(nombre).rsplit("_", 1)[-1]
    return ultimo if ultimo.isdigit() else str(nombre)


def _metricas(promedio):
    tabla = promedio.melt(id_vars=["dataset", "modelo"],
                          value_vars=[c for c in METRICAS if c in promedio],
                          var_name="metrica", value_name="Valor")
    tabla["Orden"] = tabla["metrica"].map({c: i for i, c in enumerate(METRICAS, 1)})
    tabla["Métrica"] = tabla["metrica"].map(METRICAS)
    tabla["Gestión"] = tabla["dataset"].map(_gestion)
    return tabla[["Gestión", "modelo", "Métrica", "Orden", "Valor"]].rename(
        columns={"modelo": "Modelo"}).round({"Valor": 4})


def _tiempo(promedio):
    tabla = promedio[["dataset", "modelo", "registros", "segundos"]].copy()
    tabla["dataset"] = tabla["dataset"].map(_gestion)
    tabla.columns = ["Gestión", "Modelo", "Registros", "Segundos"]
    return tabla.astype({"Registros": int}).round({"Segundos": 2})


def _recall_por_tipo(por_tipo):
    tabla = por_tipo.groupby(["dataset", "modelo", "tipo"], sort=False)["recall"].mean().reset_index()
    tabla["Orden"] = tabla["tipo"].map({t: i for i, t in enumerate(TIPOS_INYECCION, 1)})
    tabla["tipo"] = tabla["tipo"].map(NOMBRES_TIPO).fillna(tabla["tipo"])
    tabla["dataset"] = tabla["dataset"].map(_gestion)
    tabla.columns = ["Gestión", "Modelo", "Tipo de anomalía", "Recall", "Orden"]
    return tabla.round({"Recall": 4})


def _histograma(puntajes_por_dataset):
    """Proporción de registros por intervalo de puntaje, separada en normales e inyectados.

    Equivale al histograma de densidad del informe: Power BI no calcula histogramas solo.
    """
    bordes = np.round(np.arange(0, 1 + ANCHO_INTERVALO, ANCHO_INTERVALO), 2)
    etiquetas = [f"{a:.2f}-{b:.2f}" for a, b in zip(bordes[:-1], bordes[1:])]
    filas = []
    for nombre, puntajes in puntajes_por_dataset.items():
        for modelo in [c for c in puntajes.columns if c not in ("inyectada", "clase_anomalia", "aviso")]:
            for grupo, mascara in (("Registros normales", puntajes["inyectada"] == 0),
                                   ("Anomalías inyectadas", puntajes["inyectada"] == 1)):
                valores = puntajes.loc[mascara, modelo].clip(0, 1)
                if valores.empty:
                    continue
                conteo, _ = np.histogram(valores, bins=bordes)
                for orden, (etiqueta, cantidad) in enumerate(zip(etiquetas, conteo), 1):
                    filas.append({
                        "Gestión": _gestion(nombre), "Modelo": modelo, "Grupo": grupo,
                        "Intervalo": etiqueta, "Orden": orden, "Registros": int(cantidad),
                        "Proporción": round(cantidad / len(valores), 4),
                    })
    return pd.DataFrame(filas)


def _puntajes(puntajes_por_dataset):
    tablas = []
    for nombre, puntajes in puntajes_por_dataset.items():
        tabla = puntajes.copy()
        tabla.insert(0, "Gestión", _gestion(nombre))
        tabla["Grupo"] = np.where(tabla["inyectada"] == 1, "Anomalías inyectadas", "Registros normales")
        tabla["clase_anomalia"] = tabla["clase_anomalia"].map(NOMBRES_TIPO).fillna("")
        tablas.append(tabla.drop(columns=["inyectada"]).rename(
            columns={"clase_anomalia": "Tipo de anomalía", "aviso": "Aviso de reglas"}))
    return pd.concat(tablas, ignore_index=True).round(4)


def _avisos(promedio):
    if "avisos_reglas" not in promedio:
        return None
    tabla = promedio[["dataset", "modelo", "avisos_reglas", "avisos_recall", "avisos_auc_roc"]].copy()
    tabla["dataset"] = tabla["dataset"].map(_gestion)
    tabla.columns = ["Gestión", "Modelo", "Avisos de reglas", "Recall sobre avisos", "AUC-ROC sobre avisos"]
    return tabla.round(4)


def exportar_evaluacion(ruta, promedio, por_tipo, puntajes_por_dataset):
    """Una hoja por gráfico del informe, más los puntajes de cada registro."""
    hojas = {
        "Metricas": _metricas(promedio),
        "Tiempo": _tiempo(promedio),
        "Recall_por_tipo": _recall_por_tipo(por_tipo),
        "Histograma_puntajes": _histograma(puntajes_por_dataset),
        "Avisos_reglas": _avisos(promedio),
        "Puntajes_por_registro": _puntajes(puntajes_por_dataset),
    }
    with pd.ExcelWriter(ruta) as escritor:
        for nombre, tabla in hojas.items():
            if tabla is not None and not tabla.empty:
                tabla.to_excel(escritor, sheet_name=nombre, index=False)
    return ruta


def exportar_configuracion(ruta, ranking, configuracion_base):
    """Ranking de configuraciones de One-Class SVM (actividad 11 y 13)."""
    tabla = ranking.copy()
    tabla["Es la base"] = np.where(
        (tabla["nu"] == configuracion_base["nu"]) & (tabla["gamma"].astype(str) == str(configuracion_base["gamma"])),
        "Sí", "No")
    tabla["Posición"] = range(1, len(tabla) + 1)
    tabla = tabla.rename(columns={
        "configuracion": "Configuración", "auc_pr": "AUC-PR", "auc_roc": "AUC-ROC",
        "precision_en_k": "Precisión@k", "precision": "Precisión", "recall": "Recall",
        "f1": "F1", "segundos": "Segundos"})
    tabla["gamma"] = tabla["gamma"].astype(str)
    with pd.ExcelWriter(ruta) as escritor:
        tabla.round(4).to_excel(escritor, sheet_name="Configuraciones", index=False)
    return ruta
