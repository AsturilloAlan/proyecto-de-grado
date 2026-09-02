"""Navegación de la interfaz.

En vez de un botón "volver" basado en el historial del navegador
(`history.back()`), que puede confundir al usuario porque las
redirecciones de login (`?next=`) o de rol (`rol_requerido`) alteran
ese historial, cada página protegida tiene un destino de "volver" fijo
y predecible, resuelto aquí según la URL actual.
"""
from django.urls import resolve, reverse

# url_name (con namespace) -> (url_name de destino, texto del botón)
# Solo se listan páginas que NO están directamente en el menú principal
# (cargar/empresas ya tienen su propio enlace en el navbar).
_MAPA_VOLVER = {
    "registros:detalle_carga": ("registros:cargar", "Volver a cargas"),
    "registros:empresa_crear": ("registros:empresas_lista", "Volver a empresas"),
    "registros:empresa_editar": ("registros:empresas_lista", "Volver a empresas"),
    "registros:empresa_historial": ("registros:empresas_lista", "Volver a empresas"),
    "cambiar_clave": ("perfil", "Volver a mi perfil"),
    "cambiar_clave_hecho": ("perfil", "Volver a mi perfil"),
    "usuario_crear": ("usuarios_lista", "Volver a usuarios"),
    "usuario_editar": ("usuarios_lista", "Volver a usuarios"),
}


def navegacion(request):
    contexto = {"es_administrador": False}

    usuario = getattr(request, "user", None)
    if usuario is not None and usuario.is_authenticated:
        contexto["es_administrador"] = usuario.is_superuser or usuario.groups.filter(
            name="Administrador"
        ).exists()

    try:
        coincidencia = resolve(request.path_info)
    except Exception:
        return contexto

    nombre = (
        f"{coincidencia.namespace}:{coincidencia.url_name}"
        if coincidencia.namespace
        else coincidencia.url_name
    )
    contexto["url_actual"] = nombre

    destino = _MAPA_VOLVER.get(nombre)
    if destino:
        url_name, texto = destino
        contexto["volver_url"] = reverse(url_name)
        contexto["volver_texto"] = texto

    return contexto
