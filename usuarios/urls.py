from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("", views.home, name="home"),
    path(
        "login/",
        views.LoginSiempreInicioView.as_view(
            template_name="usuarios/login.html", authentication_form=LoginForm
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("verificar-codigo/", views.verificar_codigo, name="verificar_codigo"),
    path("perfil/", views.perfil, name="perfil"),
    path("perfil/cambiar-clave/", views.CambiarClaveView.as_view(), name="cambiar_clave"),
    path("perfil/cambiar-clave/hecho/", views.cambiar_clave_hecho, name="cambiar_clave_hecho"),
    path("usuarios/", views.usuarios_lista, name="usuarios_lista"),
    path("usuarios/nuevo/", views.usuario_crear, name="usuario_crear"),
    path("usuarios/<int:usuario_id>/editar/", views.usuario_editar, name="usuario_editar"),
]
