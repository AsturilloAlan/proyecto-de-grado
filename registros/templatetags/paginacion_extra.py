"""Filtro para la paginación (`_paginacion.html`).

Django los templates solo pueden invocar métodos sin argumentos, y
`Paginator.get_elided_page_range()` necesita el número de página actual
como argumento — por eso no se puede llamar directo desde el template.
Este filtro es el puente: recibe el objeto de página (lo que devuelve
`Paginator.get_page()`) y arma la lista de números a mostrar, con "..."
en vez de listar cientos de páginas seguidas (un archivo grande puede
tener varios cientos de páginas de registros).
"""
from django import template

register = template.Library()


@register.filter
def rango_paginas(pagina):
    return pagina.paginator.get_elided_page_range(
        pagina.number, on_each_side=1, on_ends=1
    )
