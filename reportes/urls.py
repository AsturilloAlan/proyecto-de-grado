from django.urls import path

from . import views

app_name = "reportes"

urlpatterns = [
    path("", views.indice, name="indice"),
    path("empresa/<int:empresa_id>/gestion/<int:gestion_id>/atipicas/",
         views.atipicas_gestion, name="atipicas_gestion"),
    path("empresa/<int:empresa_id>/gestion/<int:gestion_id>/observaciones/",
         views.observaciones_gestion, name="observaciones_gestion"),
    path("empresa/<int:empresa_id>/gestion/<int:gestion_id>/hallazgos/",
         views.hallazgos_gestion, name="hallazgos_gestion"),
    path("empresa/<int:empresa_id>/gestion/<int:gestion_id>/graficos/",
         views.panorama, name="panorama"),
    path("empresa/<int:empresa_id>/gestion/<int:gestion_id>/graficos/datos/",
         views.panorama_datos, name="panorama_datos"),
    path("empresa/<int:empresa_id>/comparativo/", views.comparativo_empresa, name="comparativo"),
    path("carga/<int:carga_id>/atipicas/", views.atipicas_carga, name="atipicas"),
    path("carga/<int:carga_id>/hallazgos/", views.hallazgos_carga, name="hallazgos"),
]
