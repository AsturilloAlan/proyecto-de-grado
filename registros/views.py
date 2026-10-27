import csv
import json
import math
from decimal import Decimal, InvalidOperation
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from usuarios.decorators import rol_requerido

from .auditoria import registrar_creacion, registrar_edicion
from .dataset import COLUMNAS_DATASET, filas_dataset
from .forms import AnulacionForm, CargaArchivoForm, EmpresaAuditadaForm, GestionForm
from .validadores import entero_positivo
from .models import (
    CargaArchivo,
    CuentaContable,
    EmpresaAuditada,
    ErrorValidacion,
    Gestion,
    HistorialCambio,
    RegistroContable,
)
from .frases import FRASES_ATIPICAS, FRASES_RAPIDAS, contradice_decision
from .procesamiento import (
    asegurar_trabajador,
    iniciar_procesamiento,
    posicion_en_cola,
    revisar_interrumpida,
)
from .services import _monto_bo
from .estados import anotar_avisos_pendientes, filtrar_por_importacion, filtrar_por_revision

PATRON_FILA_RELACIONADA = re.compile(r"fila (\d+)")

CRITERIOS_VALIDACION = {
    "fecha": "Fecha válida, dentro de la gestión y no posterior a hoy",
    "debe": "Importe numérico",
    "haber": "Importe numérico",
    "debe/haber": "Monto positivo en una sola columna, Debe o Haber",
    "cuenta": "Cuenta identificada y registrada en el catálogo",
    "comprobante": "Número de comprobante presente y cuadre con su total",
    "duplicado": "Comprobante, fecha, cuenta, monto y glosa no repetidos",
    "naturaleza_cuenta": "Ingresos en el Haber y gastos en el Debe",
    "archivo": "Archivo legible con columnas fecha, debe y haber",
}

NOMBRES_TIPO_ERROR = {
    "fecha": "Fecha",
    "debe": "Importe en Debe",
    "haber": "Importe en Haber",
    "debe/haber": "Importe en Debe/Haber",
    "cuenta": "Cuenta",
    "comprobante": "Comprobante",
    "archivo": "Archivo",
    "": "Archivo",
}

QUE_HACER_ERROR = {
    "fecha": "Escribir una fecha válida (día/mes/año) dentro de la gestión.",
    "debe": "Escribir el importe del Debe como número, sin letras ni símbolos.",
    "haber": "Escribir el importe del Haber como número, sin letras ni símbolos.",
    "debe/haber": "Dejar un monto positivo solo en Debe o solo en Haber.",
    "cuenta": "Indicar el código de la cuenta en la fila o en el encabezado del bloque.",
    "comprobante": "Indicar el número de comprobante.",
    "archivo": "Revisar el archivo completo y volver a subirlo.",
}

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


def _conteo_avisos(carga_id):
    """Avisos de la carga por estado de revisión, para actualizar los contadores."""
    conteo = dict(
        ErrorValidacion.objects.filter(carga_id=carga_id, tipo="aviso")
        .values_list("estado_revision")
        .annotate(total=Count("id"))
    )
    pendientes = ErrorValidacion.objects.filter(
        carga_id=carga_id, tipo="aviso", revisado=False
    ).count()
    return {
        "avisos_pendientes": pendientes,
        "avisos_observados": conteo.get("observado", 0),
        "avisos_validos": conteo.get("valido", 0),
    }


AVISOS_POR_PAGINA = 25


def _url_siguiente_aviso(carga, aviso_actual, campo_filtro=""):
    """Enlace a la fila del siguiente aviso pendiente (después del actual, o el primero
    si ya no quedan más abajo), para revisar uno tras otro sin volver a la lista.
    """
    if aviso_actual is None:
        return ""
    pendientes = carga.errores.filter(tipo="aviso", revisado=False).order_by("fila", "id")
    if campo_filtro:
        pendientes = pendientes.filter(campo=campo_filtro)
    otros = pendientes.exclude(pk=aviso_actual.pk)
    siguiente = otros.filter(fila__gte=aviso_actual.fila).first() or otros.first()
    if siguiente is None:
        return ""
    posicion = pendientes.filter(
        Q(fila__lt=siguiente.fila) | Q(fila=siguiente.fila, id__lt=siguiente.id)
    ).count()
    parametros = QueryDict(mutable=True)
    parametros["pestana"] = "registros"
    if campo_filtro:
        parametros["campo_aviso"] = campo_filtro
    parametros["estado_aviso"] = "pendientes"
    parametros["pagina_avisos"] = posicion // AVISOS_POR_PAGINA + 1
    parametros["fila"] = siguiente.fila
    relacionada = (
        PATRON_FILA_RELACIONADA.search(siguiente.descripcion)
        if siguiente.campo == "duplicado" else None
    )
    if relacionada:
        parametros["relacionada"] = relacionada.group(1)
    parametros["desde_aviso"] = siguiente.id
    return "?" + parametros.urlencode() + "#fila-buscada"


def _carga_admite_revision(carga):
    """Una carga anulada queda en solo lectura: su revisión ya no cuenta."""
    return carga.estado != "anulada"


