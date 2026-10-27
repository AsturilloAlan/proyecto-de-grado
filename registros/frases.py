"""Justificaciones frecuentes para la revisión de avisos.

Dejan constancia de lo que el auditor comprobó, no de acciones pendientes. Sirven de
punto de partida: se puede completar con la referencia del documento revisado.
"""

FRASES_RAPIDAS = {
    "valido": [
        ("Respaldo verificado", "Se revisó el comprobante y su documento de respaldo; la operación es correcta."),
        ("Operaciones distintas", "Son dos operaciones distintas con el mismo monto, cada una con su propio respaldo."),
        ("Habitual del giro", "Movimiento habitual del giro de la empresa."),
        ("Explicado por el contador", "El contador de la empresa explicó la operación y presentó su respaldo."),
    ],
    "observado": [
        ("Duplicado real", "La misma operación se registró dos veces."),
        ("Sin respaldo", "La operación no cuenta con documento de respaldo."),
        ("Cuenta incorrecta", "La operación se registró en una cuenta que no corresponde."),
        ("Sin explicación del contador", "El contador de la empresa no explicó ni documentó la operación."),
    ],
}


def contradice_decision(estado, comentario):
    """True si el comentario empieza con una frase pensada para la decisión contraria
    (por ejemplo, "...la operación es correcta. Factura N.º 12" al marcar observado).
    """
    contrario = {"valido": "observado", "observado": "valido"}.get(estado)
    if not contrario:
        return False
    texto = comentario.strip().casefold()
    return any(
        texto.startswith(frase.casefold().rstrip("."))
        for _, frase in FRASES_RAPIDAS[contrario]
    )


# Justificaciones frecuentes para la revisión de atípicas, tomadas de los comentarios que
# dejó la auditora en la validación preliminar. Claves = decisión del modelo de revisión.
FRASES_ATIPICAS = {
    "relevante": [
        ("Sin respaldo", "La operación no cuenta con documento de respaldo."),
        ("Importe sin justificar", "El importe no corresponde a la actividad habitual y no se justificó."),
        ("Registro incorrecto", "La operación se registró en una cuenta o fecha que no corresponde."),
    ],
    "seguimiento": [
        ("Cotejar con balance", "Cotejar el importe con el balance general."),
        ("Cotejar con banco", "Cotejar con el extracto bancario."),
        ("Cotejar con kardex o arqueo", "Cotejar con el kardex o el arqueo de caja."),
        ("Pedir respaldo", "Solicitar el documento de respaldo a la empresa."),
    ],
    "no_relevante": [
        ("Respaldo verificado", "Se revisó el comprobante y su respaldo; la operación es correcta."),
        ("Habitual del giro", "Movimiento habitual del giro de la empresa."),
        ("Cierre o apertura", "Corresponde a un asiento de cierre o apertura de la gestión."),
    ],
}
