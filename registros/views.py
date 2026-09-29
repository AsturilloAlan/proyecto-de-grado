import csv
import json
import logging
import math

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from usuarios.decorators import rol_requerido

from .auditoria import registrar_creacion, registrar_edicion
from .forms import AnulacionForm, CargaArchivoForm, EmpresaAuditadaForm, GestionForm
from .models import CargaArchivo, EmpresaAuditada, ErrorValidacion, Gestion, HistorialCambio
from .services import procesar_carga
from .estados import anotar_avisos_pendientes, filtrar_por_importacion, filtrar_por_revision

logger = logging.getLogger(__name__)

# Roles de negocio que pueden operar sobre cargas (subir, ver, revisar, exportar).
ROLES_OPERATIVOS = ("Administrador", "Auditor")

# Tope de IDs por pedido de marcado en lote.
MAX_IDS_POR_LOTE = 500


def _registrar_decisiones(avisos_antes, usuario):
    """Registra en HistorialCambio el estado anterior y el nuevo de cada aviso
    revisado.
    """
    filas = []
    for aviso, estado_anterior, comentario_anterior in avisos_antes:
        filas.append(
            HistorialCambio(
                modelo="ErrorValidacion",
                objeto_id=aviso.pk,
                objeto_descripcion=str(aviso)[:200],
                accion="edicion",
                campo="estado_revision",
                valor_anterior=f"{estado_anterior} | {comentario_anterior}".strip(" |"),
                valor_nuevo=f"{aviso.estado_revision} | {aviso.comentario_revision}".strip(" |"),
                usuario=usuario,
            )
        )
    HistorialCambio.objects.bulk_create(filas, batch_size=1000)


def _leer_json_objeto(request):
    """Devuelve el cuerpo JSON si es un objeto; si no, None."""
    try:
        datos = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


def _celda_csv_segura(texto):
    """Evita que Excel interprete el texto como fórmula (inyección CSV). No modifica
    el dato guardado.
    """
    texto = "" if texto is None else str(texto)
    if texto and texto[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + texto
    return texto


def _carga_admite_revision(carga):
    """Una carga anulada queda en solo lectura: su revisión ya no cuenta."""
    return carga.estado != "anulada"


CAMPOS_AUDITABLES_EMPRESA = [
    "nombre",
    "nit",
    "rubro",
    "categoria_cierre",
    "contacto_nombre",
    "contacto_email",
    "contacto_telefono",
]


@rol_requerido(*ROLES_OPERATIVOS)
def cargar_registros(request):
    if request.method == "POST":
        form = CargaArchivoForm(request.POST, request.FILES)
        if form.is_valid():
            carga = form.save(commit=False)
            carga.usuario = request.user
            carga.save()
            try:
                procesar_carga(carga)
            except Exception:
                # procesar_carga no deja datos a medias; solo se marca la carga como
                # fallida.
                logger.exception("Fallo al procesar la carga #%s", carga.id)
                carga.refresh_from_db()
                carga.estado = "con_errores"
                carga.save(update_fields=["estado"])
                ErrorValidacion.objects.create(
                    carga=carga, fila=0, campo="archivo",
                    descripcion=(
                        "Ocurrió un error inesperado al procesar el archivo y no se "
                        "guardó ninguna fila. Anula esta carga y vuelve a intentarlo; "
                        "si se repite, revisa el formato del archivo."
                    ),
                )
            return redirect("registros:detalle_carga", carga_id=carga.id)
    else:
        form = CargaArchivoForm()

    cargas_qs = anotar_avisos_pendientes(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario")
    ).order_by("-fecha_carga")

    # Filtros por empresa, gestión, importación y revisión.
    filtro_empresa = request.GET.get("empresa", "")
    filtro_gestion = request.GET.get("gestion", "")
    filtro_importacion = request.GET.get("importacion", "")
    filtro_revision = request.GET.get("revision", "")
    if filtro_empresa.isdigit():
        cargas_qs = cargas_qs.filter(empresa_id=filtro_empresa)
    if filtro_gestion.isdigit():
        cargas_qs = cargas_qs.filter(gestion_id=filtro_gestion)
    cargas_qs = filtrar_por_importacion(cargas_qs, filtro_importacion)
    cargas_qs = filtrar_por_revision(cargas_qs, filtro_revision)

    paginador = Paginator(cargas_qs, 8)
    cargas_anteriores = paginador.get_page(request.GET.get("pagina"))

    # Conserva los filtros al cambiar de página.
    parametros = request.GET.copy()
    parametros.pop("pagina", None)
    querystring = parametros.urlencode()

    # En los filtros solo aparecen empresas y gestiones que tienen cargas.
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
            "opciones_importacion": [
                (k, v) for k, v in CargaArchivo.ETIQUETAS_IMPORTACION.items() if k != "procesando"
            ],
            "opciones_revision": [
                (k, v) for k, v in CargaArchivo.ETIQUETAS_REVISION.items() if k != "no_aplica"
            ],
            "filtro_empresa": filtro_empresa,
            "filtro_gestion": filtro_gestion,
            "filtro_importacion": filtro_importacion,
            "filtro_revision": filtro_revision,
            "hay_filtro_activo": bool(
                filtro_empresa or filtro_gestion or filtro_importacion or filtro_revision
            ),
            "hay_cargas_en_total": empresas_con_cargas.exists(),
        },
    )