# Filtro "Estado" del historial: valor -> (texto, filtro de importación, de revisión).
FILTROS_ESTADO = {
    "pendientes": ("Con avisos por revisar", "", "en_revision"),
    "por_confirmar": ("Revisada, falta confirmar", "", "por_confirmar"),
    "validada": ("Validada", "", "validada"),
    "con_rechazos": ("Con filas rechazadas", "parcial", ""),
    "fallida": ("Fallida", "fallida", ""),
    "anulada": ("Anulada", "", "anulada"),
}


CAMPOS_AUDITABLES_EMPRESA = [
    "nombre",
    "nit",
    "rubro",
    "categoria_cierre",
    "contacto_nombre",
    "contacto_email",
    "contacto_telefono",
    "contador_nombre",
    "contador_email",
    "contador_telefono",
]


@rol_requerido(*ROLES_OPERATIVOS)
def cargar_registros(request):
    if request.method == "POST":
        form = CargaArchivoForm(request.POST, request.FILES)
        if form.is_valid():
            carga = form.save(commit=False)
            carga.usuario = request.user
            carga.save()
            iniciar_procesamiento(carga)
            return redirect("registros:detalle_carga", carga_id=carga.id)
    else:
        form = CargaArchivoForm()

    cargas_qs = anotar_avisos_pendientes(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario")
    ).order_by("-fecha_carga")

    filtro_empresa = request.GET.get("empresa", "")
    filtro_gestion = request.GET.get("gestion", "")
    filtro_estado = request.GET.get("estado", "")
    if filtro_estado not in FILTROS_ESTADO:
        filtro_estado = ""
    _, filtro_importacion, filtro_revision = FILTROS_ESTADO.get(filtro_estado, ("", "", ""))
    filtro_importacion = request.GET.get("importacion", "") or filtro_importacion
    filtro_revision = request.GET.get("revision", "") or filtro_revision
    if entero_positivo(filtro_empresa) is not None:
        cargas_qs = cargas_qs.filter(empresa_id=entero_positivo(filtro_empresa))
    if entero_positivo(filtro_gestion) is not None:
        cargas_qs = cargas_qs.filter(gestion_id=entero_positivo(filtro_gestion))
    cargas_qs = filtrar_por_importacion(cargas_qs, filtro_importacion)
    cargas_qs = filtrar_por_revision(cargas_qs, filtro_revision)

    paginador = Paginator(cargas_qs, 8)
    cargas_anteriores = paginador.get_page(request.GET.get("pagina"))

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

    # Gestiones que coinciden con el cierre de cada empresa (las demás se atenúan).
    gestiones = list(Gestion.objects.all())
    gestiones_por_empresa = {
        str(empresa.id): [
            str(g.id) for g in gestiones
            if (g.fecha_inicio, g.fecha_fin) == tuple(empresa.fechas_gestion_para(g.anio))
        ]
        for empresa in EmpresaAuditada.objects.all()
    }

    return render(
        request,
        "registros/cargar.html",
        {
            "form": form,
            "gestiones_por_empresa": gestiones_por_empresa,
            "cargas_anteriores": cargas_anteriores,
            "querystring": querystring,
            "empresas_con_cargas": empresas_con_cargas,
            "gestiones_con_cargas": gestiones_con_cargas,
            "opciones_estado": [(valor, datos[0]) for valor, datos in FILTROS_ESTADO.items()],
            "filtro_estado": filtro_estado,
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
    revisar_interrumpida(carga)
    if carga.estado == "pendiente":
        return render(request, "registros/carga_procesando.html", {
            "carga": carga,
            "segundos_transcurridos": int((timezone.now() - carga.fecha_carga).total_seconds()),
            "en_cola": carga.inicio_proceso is None,
            "cargas_antes": posicion_en_cola(carga),
        })
    campo_aviso_filtro = request.GET.get("campo_aviso", "").strip()

    errores_qs = carga.errores.filter(tipo="error").order_by("fila")
    campo_error_filtro = request.GET.get("campo_error", "").strip()
    if campo_error_filtro and errores_qs.filter(campo=campo_error_filtro).exists():
        errores_qs = errores_qs.filter(campo=campo_error_filtro)
    else:
        campo_error_filtro = ""
    estado_aviso = request.GET.get("estado_aviso", "pendientes")
    if estado_aviso not in ("pendientes", "observados", "validos"):
        estado_aviso = "pendientes"
    avisos_qs = carga.errores.filter(tipo="aviso").order_by("fila")
    if campo_aviso_filtro:
        avisos_qs = avisos_qs.filter(campo=campo_aviso_filtro)
    avisos_qs = avisos_qs.filter(
        estado_revision={"pendientes": "pendiente", "observados": "observado", "validos": "valido"}[estado_aviso]
    )
    errores_paginador = Paginator(errores_qs, 25)
    avisos_paginador = Paginator(avisos_qs, AVISOS_POR_PAGINA)
    errores = errores_paginador.get_page(request.GET.get("pagina_errores"))
    avisos = avisos_paginador.get_page(request.GET.get("pagina_avisos"))
    # Enlace de cada aviso a su fila (y, en duplicados, a la fila con la que coincide).
    avisos.object_list = list(avisos.object_list)
    registros_de_avisos = {
        r.fila_origen: r
        for r in carga.registros.filter(fila_origen__in=[a.fila for a in avisos.object_list])
    }
    for aviso in avisos.object_list:
        aviso.criterio = CRITERIOS_VALIDACION.get(aviso.campo, "")
        registro = registros_de_avisos.get(aviso.fila)
        aviso.comprobante = registro.numero_comprobante if registro else ""
        aviso.descripcion_corta = aviso.descripcion
        coincidencia = PATRON_FILA_RELACIONADA.search(aviso.descripcion) if aviso.campo == "duplicado" else None
        if coincidencia and registro:
            monto = registro.debe or registro.haber
            columna = "Debe" if registro.debe else "Haber"
            aviso.descripcion_corta = (
                f"Posible duplicado de la fila {coincidencia.group(1)}: misma fecha "
                f"({registro.fecha:%d/%m/%Y}), cuenta, glosa y monto ({columna} {_monto_bo(monto)})"
            )
        aviso.fila_relacionada = int(coincidencia.group(1)) if coincidencia else None
    errores.object_list = list(errores.object_list)
    for error in errores.object_list:
        error.criterio = CRITERIOS_VALIDACION.get(error.campo, "")
        error.que_hacer = QUE_HACER_ERROR.get(error.campo or "archivo", "")
        error.nombre_tipo = NOMBRES_TIPO_ERROR.get(error.campo, error.campo or "Archivo")
    parametros_avisos = QueryDict(mutable=True)
    if campo_aviso_filtro:
        parametros_avisos["campo_aviso"] = campo_aviso_filtro
    parametros_avisos["estado_aviso"] = estado_aviso
    # Filtros que conserva la paginación de avisos (sin el número de página).
    paginacion_avisos_qs = "pestana=avisos&" + parametros_avisos.urlencode()
    parametros_avisos["pagina_avisos"] = avisos.number
    avisos_querystring = parametros_avisos.urlencode()
    parametros_errores = QueryDict(mutable=True)
    parametros_errores["pestana"] = "errores"
    if campo_error_filtro:
        parametros_errores["campo_error"] = campo_error_filtro
    paginacion_errores_qs = parametros_errores.urlencode()

    resumen_errores = list(
        carga.errores.filter(tipo="error")
        .values("campo")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for item in resumen_errores:
        item["nombre"] = NOMBRES_TIPO_ERROR.get(item["campo"], item["campo"] or "Archivo")
        item["criterio"] = CRITERIOS_VALIDACION.get(item["campo"] or "archivo", "")
    # En avisos solo cuentan los pendientes (son los que marca el botón por tipo).
    resumen_avisos = list(
        carga.errores.filter(tipo="aviso", revisado=False)
        .values("campo")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    for item in resumen_avisos:
        item["criterio"] = CRITERIOS_VALIDACION.get(item["campo"], "")

    # Filtros de registros. Se aplican antes de calcular el salto a una fila.
    cuenta_filtro = request.GET.get("cuenta_codigo", "").strip()
    comprobante_filtro = request.GET.get("numero_comprobante", "").strip()

    registros_qs = carga.registros.select_related("cuenta").order_by("fila_origen")
    if cuenta_filtro:
        registros_qs = registros_qs.filter(cuenta__codigo__startswith=cuenta_filtro)
    if comprobante_filtro:
        registros_qs = registros_qs.filter(numero_comprobante__icontains=comprobante_filtro)
    columna_monto = request.GET.get("columna_monto", "cualquiera")
    if columna_monto not in ("debe", "haber", "cualquiera"):
        columna_monto = "cualquiera"
    monto_filtro = request.GET.get("monto_min", "").strip()
    try:
        monto_min = Decimal(monto_filtro.replace(",", ".")) if monto_filtro and len(monto_filtro) <= 32 else None
    except InvalidOperation:
        monto_min = None
    if monto_min is None or not monto_min.is_finite():
        monto_filtro, monto_min = "", None
    elif monto_min < 0 or monto_min > Decimal("9999999999999999.99"):
        monto_filtro, monto_min = "", None
    if monto_min is not None:
        if columna_monto == "debe":
            registros_qs = registros_qs.filter(debe__gte=monto_min)
        elif columna_monto == "haber":
            registros_qs = registros_qs.filter(haber__gte=monto_min)
        else:
            registros_qs = registros_qs.filter(Q(debe__gte=monto_min) | Q(haber__gte=monto_min))
    hay_filtro_registros = bool(cuenta_filtro or comprobante_filtro or monto_filtro)
    paginador = Paginator(registros_qs, 50)

    numero_pagina = request.GET.get("pagina")
    fila_buscada = None
    fila_encontrada = False
    fila_param = request.GET.get("fila", "").strip()
    if entero_positivo(fila_param) is not None:
        fila_buscada = entero_positivo(fila_param)
        posicion = registros_qs.filter(fila_origen__lte=fila_buscada).count()
        numero_pagina = math.ceil(posicion / paginador.per_page) or 1
        fila_encontrada = registros_qs.filter(fila_origen=fila_buscada).exists()

    motivo_fila_no_encontrada = ""
    if fila_buscada is not None and not fila_encontrada:
        if carga.registros.filter(fila_origen=fila_buscada).exists():
            motivo_fila_no_encontrada = "filtrada"
        elif carga.errores.filter(tipo="error", fila=fila_buscada).exists():
            motivo_fila_no_encontrada = "rechazada"
        else:
            motivo_fila_no_encontrada = "inexistente"

    registros_pagina = paginador.get_page(numero_pagina)
    fila_relacionada = request.GET.get("relacionada", "")
    fila_relacionada = entero_positivo(fila_relacionada)
    desde_aviso = request.GET.get("desde_aviso", "")
    desde_aviso = entero_positivo(desde_aviso)
    aviso_origen = (
        carga.errores.filter(pk=desde_aviso, tipo="aviso").first() if desde_aviso else None
    )
    siguiente_aviso_url = _url_siguiente_aviso(carga, aviso_origen, campo_aviso_filtro)

    # Filtros para la paginación, sin "fila" (el salto es de una sola vez).
    parametros_registros = request.GET.copy()
    parametros_registros.pop("pagina", None)
    for clave in ("fila", "relacionada", "desde_aviso"):
        parametros_registros.pop(clave, None)
    parametros_registros["pestana"] = "registros"
    registros_querystring = parametros_registros.urlencode()

    conteo_avisos = _conteo_avisos(carga.id)
    avisos_pendientes = conteo_avisos["avisos_pendientes"]
    hay_avisos = carga.errores.filter(tipo="aviso").exists()

    # Pestaña activa: la de la URL, la que indican los parámetros, o Avisos si hay pendientes.
    pestana = request.GET.get("pestana", "")
    if pestana not in ("avisos", "errores", "registros", "atipicas"):
        if fila_buscada is not None or hay_filtro_registros or request.GET.get("pagina"):
            pestana = "registros"
        elif campo_error_filtro or request.GET.get("pagina_errores"):
            pestana = "errores"
        elif hay_avisos and (
            avisos_pendientes or campo_aviso_filtro or request.GET.get("pagina_avisos")
            or request.GET.get("estado_aviso")
        ):
            pestana = "avisos"
        elif carga.estado == "validado" and _tiene_atipicas_pendientes(carga):
            # Avisos al día: el trabajo que sigue está en Atípicas.
            pestana = "atipicas"
        else:
            pestana = "registros"
    if pestana == "avisos" and not hay_avisos:
        pestana = "registros"

    analisis = _analisis_de(carga)
    atipicas_pagina = None
    metricas_validacion = None
    tipos_atipica = []
    motivo_filtro = request.GET.get("motivo", "")
    validacion_filtro = request.GET.get("validacion", "")
    if validacion_filtro not in ("pendiente", "relevante", "seguimiento", "no_relevante"):
        # Igual que en Avisos: se abre en Pendientes y cada revisada pasa a la pestaña de su estado.
        validacion_filtro = "pendiente"
    if analisis is not None and analisis.estado == "completado" and pestana == "atipicas":
        atipicas_qs = (analisis.resultados.filter(atipica=True)
                       .select_related("registro", "registro__cuenta", "revisado_por")
                       .order_by("-nivel", "registro__fila_origen"))
        if validacion_filtro:
            atipicas_qs = atipicas_qs.filter(revision=validacion_filtro)
        tipos_atipica = _tipos_de_atipica(analisis)
        if motivo_filtro in {t["clave"] for t in tipos_atipica}:
            atipicas_qs = atipicas_qs.filter(motivo_principal=motivo_filtro)
        else:
            motivo_filtro = ""
        atipicas_pagina = Paginator(atipicas_qs, 20).get_page(request.GET.get("pagina_atipicas"))
        metricas_validacion = analisis.metricas_validacion()

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
            "total_errores": sum(item["total"] for item in resumen_errores),
            "resumen_avisos": resumen_avisos,
            "avisos_pendientes": avisos_pendientes,
            "campo_aviso_filtro": campo_aviso_filtro,
            "campo_error_filtro": campo_error_filtro,
            "filtro_ya_revisado": filtro_ya_revisado,
            "registros_pagina": registros_pagina,
            "fila_buscada": fila_buscada,
            "fila_encontrada": fila_encontrada,
            "fila_relacionada": fila_relacionada,
            "desde_aviso": desde_aviso,
            "aviso_origen": aviso_origen,
            "frases_rapidas": FRASES_RAPIDAS,
            "frases_atipicas": FRASES_ATIPICAS,
            "siguiente_aviso_url": siguiente_aviso_url,
            "avisos_querystring": avisos_querystring,
            "paginacion_avisos_qs": paginacion_avisos_qs,
            "paginacion_errores_qs": paginacion_errores_qs,
            "motivo_fila_no_encontrada": motivo_fila_no_encontrada,
            "carga_admite_revision": _carga_admite_revision(carga),
            "puede_anular": _puede_anular(request.user, carga),
            "cuenta_filtro": cuenta_filtro,
            "comprobante_filtro": comprobante_filtro,
            "monto_filtro": monto_filtro,
            "columna_monto": columna_monto,
            "hay_filtro_registros": hay_filtro_registros,
            "pestana": pestana,
            "analisis": analisis,
            "atipicas_pagina": atipicas_pagina,
            "metricas_validacion": metricas_validacion,
            "volver_atipicas": _volver_atipicas(request, carga),
            "atipicas_pendientes": (
                analisis.resultados.filter(atipica=True, revision="pendiente").count()
                if analisis is not None and analisis.estado == "completado" else None
            ),
            # El paso "Revisar atípicas" se cumple con las 20 más atípicas revisadas.
            "primeras_atipicas": (
                analisis.metricas_validacion()
                if analisis is not None and analisis.estado == "completado" else None
            ),
            "validacion_filtro": validacion_filtro,
            "atipicas_querystring": "pestana=atipicas"
            + (f"&validacion={validacion_filtro}" if validacion_filtro else "")
            + (f"&motivo={motivo_filtro}" if motivo_filtro else ""),
            "tipos_atipica": tipos_atipica,
            "motivo_filtro": motivo_filtro,
            "puede_analizar": _puede_analizar(carga),
            "estado_aviso": estado_aviso,
            "avisos_observados": conteo_avisos["avisos_observados"],
            "avisos_validos": conteo_avisos["avisos_validos"],
            "hay_avisos": hay_avisos,
            "registros_querystring": registros_querystring,
        },
    )


def _analisis_de(carga):
    from analisis.models import AnalisisCarga
    from analisis.servicio import revisar_interrumpido

    analisis = AnalisisCarga.objects.filter(carga=carga).first()
    if analisis is None:
        return None
    revisar_interrumpido(analisis)
    if analisis.estado == "en_cola" and getattr(settings, "PROCESAR_EN_SEGUNDO_PLANO", True):
        # Tras un reinicio del servidor, el trabajador retoma la cola.
        asegurar_trabajador()
    return analisis


def _puede_analizar(carga):
    from analisis.servicio import se_puede_analizar

    return se_puede_analizar(carga)


@rol_requerido(*ROLES_OPERATIVOS)
@require_POST
def carga_analizar(request, carga_id):
    """Pone la carga en la cola del modelo (primer análisis o tras reentrenar)."""
    from analisis import servicio
    from analisis.models import AnalisisCarga

    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    analisis = AnalisisCarga.objects.filter(carga=carga).first()
    if analisis is not None and analisis.en_curso:
        messages.info(request, "El análisis de esta carga ya está en curso.")
    elif not servicio.se_puede_analizar(carga):
        messages.error(request, "Esta carga no tiene registros válidos para analizar.")
    elif getattr(settings, "PROCESAR_EN_SEGUNDO_PLANO", True):
        servicio.encolar(carga, request.user)
        transaction.on_commit(asegurar_trabajador)
        messages.success(request, "El análisis con el modelo quedó en cola.")
    else:
        servicio.analizar_ahora(carga, request.user)
        messages.success(request, "Análisis con el modelo terminado.")
    return redirect(f"{reverse('registros:detalle_carga', args=[carga.id])}?pestana=atipicas")


def _tipos_de_atipica(analisis):
    """Atípicas agrupadas por su motivo principal: [{clave, etiqueta, total}]."""
    from analisis.explicacion import ETIQUETAS

    conteo = (analisis.resultados.filter(atipica=True).exclude(motivo_principal="")
              .values("motivo_principal").annotate(total=Count("id")).order_by("-total"))
    return [{"clave": c["motivo_principal"],
             "etiqueta": ETIQUETAS.get(c["motivo_principal"], c["motivo_principal"]),
             "total": c["total"]} for c in conteo]


def _tiene_atipicas_pendientes(carga):
    from analisis.models import ResultadoTransaccion

    return ResultadoTransaccion.objects.filter(
        analisis__carga=carga, analisis__estado="completado", atipica=True, revision="pendiente").exists()


def _volver_atipicas(request, carga):
    """Enlace de regreso: a la lista de atípicas de esta misma carga o a un reporte
    (desde "Ver en registros" de los reportes). Nunca a otro sitio."""
    volver = request.GET.get("volver", "")
    if "\n" in volver or "\\" in volver or volver.startswith("//"):
        return ""
    base = reverse("registros:detalle_carga", args=[carga.id]) + "?pestana=atipicas"
    if volver.startswith(base) or volver.startswith("/reportes/"):
        return volver
    return ""


def _es_fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


@rol_requerido(*ROLES_OPERATIVOS)
def atipica_asiento(request, carga_id, resultado_id):
    """Asiento completo de una transacción atípica, para verlo sin salir de la lista."""
    from analisis.models import ResultadoTransaccion

    resultado = get_object_or_404(
        ResultadoTransaccion.objects.select_related("registro", "analisis"),
        pk=resultado_id, analisis__carga_id=carga_id, atipica=True,
    )
    registro = resultado.registro
    lineas = RegistroContable.objects.filter(carga_id=carga_id).select_related("cuenta")
    if registro.numero_comprobante:
        lineas = lineas.filter(numero_comprobante=registro.numero_comprobante, fecha=registro.fecha)
    else:
        lineas = lineas.filter(fecha=registro.fecha, glosa=registro.glosa)
    maximo = 60
    lineas = list(lineas.order_by("fila_origen", "id")[:maximo + 1])
    recortado = len(lineas) > maximo
    lineas = lineas[:maximo]
    return render(request, "registros/_asiento_atipica.html", {
        "lineas": lineas,
        "recortado": recortado,
        "registro_id": registro.id,
        "comprobante": registro.numero_comprobante,
        "fecha": registro.fecha,
        "total_debe": sum(l.debe for l in lineas),
        "total_haber": sum(l.haber for l in lineas),
    })


@rol_requerido(*ROLES_OPERATIVOS)
@require_POST
def atipica_revisar(request, carga_id, resultado_id):
    """Validación preliminar (actividad 13): el auditor indica si una transacción que el
    modelo señaló es relevante para la auditoría. La decisión queda en el historial.
    """
    from analisis.models import ResultadoTransaccion

    resultado = get_object_or_404(
        ResultadoTransaccion.objects.select_related("analisis__carga", "registro"),
        pk=resultado_id, analisis__carga_id=carga_id, atipica=True,
    )
    carga = resultado.analisis.carga
    destino = request.POST.get("siguiente", "")
    base = reverse("registros:detalle_carga", args=[carga.id])
    if not destino.startswith(base + "?"):
        destino = f"{base}?pestana=atipicas"
    destino = f"{destino}#atipica-{resultado.id}"

    if not _carga_admite_revision(carga):
        if _es_fetch(request):
            return JsonResponse({"ok": False, "error": "La carga está anulada; su revisión es de solo lectura."}, status=403)
        messages.error(request, "La carga está anulada; su revisión es de solo lectura.")
        return redirect(destino)
    decision = request.POST.get("decision", "")
    if decision not in ("relevante", "seguimiento", "no_relevante", "pendiente"):
        if _es_fetch(request):
            return JsonResponse({"ok": False, "error": "Decisión no válida."}, status=400)
        messages.error(request, "Decisión no válida.")
        return redirect(destino)
    comentario = request.POST.get("comentario", "").strip()
    if decision != "pendiente" and (not 5 <= len(comentario) <= 300 or not any(c.isalpha() for c in comentario)):
        error = "Registrar una justificación de 5 a 300 caracteres que explique la decisión."
        if _es_fetch(request):
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect(destino)

    with transaction.atomic():
        resultado = ResultadoTransaccion.objects.select_for_update().select_related(
            "registro", "analisis").get(pk=resultado.pk)
        anteriores = {"revision": resultado.revision,
                      "comentario_revision": resultado.comentario_revision}
        resultado.revision = decision
        if decision == "pendiente":
            resultado.comentario_revision = ""
            resultado.revisado_por = None
            resultado.fecha_revision = None
        else:
            resultado.comentario_revision = comentario
            resultado.revisado_por = request.user
            resultado.fecha_revision = timezone.now()
        resultado.save()
        registrar_edicion(resultado, anteriores, request.user,
                          ["revision", "comentario_revision"])
    if _es_fetch(request):
        from django.template.loader import render_to_string

        siguiente = request.POST.get("siguiente", "")
        contexto = {"resultado": resultado, "carga": carga, "siguiente": siguiente}
        return JsonResponse({
            "ok": True,
            "celda": render_to_string("registros/_validacion_atipica.html", contexto, request=request),
            "metricas": render_to_string(
                "registros/_metricas_validacion.html",
                {"metricas_validacion": resultado.analisis.metricas_validacion()},
                request=request,
            ),
        })
    return redirect(destino)


@rol_requerido(*ROLES_OPERATIVOS)
def carga_estado(request, carga_id):
    """Estado de una carga, para que la página "En proceso" sepa cuándo terminó."""
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    revisar_interrumpida(carga)
    return JsonResponse({
        "en_proceso": carga.estado == "pendiente",
        "en_cola": carga.estado == "pendiente" and carga.inicio_proceso is None,
        "cargas_antes": posicion_en_cola(carga),
        "segundos": int((timezone.now() - carga.fecha_carga).total_seconds()),
    })


@rol_requerido(*ROLES_OPERATIVOS)
def exportar_dataset_csv(request, carga_id):
    """Exporta a CSV el dataset de una carga (registros guardados y avisos por fila)."""
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if carga.estado == "anulada":
        messages.error(request, "La carga está anulada; su información no se exporta.")
        return redirect("registros:detalle_carga", carga_id=carga.id)

    respuesta = HttpResponse(content_type="text/csv")
    respuesta["Content-Disposition"] = f'attachment; filename="dataset_carga_{carga.id}.csv"'
    escritor = csv.writer(respuesta)
    escritor.writerow(COLUMNAS_DATASET)
    escritor.writerows(filas_dataset(carga, celda=_celda_csv_segura))
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
    if contradice_decision("valido", comentario):
        return JsonResponse({
            "ok": False,
            "error": "La justificación corresponde a una observación; en lote solo se marcan válidos.",
        }, status=400)

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

    return JsonResponse(
        {
            "ok": True,
            "actualizados": actualizados,
            "revisado_por": request.user.username,
            "fecha_revision": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            **_conteo_avisos(carga_id),
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

    if estado not in ("valido", "observado", "pendiente"):
        return JsonResponse(
            {"ok": False, "error": "Estado no válido."},
            status=400,
        )
    if not comentario:
        return JsonResponse(
            {"ok": False, "error": "La justificación es obligatoria."}, status=400
        )
    if contradice_decision(estado, comentario):
        return JsonResponse({
            "ok": False,
            "error": "La justificación corresponde a la decisión contraria; revisar la decisión o el texto.",
        }, status=400)

    with transaction.atomic():
        aviso = ErrorValidacion.objects.select_for_update().get(pk=aviso.pk)
        if estado == "pendiente" and not aviso.revisado:
            return JsonResponse({"ok": False, "error": "El aviso ya está pendiente."}, status=400)
        # Si ya estaba revisado es una rectificación; la decisión anterior queda en el historial.
        estado_anterior, comentario_anterior = aviso.estado_revision, aviso.comentario_revision
        aviso.estado_revision = estado
        if estado == "pendiente":
            aviso.revisado = False
            aviso.revisado_por = None
            aviso.fecha_revision = None
            aviso.comentario_revision = ""
        else:
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
        HistorialCambio.objects.create(
            modelo="ErrorValidacion",
            objeto_id=aviso.pk,
            objeto_descripcion=str(aviso)[:200],
            accion="edicion",
            campo="estado_revision",
            valor_anterior=f"{estado_anterior} | {comentario_anterior}".strip(" |"),
            valor_nuevo=f"{estado} | {comentario}",
            usuario=request.user,
        )
        # Una carga ya validada que vuelve a tener pendientes deja de estar validada.
        carga = aviso.carga
        if estado == "pendiente" and carga.estado == "validado":
            valores_anteriores = {"estado": carga.estado}
            carga.estado = "con_observaciones"
            carga.save(update_fields=["estado"])
            registrar_edicion(carga, valores_anteriores, request.user, ["estado"])

    return JsonResponse(
        {
            "ok": True,
            "aviso_id": aviso.id,
            "campo": aviso.campo,
            "estado_revision": aviso.estado_revision,
            "estado_revision_display": aviso.get_estado_revision_display(),
            "revisado_por": request.user.username,
            "fecha_revision": timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M"),
            **_conteo_avisos(carga_id),
        }
    )


@rol_requerido(*ROLES_OPERATIVOS)
def reporte_observaciones(request, carga_id):
    """Reporte imprimible de las transacciones marcadas como observadas en una carga."""
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario"), pk=carga_id
    )

    # Mismo contenido y exportación que el reporte consolidado por gestión.
    from reportes.views import entregar_observaciones

    return entregar_observaciones(request, [carga], carga.empresa, carga.gestion, carga=carga)


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
            "como revisado; deben revisarse antes de confirmar la carga.",
        )
        return redirect("registros:detalle_carga", carga_id=carga.id)

    valores_anteriores = {"estado": carga.estado}
    carga.estado = "validado"
    carga.revisado_por = request.user
    carga.fecha_revision = timezone.now()
    carga.save()
    registrar_edicion(carga, valores_anteriores, request.user, ["estado"])

    texto = (
        f"Carga #{carga.id} confirmada como válida. Queda registrado el usuario y la fecha."
        ""
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


def _puede_anular(usuario, carga):
    """El Administrador anula cualquier carga; el Auditor, solo las que subió él."""
    if usuario.is_superuser or usuario.groups.filter(name="Administrador").exists():
        return True
    return carga.usuario_id == usuario.id and usuario.groups.filter(name="Auditor").exists()


@rol_requerido(*ROLES_OPERATIVOS)
def carga_anular(request, carga_id):
    """Anula una carga con motivo obligatorio. La carga no se borra."""
    carga = get_object_or_404(CargaArchivo, pk=carga_id)
    if not _puede_anular(request.user, carga):
        messages.error(request, "Solo quien subió la carga o un Administrador puede anularla.")
        return redirect("registros:detalle_carga", carga_id=carga.id)
    if carga.estado == "anulada":
        messages.error(request, "Esta carga ya está anulada.")
        return redirect("registros:detalle_carga", carga_id=carga.id)
    if carga.estado == "pendiente":
        messages.error(request, "La carga aún está en proceso; se puede anular cuando termine.")
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
                f"Carga #{carga.id} anulada. El archivo se puede volver a subir "
                "desde \"Cargar registros\".",
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
        # Una gestión ya usada no se edita: cambiaría el contexto de cargas validadas.
        messages.error(
            request,
            f"La gestión {gestion.anio} ya se usó en {cargas_asociadas} carga"
            f"{'s' if cargas_asociadas != 1 else ''} y no se puede modificar. "
            "Si el período es incorrecto, corresponde anular la carga y volver a subirla.",
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


@rol_requerido(*ROLES_OPERATIVOS)
def empresas_lista(request):
    """Listado de empresas clientes de ST&S (RF-09, Administrador y Auditor)."""
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


@rol_requerido("Administrador")  # RF-09: los datos del cliente los gestiona solo el Administrador.
def empresa_crear(request):
    if request.method == "POST":
        form = EmpresaAuditadaForm(request.POST)
        if form.is_valid():
            empresa = form.save()
            registrar_creacion(empresa, request.user)

            anio = form.cleaned_data.get("anio_gestion_inicial")
            if anio:
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


@rol_requerido(*ROLES_OPERATIVOS)
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


NATURALEZA_DEUDORA = ("activo", "gasto")
MOVIMIENTOS_POR_PAGINA = 100


@rol_requerido(*ROLES_OPERATIVOS)
def libro_mayor(request, carga_id):
    from django.db.models import Sum

    from analisis.models import ResultadoTransaccion
    from reportes.servicio import rango_dentro_de_gestion

    carga = get_object_or_404(CargaArchivo.objects.select_related("empresa", "gestion"), pk=carga_id)
    cuentas = list(
        CuentaContable.objects.filter(registros__carga=carga)
        .annotate(movimientos=Count("registros")).order_by("codigo"))
    if not cuentas:
        messages.info(request, "La carga no tiene registros guardados.")
        return redirect("registros:detalle_carga", carga.id)

    fila_buscada = entero_positivo(request.GET.get("fila", ""))
    cuenta = None
    cuenta_id = entero_positivo(request.GET.get("cuenta", ""))
    if cuenta_id:
        cuenta = next((c for c in cuentas if c.id == cuenta_id), None)
    if cuenta is None and fila_buscada:
        registro = carga.registros.filter(fila_origen=fila_buscada).select_related("cuenta").first()
        cuenta = registro.cuenta if registro else None
    cuenta = cuenta or cuentas[0]

    desde, hasta = rango_dentro_de_gestion(carga.gestion, request.GET.get("desde", ""), request.GET.get("hasta", ""))
    deudora = cuenta.tipo in NATURALEZA_DEUDORA
    base = carga.registros.filter(cuenta=cuenta)
    totales = base.aggregate(debe=Sum("debe"), haber=Sum("haber"))
    # Se conserva el saldo del libro: no se recalcula sin conocer el saldo inicial.
    hay_saldo_libro = base.filter(saldo__isnull=False).exists()
    movimientos = base
    if desde:
        movimientos = movimientos.filter(fecha__gte=desde)
    if hasta:
        movimientos = movimientos.filter(fecha__lte=hasta)
    movimientos = movimientos.order_by("fecha", "fila_origen", "id")

    paginador = Paginator(movimientos, MOVIMIENTOS_POR_PAGINA)
    numero_pagina = request.GET.get("pagina")
    if fila_buscada and not request.GET.get("pagina"):
        posicion = next((i for i, m in enumerate(paginador.object_list) if m.fila_origen == fila_buscada), None)
        if posicion is not None:
            numero_pagina = posicion // MOVIMIENTOS_POR_PAGINA + 1
    pagina = paginador.get_page(numero_pagina)

    atipicas = {r.registro_id: r for r in ResultadoTransaccion.objects.filter(
        analisis__carga=carga, atipica=True, registro_id__in=[m.id for m in pagina])}
    avisos = set(carga.errores.filter(tipo="aviso", fila__in=[m.fila_origen for m in pagina])
                 .values_list("fila", flat=True))
    for m in pagina:
        m.atipica = atipicas.get(m.id)
    total_atipicas = ResultadoTransaccion.objects.filter(
        analisis__carga=carga, atipica=True, registro__cuenta=cuenta).count()
    consulta = QueryDict(mutable=True)
    consulta["cuenta"] = cuenta.id
    for clave, valor in (("desde", desde), ("hasta", hasta), ("volver", _volver_atipicas(request, carga))):
        if valor:
            consulta[clave] = valor

    return render(request, "registros/libro_mayor.html", {
        "carga": carga, "cuentas": cuentas, "cuenta": cuenta, "deudora": deudora,
        "hay_saldo_libro": hay_saldo_libro,
        "pagina": pagina, "atipicas": atipicas, "avisos": avisos, "total_atipicas": total_atipicas,
        "totales": totales, "fila_buscada": fila_buscada, "desde": desde, "hasta": hasta,
        "querystring": consulta.urlencode(),
        "volver": _volver_atipicas(request, carga),
    })
