from django.urls import path

from . import views

app_name = "registros"

urlpatterns = [
    path("cargar/", views.cargar_registros, name="cargar"),
    path("cargar/<int:carga_id>/", views.detalle_carga, name="detalle_carga"),
    path("empresas/", views.empresas_lista, name="empresas_lista"),
    path("empresas/nueva/", views.empresa_crear, name="empresa_crear"),
    path("empresas/<int:empresa_id>/editar/", views.empresa_editar, name="empresa_editar"),
    path(
        "empresas/<int:empresa_id>/historial/",
        views.empresa_historial,
        name="empresa_historial",
    ),
]