@rol_requerido(*ROLES_OPERATIVOS)
def detalle_carga(request, carga_id):
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario"), pk=carga_id
    )
    # Errores y avisos se paginan por separado.
    campo_aviso_filtro = request.GET.get("campo_aviso", "").strip()

    errores_qs = carga.errores.filter(tipo="error").order_by("fila")
    avisos_qs = carga.errores.filter(tipo="aviso").order_by("revisado", "fila")
    if campo_aviso_filtro:
        avisos_qs = avisos_qs.filter(campo=campo_aviso_filtro)
    errores_paginador = Paginator(errores_qs, 25)
    avisos_paginador = Paginator(avisos_qs, 25)
    errores = errores_paginador.get_page(request.GET.get("pagina_errores"))
    avisos = avisos_paginador.get_page(request.GET.get("pagina_avisos"))

    # Resumen de errores y avisos por tipo de campo.
    resumen_errores = list(
        carga.errores.filter(tipo="error")
        .values("campo")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    # En avisos se cuentan solo los pendientes, que es lo que afecta el botón de marcar
    # por tipo.
    resumen_avisos = list(
        carga.errores.filter(tipo="aviso", revisado=False)
        .values("campo")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    # Filtros de registros. Se aplican antes de calcular el salto a una fila.
    cuenta_filtro = request.GET.get("cuenta_codigo", "").strip()
    comprobante_filtro = request.GET.get("numero_comprobante", "").strip()

    registros_qs = carga.registros.select_related("cuenta").order_by("fila_origen")
    if cuenta_filtro:
        # startswith: al filtrar "2" se obtiene el grupo 2 completo y no códigos con un
        # 2 en medio.
        registros_qs = registros_qs.filter(cuenta__codigo__startswith=cuenta_filtro)
    if comprobante_filtro:
        registros_qs = registros_qs.filter(numero_comprobante__icontains=comprobante_filtro)
    paginador = Paginator(registros_qs, 50)

    # Salto a una fila: se calcula en qué página cae según los registros guardados hasta
    # esa fila.
    numero_pagina = request.GET.get("pagina")
    fila_buscada = None
    fila_encontrada = False
    fila_param = request.GET.get("fila", "").strip()
    if fila_param.isdigit():
        fila_buscada = int(fila_param)
        posicion = registros_qs.filter(fila_origen__lte=fila_buscada).count()
        numero_pagina = math.ceil(posicion / paginador.per_page) or 1
        fila_encontrada = registros_qs.filter(fila_origen=fila_buscada).exists()

    # Motivo por el que la fila buscada no aparece: filtrada, rechazada o inexistente.
    motivo_fila_no_encontrada = ""
    if fila_buscada is not None and not fila_encontrada:
        if carga.registros.filter(fila_origen=fila_buscada).exists():
            motivo_fila_no_encontrada = "filtrada"
        elif carga.errores.filter(tipo="error", fila=fila_buscada).exists():
            motivo_fila_no_encontrada = "rechazada"
        else:
            motivo_fila_no_encontrada = "inexistente"

    registros_pagina = paginador.get_page(numero_pagina)

    # Conserva los filtros en la paginación; "fila" se quita para que el salto sea de
    # una sola vez.
    parametros_registros = request.GET.copy()
    parametros_registros.pop("pagina", None)
    parametros_registros.pop("fila", None)
    registros_querystring = parametros_registros.urlencode()

    avisos_pendientes = carga.errores.filter(tipo="aviso", revisado=False).count()

    # Indica cuando el tipo filtrado ya no tiene avisos pendientes.
    filtro_ya_revisado = bool(
        campo_aviso_filtro
        and not any(item["campo"] == campo_aviso_filtro for item in resumen_avisos)
    )

    return render(
        request,
        "registros/detalle_carga.html",
        {
            "carga": carga,
            "errores": errores,
            "avisos": avisos,
            "resumen_errores": resumen_errores,
            "resumen_avisos": resumen_avisos,
            "avisos_pendientes": avisos_pendientes,
            "campo_aviso_filtro": campo_aviso_filtro,
            "filtro_ya_revisado": filtro_ya_revisado,
            "registros_pagina": registros_pagina,
            "fila_buscada": fila_buscada,
            "fila_encontrada": fila_encontrada,
            "motivo_fila_no_encontrada": motivo_fila_no_encontrada,
            "carga_admite_revision": _carga_admite_revision(carga),
            "cuenta_filtro": cuenta_filtro,
            "comprobante_filtro": comprobante_filtro,
            "registros_querystring": registros_querystring,
        },
    )


@rol_requerido(*ROLES_OPERATIVOS)
def exportar_dataset_csv(request, carga_id):
    """Exporta a CSV los registros guardados de una carga. Herramienta temporal para
    prototipar los modelos.
    """
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    registros = carga.registros.select_related("cuenta").order_by("fila_origen")

    respuesta = HttpResponse(content_type="text/csv")
    respuesta["Content-Disposition"] = f'attachment; filename="dataset_carga_{carga.id}.csv"'
    escritor = csv.writer(respuesta)
    escritor.writerow(
        ["fila_origen", "fecha", "cuenta_codigo", "cuenta_nombre", "cuenta_tipo",
         "comprobante", "glosa", "debe", "haber", "saldo"]
    )
    for registro in registros.iterator():
        escritor.writerow(
            [
                registro.fila_origen,
                registro.fecha.isoformat(),
                _celda_csv_segura(registro.cuenta.codigo),
                _celda_csv_segura(registro.cuenta.nombre),
                registro.cuenta.tipo,
                _celda_csv_segura(registro.numero_comprobante),
                _celda_csv_segura(registro.glosa),
                registro.debe,
                registro.haber,
                registro.saldo if registro.saldo is not None else "",
            ]
        )
    return respuesta


@rol_requerido(*ROLES_OPERATIVOS)
@require_POST
def avisos_marcar_revisados(request, carga_id):
    """Marca en lote como válidos los avisos pendientes indicados, con una
    justificación común.
    """
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if not _carga_admite_revision(carga):
        return JsonResponse(
            {"ok": False, "error": "La carga está anulada; su revisión es de solo lectura."},
            status=403,
        )

    datos = _leer_json_objeto(request)
    if datos is None:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    aviso_ids = datos.get("aviso_ids") or []
    campo = datos.get("campo") or ""
    comentario = datos.get("comentario") or ""
    if not isinstance(campo, str) or not isinstance(comentario, str) or not isinstance(aviso_ids, list):
        return JsonResponse({"ok": False, "error": "Datos con formato inválido."}, status=400)
    campo = campo.strip()
    comentario = comentario.strip()
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in aviso_ids):
        return JsonResponse({"ok": False, "error": "Los IDs de aviso deben ser números."}, status=400)
    if len(aviso_ids) > MAX_IDS_POR_LOTE:
        return JsonResponse({"ok": False, "error": "Demasiados avisos en un solo pedido."}, status=400)
    if not comentario:
        return JsonResponse(
            {"ok": False, "error": "La justificación es obligatoria para marcar en lote."},
            status=400,
        )

    # Solo avisos pendientes: una decisión ya tomada no se cambia en lote.
    base_qs = ErrorValidacion.objects.filter(carga=carga, tipo="aviso", revisado=False)
    if campo:
        avisos_qs = base_qs.filter(campo=campo)
    elif aviso_ids:
        avisos_qs = base_qs.filter(id__in=aviso_ids)
    else:
        return JsonResponse({"ok": False, "error": "No se seleccionó ningún aviso."}, status=400)

    ahora = timezone.now()
    with transaction.atomic():
        avisos = list(avisos_qs.select_for_update())
        if not avisos:
            return JsonResponse({"ok": False, "error": "No hay avisos pendientes para marcar."}, status=400)
        actualizados = [a.id for a in avisos]
        antes = [(a, a.estado_revision, a.comentario_revision) for a in avisos]
        ErrorValidacion.objects.filter(id__in=actualizados, revisado=False).update(
            revisado=True,
            estado_revision="valido",
            revisado_por=request.user,
            fecha_revision=ahora,
            comentario_revision=comentario,
        )
        for a in avisos:
            a.estado_revision = "valido"
            a.comentario_revision = comentario
        _registrar_decisiones(antes, request.user)

    avisos_pendientes = ErrorValidacion.objects.filter(
        carga_id=carga_id, tipo="aviso", revisado=False
    ).count()

    return JsonResponse(
        {
            "ok": True,
            "actualizados": actualizados,
            "revisado_por": request.user.username,
            "fecha_revision": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            "avisos_pendientes": avisos_pendientes,
        }
    )


@rol_requerido(*ROLES_OPERATIVOS)
@require_POST
def aviso_marcar_estado(request, carga_id, aviso_id):
    """Registra la decisión del auditor sobre un aviso: válido (falso positivo) u
    observado.
    """
    aviso = get_object_or_404(
        ErrorValidacion.objects.select_related("carga"),
        pk=aviso_id, carga_id=carga_id, tipo="aviso",
    )
    if not _carga_admite_revision(aviso.carga):
        return JsonResponse(
            {"ok": False, "error": "La carga está anulada; su revisión es de solo lectura."},
            status=403,
        )

    datos = _leer_json_objeto(request)
    if datos is None:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    estado = datos.get("estado") or ""
    comentario = datos.get("comentario") or ""
    if not isinstance(estado, str) or not isinstance(comentario, str):
        return JsonResponse({"ok": False, "error": "Datos con formato inválido."}, status=400)
    estado = estado.strip()
    comentario = comentario.strip()

    if estado not in ("valido", "observado"):
        return JsonResponse(
            {"ok": False, "error": "El estado debe ser 'valido' u 'observado'."},
            status=400,
        )
    if not comentario:
        return JsonResponse(
            {"ok": False, "error": "La justificación es obligatoria."}, status=400
        )

    with transaction.atomic():
        aviso = ErrorValidacion.objects.select_for_update().get(pk=aviso.pk)
        # Si el aviso ya estaba revisado es una rectificación; la decisión anterior
        # queda en el historial.
        antes = [(aviso, aviso.estado_revision, aviso.comentario_revision)]
        aviso.estado_revision = estado
        aviso.revisado = True
        aviso.revisado_por = request.user
        aviso.fecha_revision = timezone.now()
        aviso.comentario_revision = comentario
        aviso.save(
            update_fields=[
                "estado_revision", "revisado", "revisado_por",
                "fecha_revision", "comentario_revision",
            ]
        )
        _registrar_decisiones(antes, request.user)

    avisos_pendientes = ErrorValidacion.objects.filter(
        carga_id=carga_id, tipo="aviso", revisado=False
    ).count()

    return JsonResponse(
        {
            "ok": True,
            "aviso_id": aviso.id,
            "estado_revision": aviso.estado_revision,
            "estado_revision_display": aviso.get_estado_revision_display(),
            "revisado_por": request.user.username,
            "fecha_revision": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            "avisos_pendientes": avisos_pendientes,
        }
    )


@rol_requerido(*ROLES_OPERATIVOS)
def reporte_observaciones(request, carga_id):
    """Reporte imprimible de las transacciones marcadas como observadas en una carga."""
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario"), pk=carga_id
    )

    observaciones = list(
        carga.errores.filter(tipo="aviso", estado_revision="observado")
        .select_related("revisado_por")
        .order_by("fila")
    )

    # Las observaciones se vinculan a sus registros por número de fila.
    filas = [o.fila for o in observaciones]
    registros_por_fila = {
        r.fila_origen: r
        for r in carga.registros.select_related("cuenta").filter(fila_origen__in=filas)
    }

    filas_reporte = []
    for observacion in observaciones:
        registro = registros_por_fila.get(observacion.fila)
        filas_reporte.append(
            {
                "observacion": observacion,
                "registro": registro,
                # Orden: comprobante y luego grupo contable.
                "orden": (
                    registro.numero_comprobante if registro else "",
                    registro.cuenta.tipo if registro else "",
                ),
            }
        )
    filas_reporte.sort(key=lambda item: item["orden"])

    return render(
        request,
        "registros/reporte_observaciones.html",
        {
            "carga": carga,
            "filas_reporte": filas_reporte,
            # Cobertura de la revisión al emitir el reporte.
            "avisos_total": carga.errores.filter(tipo="aviso").count(),
            "avisos_pendientes": carga.errores.filter(tipo="aviso", revisado=False).count(),
            "fecha_generacion": timezone.localtime(timezone.now()),
            "generado_por": request.user,
        },
    )


