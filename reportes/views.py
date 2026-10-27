"""Vistas de reportes (actividad 15).

Organización pensada para el trabajo real de la firma: cada empresa auditada tiene sus
gestiones (2022, 2023, 2024...) y cada gestión, sus cargas. Los reportes se emiten por
gestión consolidando las cargas válidas, o para una sola carga.
"""
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from analisis.models import AnalisisCarga
from registros.models import CargaArchivo, EmpresaAuditada, Gestion
from registros.validadores import entero_positivo
from usuarios.decorators import rol_requerido

from . import exportar, servicio
from .models import ReporteGenerado

ROLES_OPERATIVOS = ("Administrador", "Auditor")


@rol_requerido(*ROLES_OPERATIVOS)
def indice(request):
    """Expediente por empresa: sus gestiones, comparadas entre sí, y las cargas de cada una."""
    empresas = EmpresaAuditada.objects.filter(cargas__isnull=False).distinct().order_by("nombre")
    empresa_id = request.GET.get("empresa", "")
    elegida = empresas.filter(pk=entero_positivo(empresa_id)).first() if empresa_id else empresas.first()
    seleccion = [elegida] if elegida else []
    empresa_id = str(elegida.pk) if elegida else empresa_id
    gestion_id = entero_positivo(request.GET.get("gestion", ""))
    opciones_gestion = []

    expedientes = []
    for empresa in seleccion:
        gestiones = []
        filas = list(reversed(servicio.comparativo_empresa(empresa)))
        opciones_gestion = [fila["gestion"] for fila in filas]
        if gestion_id not in {g.pk for g in opciones_gestion}:
            gestion_id = opciones_gestion[0].pk if opciones_gestion else None
        for fila in filas:
            if fila["gestion"].pk != gestion_id:
                continue
            grupo = servicio.cargas_de_gestion(empresa, fila["gestion"])
            analisis = {a.carga_id: a for a in AnalisisCarga.objects.filter(carga__in=grupo["incluidas"])}
            fila.update({
                "grupo": grupo,
                "detalle_cargas": [{"carga": c, "analisis": analisis.get(c.id)} for c in grupo["incluidas"]],
                "hay_analisis": any(a.estado == "completado" for a in analisis.values()),
            })
            gestiones.append(fila)
        expedientes.append({"empresa": empresa, "gestiones": list(reversed(gestiones))})

    return render(request, "reportes/indice.html", {
        "expedientes": expedientes,
        "empresas": empresas,
        "empresa_id": empresa_id,
        "opciones_gestion": opciones_gestion,
        "gestion_id": gestion_id,
        "emitidos": ReporteGenerado.objects.select_related(
            "empresa", "gestion", "carga", "usuario")[:20],
    })


def _filtros(request):
    return {
        "nivel": request.GET.get("nivel", ""),
        "motivo": request.GET.get("motivo", ""),
        "validacion": request.GET.get("validacion", ""),
        "tributarias": request.GET.get("tributarias", ""),
    }


FORMATOS = {
    "pdf": ("application/pdf", "pdf"),
    "excel": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}


def _formato(request):
    formato = request.GET.get("formato", "")
    return formato if formato in FORMATOS else "pantalla"


def _origen(request, empresa, gestion, carga=None):
    """De dónde vino el usuario, para ofrecerle volver ahí con un clic (parámetro ?desde=).

    - desde=carga-<id>: venía de una carga (pasos, pestaña Atípicas o Avisos).
    - desde=graficos: venía de los gráficos de la gestión.
    - sin parámetro: venía de Reportes (o entró directo).
    """
    desde = request.GET.get("desde", "")
    if desde.startswith("carga-"):
        carga_id = entero_positivo(desde[6:])
        origen = CargaArchivo.objects.filter(pk=carga_id, empresa=empresa, gestion=gestion).first() if carga_id else None
        if origen:
            return {"tipo": "carga", "url": reverse("registros:detalle_carga", args=[origen.id]),
                    "texto": "Volver a la carga", "miga": f"{empresa} · {gestion.anio}"}
    if desde == "graficos":
        return {"tipo": "graficos", "url": reverse("reportes:panorama", args=[empresa.id, gestion.id]),
                "texto": "Volver al análisis", "miga": "Análisis"}
    if carga is not None:
        return {"tipo": "carga", "url": reverse("registros:detalle_carga", args=[carga.id]),
                "texto": "Volver a la carga", "miga": f"Carga N.º {carga.id}"}
    return {"tipo": "reportes", "url": f"{reverse('reportes:indice')}?empresa={empresa.id}&gestion={gestion.id}",
            "texto": "Volver a Reportes", "miga": f"{empresa} · {gestion.anio}"}


