"""Dataset de una carga para los modelos: lo comparten la exportación CSV y la evaluación."""

COLUMNAS_DATASET = [
    "fila_origen", "fecha", "cuenta_codigo", "cuenta_nombre", "cuenta_tipo",
    "comprobante", "glosa", "debe", "haber", "saldo",
    # Avisos que el sistema ya detecta por reglas, y la decisión del auditor.
    "aviso_duplicado", "aviso_naturaleza", "aviso_observado",
]


def _filas_con_aviso(carga):
    """Filas marcadas por cada regla y filas cuyo aviso el auditor confirmó."""
    duplicado, naturaleza, observado = set(), set(), set()
    avisos = carga.errores.filter(tipo="aviso").values_list("fila", "campo", "estado_revision")
    for fila, campo, estado in avisos:
        if campo == "duplicado":
            duplicado.add(fila)
        elif campo == "naturaleza_cuenta":
            naturaleza.add(fila)
        if estado == "observado":
            observado.add(fila)
    return duplicado, naturaleza, observado


def filas_dataset(carga, celda=lambda valor: valor):
    """Una lista por registro, en el orden de COLUMNAS_DATASET. `celda` limpia los textos
    (por ejemplo, contra inyección de fórmulas en CSV).
    """
    duplicado, naturaleza, observado = _filas_con_aviso(carga)
    registros = carga.registros.select_related("cuenta").order_by("fila_origen")
    for registro in registros.iterator():
        fila = registro.fila_origen
        yield [
            fila,
            registro.fecha.isoformat(),
            celda(registro.cuenta.codigo),
            celda(registro.cuenta.nombre),
            registro.cuenta.tipo,
            celda(registro.numero_comprobante),
            celda(registro.glosa),
            registro.debe,
            registro.haber,
            registro.saldo if registro.saldo is not None else "",
            int(fila in duplicado),
            int(fila in naturaleza),
            int(fila in observado),
        ]
