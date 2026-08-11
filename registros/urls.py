from django.urls import path

from . import views

app_name = "registros"

urlpatterns = [
    path("cargar/", views.cargar_registros, name="cargar"),
    path("cargar/<int:carga_id>/", views.detalle_carga, name="detalle_carga"),
]