@rol_requerido("Administrador", "Auditor")
@require_POST
def carga_confirmar_validacion(request, carga_id):
    """Da por válida una carga con pendientes, una vez revisados todos sus avisos."""
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if carga.estado != "con_observaciones":
        messages.error(request, "Esta carga no tiene observaciones pendientes por confirmar.")
        return redirect("registros:detalle_carga", carga_id=carga.id)

    avisos_pendientes = carga.errores.filter(tipo="aviso", revisado=False).count()
    if avisos_pendientes:
        messages.error(
            request,
            f"Todavía hay {avisos_pendientes} aviso{'s' if avisos_pendientes != 1 else ''} sin marcar "
            "como revisado. Revísalos (individualmente o por tipo) antes de confirmar la carga.",
        )
        return redirect("registros:detalle_carga", carga_id=carga.id)

    valores_anteriores = {"estado": carga.estado}
    carga.estado = "validado"
    carga.revisado_por = request.user
    carga.fecha_revision = timezone.now()
    carga.save()
    registrar_edicion(carga, valores_anteriores, request.user, ["estado"])

    texto = (
        f"Carga #{carga.id} confirmada como válida. Quedó registrado que la revisaste "
        "tú, con la fecha y hora actual."
    )
    if carga.registros_con_error:
        texto += (
            f" Atención: {carga.registros_con_error} fila"
            f"{'s' if carga.registros_con_error != 1 else ''} rechazada"
            f"{'s' if carga.registros_con_error != 1 else ''} al importar quedan "
            "excluidas y siguen visibles en el detalle."
        )
    messages.success(request, texto)
    return redirect("registros:detalle_carga", carga_id=carga.id)


