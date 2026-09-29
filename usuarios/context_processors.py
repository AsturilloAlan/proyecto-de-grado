"""Navegación de la interfaz."""
from django.urls import resolve, reverse

# url_name -> (destino del botón volver, texto). Solo páginas fuera del menú principal.
_MAPA_VOLVER = {
    "registros:gestion_crear": ("registros:cargar", "Volver a cargar registros"),
    "registros:gestion_editar": ("registros:cargar", "Volver a cargar registros"),
    "registros:empresa_crear": ("registros:empresas_lista", "Volver a empresas"),
    "registros:empresa_editar": ("registros:empresas_lista", "Volver a empresas"),
    "registros:empresa_historial": ("registros:empresas_lista", "Volver a empresas"),
    "cambiar_clave": ("perfil", "Volver a mi perfil"),
    "cambiar_clave_hecho": ("perfil", "Volver a mi perfil"),
    "usuario_crear": ("usuarios_lista", "Volver a usuarios"),
    "usuario_editar": ("usuarios_lista", "Volver a usuarios"),
}


def navegacion(request):
    contexto = {
        "es_administrador": False,
        "es_auditor": False,
        "puede_confirmar_carga": False,
        "puede_gestionar_gestiones": False,
    }

    usuario = getattr(request, "user", None)
    if usuario is not None and usuario.is_authenticated:
        contexto["es_administrador"] = usuario.is_superuser or usuario.groups.filter(
            name="Administrador"
        ).exists()
        contexto["es_auditor"] = usuario.is_superuser or usuario.groups.filter(
            name="Auditor"
        ).exists()
        # Roles que pueden confirmar una carga con pendientes.
        contexto["puede_confirmar_carga"] = contexto["es_administrador"] or contexto["es_auditor"]
        # Roles que pueden crear y editar gestiones.
        contexto["puede_gestionar_gestiones"] = contexto["es_administrador"] or contexto["es_auditor"]

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