def _que_procede(cargas):
    """Siguiente paso concreto para terminar la revisión: la primera carga con avisos o
    atípicas pendientes. Sirve para que Hallazgos no sea un callejón sin salida."""
    from analisis.models import ResultadoTransaccion
    from registros.models import ErrorValidacion

    pasos = []
    for carga in cargas:
        avisos = ErrorValidacion.objects.filter(carga=carga, tipo="aviso", revisado=False).count()
        if avisos:
            pasos.append({"texto": f"Revisar {avisos} aviso{'s' if avisos != 1 else ''} pendiente{'s' if avisos != 1 else ''}",
                          "url": reverse("registros:detalle_carga", args=[carga.id]) + "?pestana=avisos"})
            break
    for carga in cargas:
        atipicas = ResultadoTransaccion.objects.filter(
            analisis__carga=carga, atipica=True, revision__in=("pendiente", "seguimiento")).count()
        if atipicas:
            pasos.append({"texto": f"Revisar {atipicas} atípica{'s' if atipicas != 1 else ''} pendiente{'s' if atipicas != 1 else ''} o en seguimiento",
                          "url": reverse("registros:detalle_carga", args=[carga.id]) + "?pestana=atipicas&validacion=pendiente"})
            break
    return pasos


def _entregar(request, tipo, datos, plantilla, documento, cifras, filtros_texto=""):
    """Registra la emisión y entrega el reporte en pantalla, PDF o Excel."""
    formato = _formato(request)
    # Pasar de página en pantalla no es una emisión nueva: se reutiliza la última.
    anterior = None
    if formato == "pantalla" and request.GET.get("pagina"):
        anterior = ReporteGenerado.objects.filter(
            tipo=tipo, usuario=request.user, empresa=datos["empresa"], gestion=datos["gestion"],
            carga=datos.get("carga"), formato="pantalla").order_by("-id").first()
    datos["emision"] = anterior or servicio.registrar_emision(
        tipo, request.user, carga=datos.get("carga"), empresa=datos["empresa"],
        gestion=datos["gestion"], filtros_texto=filtros_texto, cifras=cifras, formato=formato)
    datos["fecha_generacion"] = timezone.localtime(timezone.now())
    datos["generado_por"] = request.user
    datos["emisor_es_socio"] = request.user.groups.filter(name="Administrador").exists()
    datos["veces_emitido"] = ReporteGenerado.objects.filter(
        tipo=tipo, empresa=datos["empresa"], gestion=datos["gestion"], carga=datos.get("carga")).count()
    if formato == "pantalla":
        datos["origen"] = _origen(request, datos["empresa"], datos["gestion"], datos.get("carga"))
        return render(request, plantilla, datos)
    doc = documento(datos)
    doc.emitido_por = request.user.get_full_name() or request.user.username
    doc.emisor_es_socio = datos["emisor_es_socio"]
    contenido = exportar.a_pdf(doc) if formato == "pdf" else exportar.a_excel(doc)
    tipo_mime, extension = FORMATOS[formato]
    respuesta = HttpResponse(contenido, content_type=tipo_mime)
    # en_linea=1 (solo PDF): se abre en el navegador para imprimir, en vez de descargarse.
    modo = "inline" if formato == "pdf" and request.GET.get("en_linea") == "1" else "attachment"
    respuesta["Content-Disposition"] = f'{modo}; filename="{doc.nombre_archivo}.{extension}"'
    return respuesta


