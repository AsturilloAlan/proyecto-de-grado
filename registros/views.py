from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from usuarios.decorators import rol_requerido

from .auditoria import registrar_creacion, registrar_edicion
from .forms import CargaArchivoForm, EmpresaAuditadaForm, GestionForm
from .models import CargaArchivo, EmpresaAuditada, Gestion, HistorialCambio
from .services import procesar_carga

CAMPOS_AUDITABLES_EMPRESA = [
    "nombre",
    "nit",
    "rubro",
    "contacto_nombre",
    "contacto_email",
    "contacto_telefono",
]


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

    cargas_qs = CargaArchivo.objects.select_related("empresa", "gestion").order_by(
        "-fecha_carga"
    )
    paginador = Paginator(cargas_qs, 8)
    cargas_anteriores = paginador.get_page(request.GET.get("pagina"))
    return render(
        request,
        "registros/cargar.html",
        {"form": form, "cargas_anteriores": cargas_anteriores},
    )


@login_required
def detalle_carga(request, carga_id):
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario"), pk=carga_id
    )
    errores = carga.errores.filter(tipo="error")[:200]
    avisos = carga.errores.filter(tipo="aviso")[:200]
    return render(
        request,
        "registros/detalle_carga.html",
        {"carga": carga, "errores": errores, "avisos": avisos},
    )


@rol_requerido("Administrador")
def gestion_crear(request):
    """Alta de una nueva gestión (año fiscal) para poder cargar archivos de
    ese periodo. Solo Administrador, igual que empresas clientes."""
    if request.method == "POST":
        form = GestionForm(request.POST)
        if form.is_valid():
            gestion = form.save()
            registrar_creacion(gestion, request.user)
            messages.success(request, f"Gestión {gestion.anio} creada correctamente.")
            return redirect("registros:cargar")
    else:
        form = GestionForm()
    return render(
        request,
        "registros/gestion_form.html",
        {"form": form, "gestiones": Gestion.objects.all()},
    )


CAMPOS_AUDITABLES_GESTION = ["anio", "fecha_inicio", "fecha_fin"]


@rol_requerido("Administrador")
def gestion_editar(request, gestion_id):
    """Corrige un error humano al registrar una gestión (año o fechas mal
    puestas). No se ofrece "eliminar": si la gestión ya se usó en alguna
    carga, la base de datos lo protege automáticamente (on_delete=PROTECT
    en CargaArchivo.gestion); si nunca se usó, dejarla sin usar no genera
    ningún problema, así que no hace falta borrarla."""
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    if request.method == "POST":
        valores_anteriores = {
            campo: getattr(gestion, campo) for campo in CAMPOS_AUDITABLES_GESTION
        }
        form = GestionForm(request.POST, instance=gestion)
        if form.is_valid():
            form.save()
            registrar_edicion(
                gestion, valores_anteriores, request.user, CAMPOS_AUDITABLES_GESTION
            )
            messages.success(request, f"Gestión {gestion.anio} actualizada correctamente.")
            return redirect("registros:gestion_crear")
    else:
        form = GestionForm(instance=gestion)
    return render(
        request,
        "registros/gestion_form.html",
        {
            "form": form,
            "gestiones": Gestion.objects.all(),
            "gestion": gestion,
            "titulo": f"Editar gestión {gestion.anio}",
        },
    )


@rol_requerido("Administrador")
def empresas_lista(request):
    """Listado de empresas clientes de ST&S (RF-09, solo Administrador)."""
    busqueda = request.GET.get("q", "").strip()
    empresas_qs = EmpresaAuditada.objects.all()
    if busqueda:
        empresas_qs = empresas_qs.filter(
            Q(nombre__icontains=busqueda) | Q(nit__icontains=busqueda)
        )

    paginador = Paginator(empresas_qs, 10)
    empresas = paginador.get_page(request.GET.get("pagina"))
    return render(
        request,
        "registros/empresas_lista.html",
        {"empresas": empresas, "busqueda": busqueda},
    )


@rol_requerido("Administrador")
def empresa_crear(request):
    if request.method == "POST":
        form = EmpresaAuditadaForm(request.POST)
        if form.is_valid():
            empresa = form.save()
            registrar_creacion(empresa, request.user)

            anio = form.cleaned_data.get("anio_gestion_inicial")
            if anio:
                # Se reutiliza si ya existe una con año calendario completo
                # para ese año (evita duplicados si dos empresas usan la
                # misma gestión "normal" de 01/01 - 31/12).
                gestion, creada = Gestion.objects.get_or_create(
                    anio=anio,
                    fecha_inicio=date(anio, 1, 1),
                    fecha_fin=date(anio, 12, 31),
                )
                if creada:
                    registrar_creacion(gestion, request.user)
                messages.success(
                    request,
                    f"Empresa registrada correctamente, con la gestión {anio} lista para usar.",
                )
            else:
                messages.success(request, "Empresa registrada correctamente.")
            return redirect("registros:empresas_lista")
    else:
        form = EmpresaAuditadaForm()
    return render(
        request,
        "registros/empresa_form.html",
        {"form": form, "titulo": "Registrar empresa cliente"},
    )


@rol_requerido("Administrador")
def empresa_editar(request, empresa_id):
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    if request.method == "POST":
        valores_anteriores = {
            campo: getattr(empresa, campo) for campo in CAMPOS_AUDITABLES_EMPRESA
        }
        form = EmpresaAuditadaForm(request.POST, instance=empresa)
        if form.is_valid():
            form.save()
            registrar_edicion(
                empresa, valores_anteriores, request.user, CAMPOS_AUDITABLES_EMPRESA
            )
            messages.success(request, "Empresa actualizada correctamente.")
            return redirect("registros:empresas_lista")
    else:
        form = EmpresaAuditadaForm(instance=empresa)
    return render(
        request,
        "registros/empresa_form.html",
        {"form": form, "titulo": f"Editar empresa: {empresa.nombre}", "empresa": empresa},
    )


@rol_requerido("Administrador")
def empresa_historial(request, empresa_id):
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    cambios = (
        HistorialCambio.objects.filter(modelo="EmpresaAuditada", objeto_id=empresa_id)
        .select_related("usuario")
    )
    return render(
        request,
        "registros/empresa_historial.html",
        {"empresa": empresa, "cambios": cambios},
    )