@rol_requerido("Administrador")
def carga_anular(request, carga_id):
    """Anula una carga con motivo obligatorio. La carga no se borra."""
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
                f"Carga #{carga.id} anulada. Ya puedes subir el archivo de nuevo con "
                "los datos correctos desde \"Cargar registros\".",
            )
            return redirect("registros:detalle_carga", carga_id=carga.id)
    else:
        form = AnulacionForm()

    return render(
        request, "registros/carga_anular.html", {"form": form, "carga": carga}
    )


@rol_requerido("Administrador", "Auditor")
def gestion_crear(request):
    """Alta de una nueva gestión (año fiscal) para poder cargar archivos de ese
    periodo.
    """
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


@rol_requerido("Administrador", "Auditor")
def gestion_editar(request, gestion_id):
    """Edita una gestión que todavía no se usó en ninguna carga."""
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    cargas_asociadas = gestion.cargas.count()
    if cargas_asociadas:
        # Una gestión ya usada no se edita: cambiaría el contexto de cargas ya
        # validadas.
        messages.error(
            request,
            f"La gestión {gestion.anio} ya se usó en {cargas_asociadas} carga"
            f"{'s' if cargas_asociadas != 1 else ''} y no se puede modificar. "
            "Si el período era incorrecto, anula la carga y vuelve a subirla con la gestión correcta.",
        )
        return redirect("registros:gestion_crear")
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
                # Las fechas se calculan según la categoría de cierre de la empresa.
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