def _emitir_atipicas(request, cargas, empresa, gestion, carga=None, grupo=None):
    datos = servicio.reporte_atipicas(cargas, **_filtros(request))
    if datos is None:
        return None
    datos.update({
        "empresa": empresa, "gestion": gestion, "carga": carga, "grupo": grupo,
        "niveles": servicio.NIVELES,
        "validaciones": servicio.VALIDACIONES,
    })
    # En pantalla el listado se pagina (20 por página); el PDF y el Excel lo traen completo.
    from django.core.paginator import Paginator

    datos["pagina_listado"] = Paginator(datos["listado"], 20).get_page(request.GET.get("pagina"))
    consulta = request.GET.copy()
    consulta.pop("pagina", None)
    datos["consulta_listado"] = consulta.urlencode()
    return _entregar(request, "atipicas", datos, "reportes/reporte_atipicas.html",
                     exportar.documento_atipicas,
                     cifras={"total_listado": len(datos["listado"]),
                             "total_atipicas": datos["total_atipicas"],
                             "revisadas": datos["validacion"]["revisadas"],
                             "relevantes": datos["validacion"]["relevantes"]},
                     filtros_texto=datos["filtros_texto"])


@rol_requerido(*ROLES_OPERATIVOS)
def atipicas_gestion(request, empresa_id, gestion_id):
    """Reporte de atípicas de un encargo: todas las cargas válidas de la gestión."""
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    grupo = servicio.cargas_de_gestion(empresa, gestion)
    respuesta = _emitir_atipicas(request, grupo["incluidas"], empresa, gestion, grupo=grupo)
    if respuesta is None:
        messages.info(request, f"{empresa}, gestión {gestion.anio}: todavía no hay cargas "
                               "válidas con análisis completo del modelo.")
        return redirect(f"{reverse('reportes:indice')}?empresa={empresa.id}")
    return respuesta


@rol_requerido(*ROLES_OPERATIVOS)
def atipicas_carga(request, carga_id):
    """Reporte de atípicas de una sola carga."""
    carga = get_object_or_404(
        CargaArchivo.objects.select_related("empresa", "gestion", "usuario", "revisado_por",
                                            "anulado_por"),
        pk=carga_id,
    )
    respuesta = _emitir_atipicas(request, [carga], carga.empresa, carga.gestion, carga=carga)
    if respuesta is None:
        messages.info(request, "Esta carga todavía no tiene un análisis completo con el modelo.")
        return redirect(reverse("registros:detalle_carga", args=[carga.id]) + "?pestana=atipicas")
    return respuesta


def entregar_observaciones(request, cargas, empresa, gestion, carga=None, grupo=None):
    """Reporte de observaciones (lo usa también la vista por carga de registros)."""
    datos = servicio.reporte_observaciones(cargas)
    datos.update({"empresa": empresa, "gestion": gestion, "carga": carga, "grupo": grupo})
    return _entregar(request, "observaciones", datos, "registros/reporte_observaciones.html",
                     exportar.documento_observaciones,
                     cifras={"total_listado": len(datos["filas_reporte"])})


@rol_requerido(*ROLES_OPERATIVOS)
def observaciones_gestion(request, empresa_id, gestion_id):
    """Reporte de transacciones observadas de un encargo (todas las cargas válidas)."""
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    grupo = servicio.cargas_de_gestion(empresa, gestion)
    if not grupo["incluidas"]:
        messages.info(request, f"{empresa}, gestión {gestion.anio}: no hay cargas válidas.")
        return redirect(f"{reverse('reportes:indice')}?empresa={empresa.id}")
    return entregar_observaciones(request, grupo["incluidas"], empresa, gestion, grupo=grupo)


def _emitir_hallazgos(request, cargas, empresa, gestion, carga=None, grupo=None):
    datos = servicio.reporte_hallazgos(cargas)
    datos.update({"empresa": empresa, "gestion": gestion, "carga": carga, "grupo": grupo,
                  "que_procede": _que_procede(cargas) if _formato(request) == "pantalla" else []})
    return _entregar(request, "hallazgos", datos, "reportes/reporte_hallazgos.html",
                     exportar.documento_hallazgos,
                     cifras={"total_listado": len(datos["hallazgos"])})


@rol_requerido(*ROLES_OPERATIVOS)
def hallazgos_gestion(request, empresa_id, gestion_id):
    """Hallazgos confirmados de un encargo: atípicas relevantes y avisos observados."""
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    grupo = servicio.cargas_de_gestion(empresa, gestion)
    if not grupo["incluidas"]:
        messages.info(request, f"{empresa}, gestión {gestion.anio}: no hay cargas válidas.")
        return redirect(f"{reverse('reportes:indice')}?empresa={empresa.id}")
    return _emitir_hallazgos(request, grupo["incluidas"], empresa, gestion, grupo=grupo)


