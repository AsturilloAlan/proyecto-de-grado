from django.urls import path

from . import views

app_name = "registros"

urlpatterns = [
    path("cargar/", views.cargar_registros, name="cargar"),
    path("cargar/<int:carga_id>/", views.detalle_carga, name="detalle_carga"),
    path(
        "cargar/<int:carga_id>/confirmar/",
        views.carga_confirmar_validacion,
        name="carga_confirmar_validacion",
    ),
    path("cargar/<int:carga_id>/anular/", views.carga_anular, name="carga_anular"),
    path(
        "cargar/<int:carga_id>/avisos/marcar-revisados/",
        views.avisos_marcar_revisados,
        name="avisos_marcar_revisados",
    ),
    path(
        "cargar/<int:carga_id>/exportar-dataset/",
        views.exportar_dataset_csv,
        name="exportar_dataset_csv",
    ),
    path(
        "cargar/<int:carga_id>/avisos/<int:aviso_id>/estado/",
        views.aviso_marcar_estado,
        name="aviso_marcar_estado",
    ),
    path(
        "cargar/<int:carga_id>/reporte-observaciones/",
        views.reporte_observaciones,
        name="reporte_observaciones",
    ),
    path("gestiones/nueva/", views.gestion_crear, name="gestion_crear"),
    path("gestiones/<int:gestion_id>/editar/", views.gestion_editar, name="gestion_editar"),
    path("empresas/", views.empresas_lista, name="empresas_lista"),
    path("empresas/nueva/", views.empresa_crear, name="empresa_crear"),
    path("empresas/<int:empresa_id>/editar/", views.empresa_editar, name="empresa_editar"),
    path(
        "empresas/<int:empresa_id>/historial/",
        views.empresa_historial,
        name="empresa_historial",
    ),
]
