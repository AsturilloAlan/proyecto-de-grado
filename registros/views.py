from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from usuarios.decorators import rol_requerido

from .auditoria import registrar_creacion, registrar_edicion
from .forms import AnulacionForm, CargaArchivoForm, EmpresaAuditadaForm, GestionForm
from .models import CargaArchivo, EmpresaAuditada, Gestion, HistorialCambio
from .services import procesar_carga

CAMPOS_AUDITABLES_EMPRESA = [
    "nombre",
    "nit",
    "rubro",
    "categoria_cierre",
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

    cargas_qs = CargaArchivo.objects.select_related("empresa", "gestion", "usuario").order_by(
        "-fecha_carga"
    )

    # Filtro por empresa/gestión/estado: útil apenas hay más de un puñado de
    # cargas (varias empresas y/o varios años), que es el caso real de uso.
    filtro_empresa = request.GET.get("empresa", "")
    filtro_gestion = request.GET.get("gestion", "")
    filtro_estado = request.GET.get("estado", "")
    if filtro_empresa.isdigit():
        cargas_qs = cargas_qs.filter(empresa_id=filtro_empresa)
    if filtro_gestion.isdigit():
        cargas_qs = cargas_qs.filter(gestion_id=filtro_gestion)
    if filtro_estado in dict(CargaArchivo.ESTADO_CHOICES):
        cargas_qs = cargas_qs.filter(estado=filtro_estado)

    paginador = Paginator(cargas_qs, 8)
    cargas_anteriores = paginador.get_page(request.GET.get("pagina"))

    # Para que los filtros no se pierdan al cambiar de página (ver
    # templates/_paginacion.html).
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    querystring = parametros.urlencode()

    # Solo se listan como opción empresas/gestiones que realmente tienen
    # alguna carga: un filtro con opciones que siempre dan resultado vacío
    # no sirve de nada.
    empresas_con_cargas = EmpresaAuditada.objects.filter(
        id__in=CargaArchivo.objects.values_list("empresa_id", flat=True).distinct()
    ).order_by("nombre")
    gestiones_con_cargas = Gestion.objects.filter(
        id__in=CargaArchivo.objects.values_list("gestion_id", flat=True).distinct()
    )

    return render(
        request,
        "registros/cargar.html",
        {
            "form": form,
            "cargas_anteriores": cargas_anteriores,
            "querystring": querystring,
            "empresas_con_cargas": empresas_con_cargas,
            "gestiones_con_cargas": gestiones_con_cargas,
            "estado_choices": CargaArchivo.ESTADO_CHOICES,
            "filtro_empresa": filtro_empresa,
            "filtro_gestion": filtro_gestion,
            "filtro_estado": filtro_estado,
            "hay_filtro_activo": bool(filtro_empresa or filtro_gestion or filtro_estado),
            "hay_cargas_en_total": empresas_con_cargas.exists(),
        },
    )


@login_required
def detalle_carga(request, carga_id):
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario"), pk=carga_id
    )
    errores = carga.errores.filter(tipo="error")[:200]
    avisos = carga.errores.filter(tipo="aviso")[:200]

    # Los registros que sí se guardaron bien (RF-01): se muestran acá para
    # que el auditor vea de inmediato qué quedó cargado, no solo el
    # resumen de errores/avisos. Puede haber miles de filas por carga, así
    # que va paginado en vez de mostrarlas todas de una.
    registros_qs = carga.registros.select_related("cuenta").order_by("fila_origen")
    paginador = Paginator(registros_qs, 50)
    registros_pagina = paginador.get_page(request.GET.get("pagina"))

    return render(
        request,
        "registros/detalle_carga.html",
        {
            "carga": carga,
            "errores": errores,
            "avisos": avisos,
            "registros_pagina": registros_pagina,
        },
    )


@rol_requerido("Administrador", "Auditor")
@require_POST
def carga_confirmar_validacion(request, carga_id):
    """Permite al Administrador o Auditor dar por válida definitivamente
    una carga que quedó "Cargado con pendientes" (RF-02), en vez de que
    ese estado quede así para siempre en la actividad reciente.

    Solo tiene sentido para ese estado puntual: una carga ya "Validado"
    no necesita confirmación, y una "Con errores" (cero filas guardadas)
    no tiene nada que dar por bueno. La confirmación en sí misma (el
    "¿estás seguro?") se pide del lado del template con un cuadro de
    diálogo antes de enviar el POST, porque es una decisión que no se
    puede deshacer con un clic — y queda registrada en HistorialCambio,
    además de en los campos revisado_por/fecha_revision, para no perder
    el rastro de quién aceptó las observaciones y cuándo (es información
    sensible de auditoría).
    """
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if carga.estado != "con_observaciones":
        messages.error(request, "Esta carga no tiene observaciones pendientes por confirmar.")
        return redirect("registros:detalle_carga", carga_id=carga.id)

    valores_anteriores = {"estado": carga.estado}
    carga.estado = "validado"
    carga.revisado_por = request.user
    carga.fecha_revision = timezone.now()
    carga.save()
    registrar_edicion(carga, valores_anteriores, request.user, ["estado"])

    messages.success(
        request,
        f"Carga #{carga.id} confirmada como válida. Quedó registrado que la revisaste "
        "vos, con la fecha y hora actual.",
    )
    return redirect("registros:detalle_carga", carga_id=carga.id)


@rol_requerido("Administrador")
def carga_anular(request, carga_id):
    """Corrige un error humano al elegir empresa/gestión al cargar un
    archivo (ej. gestión 2025 en vez de 2022), sin "editar" en silencio
    una carga ya procesada — ver el comentario de ESTADO_CHOICES en
    models.py sobre por qué no se permite editar directamente.

    La carga anulada NUNCA se borra: queda con su estado en "Anulada",
    el motivo escrito acá, y quién/cuándo la anuló, siempre visible en su
    detalle. El camino correcto después de anular es volver a "Cargar
    registros" y subir el archivo de nuevo con los datos correctos.
    """
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if carga.estado == "anulada":
        messages.error(request, "Esta carga ya está anulada.")
        return redirect("registros:detalle_carga", carga_id=carga.id)

    if request.method == "POST":
        form = AnulacionForm(request.POST)
        if form.is_valid():
            valores_anteriores = {
                "estado": carga.estado,
                "motivo_anulacion": carga.motivo_anulacion,
            }
            carga.estado = "anulada"
            carga.anulado_por = request.user
            carga.fecha_anulacion = timezone.now()
            carga.motivo_anulacion = form.cleaned_data["motivo"]
            carga.save()
            registrar_edicion(
                carga, valores_anteriores, request.user, ["estado", "motivo_anulacion"]
            )
            messages.success(
                request,
                f"Carga #{carga.id} anulada. Ya podés subir el archivo de nuevo con "
                "los datos correctos desde \"Cargar registros\".",
            )
            return redirect("registros:detalle_carga", carga_id=carga.id)
    else:
        form = AnulacionForm()

    return render(
        request, "registros/carga_anular.html", {"form": form, "carga": carga}
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
                # Las fechas ya no se asumen siempre calendario: se calculan
                # según la categoría de cierre (SIN) elegida para la empresa
                # (ver EmpresaAuditada.fechas_gestion_para). Se reutiliza si
                # ya existe una gestión con esas mismas fechas exactas (evita
                # duplicados si dos empresas de la misma categoría comparten
                # gestión).
                fecha_inicio, fecha_fin = empresa.fechas_gestion_para(anio)
                gestion, creada = Gestion.objects.get_or_create(
                    anio=anio,
                    fecha_inicio=fecha_inicio,
                    fecha_fin=fecha_fin,
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
