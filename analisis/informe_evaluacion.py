"""Genera las tablas, los gráficos y el informe de la evaluación comparativa."""
import numbers
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

from .evaluacion import TIPOS_INYECCION, distribucion_puntajes, elegir_modelo, promediar


def _tabla_md(df, columnas, formatos):
    encabezado = "| " + " | ".join(columnas.values()) + " |"
    separador = "|" + "---|" * len(columnas)
    filas = []
    for _, fila in df.iterrows():
        celdas = []
        for columna in columnas:
            valor = fila.get(columna)
            formato = formatos.get(columna)
            if valor is None or valor == "" or pd.isna(valor):
                valor = "-"
            elif formato and isinstance(valor, numbers.Number):
                valor = formato.format(valor)
            celdas.append(str(valor))
        filas.append("| " + " | ".join(celdas) + " |")
    return "\n".join([encabezado, separador, *filas])


def _graficos(carpeta, promedio, por_tipo, puntajes_por_dataset):
    """Gráficos PNG. Si matplotlib no está instalado, se omiten sin error."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        warnings.warn("matplotlib no está instalado: el informe se genera sin gráficos.")
        return None

    archivos = []
    general = promedio.groupby("modelo", sort=False).mean(numeric_only=True)

    fig, ejes = plt.subplots(1, 2, figsize=(10, 4))
    general[["inyectadas_auc_pr", "inyectadas_f1", "inyectadas_precision", "inyectadas_recall"]].rename(
        columns={"inyectadas_auc_pr": "AUC-PR", "inyectadas_f1": "F1",
                 "inyectadas_precision": "Precisión", "inyectadas_recall": "Recall"}
    ).plot.bar(ax=ejes[0], rot=0, color=["#6E1B3F", "#A0526F", "#C98FA6", "#E5C7D3"])
    ejes[0].set_title("Métricas frente a anomalías inyectadas")
    ejes[0].set_ylim(0, 1)
    ejes[0].set_xlabel("")
    general["segundos"].plot.bar(ax=ejes[1], rot=0, color="#4A1129")
    ejes[1].set_title("Tiempo de cómputo promedio (s)")
    ejes[1].set_xlabel("")
    fig.tight_layout()
    ruta = carpeta / "metricas_y_tiempo.png"
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    archivos.append(ruta.name)

    tipos = por_tipo.groupby(["tipo", "modelo"], sort=False)["recall"].mean().unstack()
    fig, eje = plt.subplots(figsize=(10, 4))
    tipos.plot.bar(ax=eje, rot=15, color=["#6E1B3F", "#A0526F", "#C98FA6"])
    eje.set_title("Recall por tipo de anomalía inyectada")
    eje.set_ylim(0, 1)
    eje.set_xlabel("")
    fig.tight_layout()
    ruta = carpeta / "recall_por_tipo.png"
    fig.savefig(ruta, dpi=150)
    plt.close(fig)
    archivos.append(ruta.name)

    for nombre, puntajes in puntajes_por_dataset.items():
        modelos = [m for m in ("Isolation Forest", "Local Outlier Factor", "One-Class SVM") if m in puntajes]
        fig, ejes = plt.subplots(1, len(modelos), figsize=(4 * len(modelos), 3.5), sharey=False)
        for eje, modelo in zip(ejes, modelos):
            eje.hist(puntajes.loc[puntajes["inyectada"] == 0, modelo], bins=50, alpha=0.7,
                     density=True, label="Normales", color="#BBBBBB")
            eje.hist(puntajes.loc[puntajes["inyectada"] == 1, modelo], bins=30, alpha=0.7,
                     density=True, label="Inyectadas", color="#6E1B3F")
            eje.set_title(modelo)
            eje.set_xlabel("Puntaje de anomalía (0-1)")
        ejes[0].legend()
        fig.suptitle(f"Distribución de puntajes · {nombre}")
        fig.tight_layout()
        ruta = carpeta / f"puntajes_{nombre}.png"
        fig.savefig(ruta, dpi=150)
        plt.close(fig)
        archivos.append(ruta.name)
    return archivos


def generar_informe(carpeta, resumen, por_tipo, puntajes_por_dataset, parametros):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)

    promedio = promediar(resumen)
    resumen.to_csv(carpeta / "resumen_por_repeticion.csv", index=False)
    promedio.to_csv(carpeta / "resumen_promedio.csv", index=False)
    por_tipo.to_csv(carpeta / "recall_por_tipo.csv", index=False)
    distribuciones = []
    for nombre, puntajes in puntajes_por_dataset.items():
        tabla = distribucion_puntajes(puntajes)
        tabla.insert(0, "dataset", nombre)
        distribuciones.append(tabla)
    distribucion = pd.concat(distribuciones, ignore_index=True)
    distribucion.to_csv(carpeta / "distribucion_puntajes.csv", index=False)

    graficos = _graficos(carpeta, promedio, por_tipo, puntajes_por_dataset)
    ranking = elegir_modelo(promedio).reset_index()
    elegido = ranking.iloc[0]["modelo"]

    lineas = [
        "# Evaluación comparativa de algoritmos de detección de anomalías",
        "",
        f"Fecha: {datetime.now():%d/%m/%Y %H:%M}",
        "",
        "## Parámetros",
        "",
        f"- Datasets: {', '.join(parametros['datasets'])}",
        f"- Repeticiones por dataset: {parametros['repeticiones']} (semillas distintas)",
        f"- Contaminación asumida: {parametros['contaminacion']:.0%}",
        f"- Anomalías inyectadas por tipo: {parametros['proporcion_por_tipo']:.1%} de los registros",
        "",
        "Tipos de anomalía inyectada:",
        "",
        *[f"- **{tipo}**: {descripcion}" for tipo, descripcion in TIPOS_INYECCION.items()],
        "",
        "## Resultados promedio por dataset",
        "",
        "Precisión, recall y F1 se calculan con el umbral de la contaminación asumida, que "
        "marca más registros que las anomalías inyectadas; por eso la precisión tiene un "
        "techo bajo. AUC-ROC, AUC-PR y Precisión@k no dependen de ese umbral y permiten "
        "comparar los modelos de forma más justa.",
        "",
        _tabla_md(promedio, {
            "dataset": "Dataset", "modelo": "Modelo", "registros": "Registros",
            "segundos": "Tiempo (s)", "inyectadas_precision": "Precisión",
            "inyectadas_recall": "Recall", "inyectadas_f1": "F1",
            "inyectadas_auc_roc": "AUC-ROC", "inyectadas_auc_pr": "AUC-PR",
            "inyectadas_precision_en_k": "Precisión@k",
        }, {
            "registros": "{:.0f}", "segundos": "{:.2f}", "inyectadas_precision": "{:.3f}",
            "inyectadas_recall": "{:.3f}", "inyectadas_f1": "{:.3f}",
            "inyectadas_auc_roc": "{:.3f}", "inyectadas_auc_pr": "{:.3f}",
            "inyectadas_precision_en_k": "{:.3f}",
        }),
        "",
        "## Coincidencia con los avisos del sistema (reglas)",
        "",
        "Proporción de registros marcados por las reglas (posibles duplicados y movimientos "
        "contrarios a la naturaleza de la cuenta) que el modelo también marca como atípicos.",
        "",
        _tabla_md(promedio, {
            "dataset": "Dataset", "modelo": "Modelo", "avisos_reglas": "Avisos",
            "avisos_recall": "Recall", "avisos_auc_roc": "AUC-ROC",
        }, {"avisos_reglas": "{:.0f}", "avisos_recall": "{:.3f}", "avisos_auc_roc": "{:.3f}"}),
        "",
        "## Recall por tipo de anomalía",
        "",
        _tabla_md(
            por_tipo.groupby(["modelo", "tipo"], sort=False)["recall"].mean().reset_index(),
            {"modelo": "Modelo", "tipo": "Tipo", "recall": "Recall"}, {"recall": "{:.3f}"}),
        "",
        "## Distribución de puntajes (normalizados 0-1, última repetición)",
        "",
        _tabla_md(distribucion, {
            "dataset": "Dataset", "modelo": "Modelo", "grupo": "Grupo", "media": "Media",
            "p50": "P50", "p90": "P90", "p99": "P99",
        }, {"media": "{:.3f}", "p50": "{:.3f}", "p90": "{:.3f}", "p99": "{:.3f}"}),
        "",
        "## Orden de los modelos",
        "",
        "Criterio: mayor AUC-PR frente a las anomalías inyectadas; en empate, mayor F1 y "
        "menor tiempo de cómputo.",
        "",
        _tabla_md(ranking, {
            "modelo": "Modelo", "inyectadas_auc_pr": "AUC-PR", "inyectadas_f1": "F1",
            "segundos": "Tiempo (s)",
        }, {"inyectadas_auc_pr": "{:.3f}", "inyectadas_f1": "{:.3f}", "segundos": "{:.2f}"}),
        "",
        f"Modelo con mejor desempeño en esta evaluación: **{elegido}**.",
        "",
    ]
    if graficos:
        lineas += ["## Gráficos", ""] + [f"![{g}]({g})" for g in graficos] + [""]
    elif graficos is None:
        lineas += ["## Gráficos", "", "Gráficos omitidos: matplotlib no está instalado "
                   "(pip install -r requirements.txt).", ""]
    (carpeta / "informe.md").write_text("\n".join(lineas), encoding="utf-8")
    return carpeta / "informe.md", elegido
