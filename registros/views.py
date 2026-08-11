from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CargaArchivoForm
from .models import CargaArchivo
from .services import procesar_carga


@login_required
def cargar_registros(request):
    if request.method == "POST":
        form = CargaArchivoForm(request.POST, request.FILES)
        if form.is_valid():
            carga = form.save(commit=False)
            carga.usuario = request.user
            carga.save()
            procesar_carga(carga)
            return redirect("registros:detalle_carga", carga_id=carga.id)
    else:
        form = CargaArchivoForm()

    cargas_anteriores = CargaArchivo.objects.select_related("empresa", "gestion").order_by(
        "-fecha_carga"
    )[:10]
    return render(
        request,
        "registros/cargar.html",
        {"form": form, "cargas_anteriores": cargas_anteriores},
    )


@login_required
def detalle_carga(request, carga_id):
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion"), pk=carga_id
    )
    errores = carga.errores.all()[:200]
    return render(
        request,
        "registros/detalle_carga.html",
        {"carga": carga, "errores": errores},
    )