@rol_requerido(*ROLES_OPERATIVOS)
def hallazgos_carga(request, carga_id):
    """Hallazgos confirmados de una sola carga."""
    carga = get_object_or_404(CargaArchivo.objects.select_related("empresa", "gestion"), pk=carga_id)
    return _emitir_hallazgos(request, [carga], carga.empresa, carga.gestion, carga=carga)


@rol_requerido(*ROLES_OPERATIVOS)
def comparativo_empresa(request, empresa_id):
    """Descarga del comparativo de gestiones de una empresa (PDF o Excel)."""
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    formato = _formato(request)
    if formato == "pantalla":
        return redirect(f"{reverse('reportes:indice')}?empresa={empresa.id}")
    filas = servicio.comparativo_empresa(empresa)
    emision = servicio.registrar_emision("comparativo", request.user, empresa=empresa,
                                         cifras={"total_listado": len(filas)}, formato=formato)
    doc = exportar.documento_expediente(empresa, filas, request.user)
    doc.emision = emision.id
    contenido = exportar.a_pdf(doc) if formato == "pdf" else exportar.a_excel(doc)
    tipo_mime, extension = FORMATOS[formato]
    respuesta = HttpResponse(contenido, content_type=tipo_mime)
    respuesta["Content-Disposition"] = f'attachment; filename="{doc.nombre_archivo}.{extension}"'
    return respuesta


# Panorama visual (actividad 16)

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def _periodos_de_gestion(gestion):
    """Atajos de periodo con sentido en auditoría: la gestión, sus trimestres y el mes de
    cierre (donde suelen concentrarse los ajustes). (clave, etiqueta, desde, hasta)."""
    from calendar import monthrange
    from datetime import date

    def fin_de_mes(anio, mes):
        return date(anio, mes, monthrange(anio, mes)[1])

    inicio, fin = gestion.fecha_inicio, gestion.fecha_fin
    periodos = [("gestion", "Toda la gestión", inicio, fin)]
    anio, mes = inicio.year, inicio.month
    for numero in range(1, 5):
        desde = date(anio, mes, 1)
        mes_fin, anio_fin = mes + 2, anio
        if mes_fin > 12:
            mes_fin, anio_fin = mes_fin - 12, anio + 1
        hasta = min(fin_de_mes(anio_fin, mes_fin), fin)
        if desde > fin:
            break
        periodos.append((f"t{numero}", f"Trimestre {numero} ({MESES[desde.month - 1][:3]}–{MESES[hasta.month - 1][:3]})",
                         max(desde, inicio), hasta))
        mes, anio = (mes_fin + 1, anio_fin) if mes_fin < 12 else (1, anio_fin + 1)
    periodos.append(("cierre", f"Mes de cierre ({MESES[fin.month - 1]})", max(date(fin.year, fin.month, 1), inicio), fin))
    return [{"clave": c, "etiqueta": e, "desde": d.isoformat(), "hasta": h.isoformat()} for c, e, d, h in periodos]


@rol_requerido(*ROLES_OPERATIVOS)
def panorama(request, empresa_id, gestion_id):
    """Gráficos de la gestión. La página se muestra al instante con siluetas de carga y
    los datos llegan después desde panorama_datos."""
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    grupo = servicio.cargas_de_gestion(empresa, gestion)
    return render(request, "reportes/panorama.html", {
        "empresa": empresa,
        "gestion": gestion,
        "grupo": grupo,
        "periodos": _periodos_de_gestion(gestion),
        "origen": _origen(request, empresa, gestion),
        "niveles": servicio.NIVELES,
        "url_datos": reverse("reportes:panorama_datos", args=[empresa.id, gestion.id]),
    })


@rol_requerido(*ROLES_OPERATIVOS)
def panorama_datos(request, empresa_id, gestion_id):
    empresa = get_object_or_404(EmpresaAuditada, pk=empresa_id)
    gestion = get_object_or_404(Gestion, pk=gestion_id)
    grupo = servicio.cargas_de_gestion(empresa, gestion)
    desde, hasta = servicio.rango_dentro_de_gestion(
        gestion, request.GET.get("desde", ""), request.GET.get("hasta", ""))
    datos = servicio.panorama(
        grupo["incluidas"],
        desde=desde, hasta=hasta,
        cuenta=request.GET.get("cuenta", ""), nivel=request.GET.get("nivel", ""),
    )
    return JsonResponse(datos)
