"""Filtro de paginación con "…": la plantilla no puede llamar a
`get_elided_page_range()` porque necesita el número de página como argumento.
"""
from django import template

register = template.Library()


@register.filter
def rango_paginas(pagina):
    return pagina.paginator.get_elided_page_range(
        pagina.number, on_each_side=1, on_ends=1
    )
