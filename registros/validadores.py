"""Conversión acotada de parámetros no confiables antes de consultar la base."""


def entero_positivo(valor):
    # isdigit() acepta, por ejemplo, ², que int() no puede convertir.
    if not isinstance(valor, str) or not valor.isascii() or not valor.isdecimal() or len(valor) > 19:
        return None
    numero = int(valor)
    return numero if 0 < numero <= 9223372036854775807 else None
