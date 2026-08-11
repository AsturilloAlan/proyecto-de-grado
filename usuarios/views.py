from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    """Página principal tras iniciar sesión (placeholder del dashboard)."""
    return render(request, "usuarios/home.html")
