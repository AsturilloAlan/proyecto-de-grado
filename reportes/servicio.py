"""Contenido de los reportes (actividad 15).

En el trabajo real, la unidad de auditoría es el encargo: una empresa en una gestión
(por ejemplo, "Empresa X, gestión 2023"). Una gestión puede tener varias cargas (libro
mayor y libro diario, o un libro subido por partes) y también cargas anuladas que ya no
cuentan. Por eso los reportes se emiten por empresa y gestión, consolidando las cargas
válidas, y también se pueden emitir para una sola carga.

Las mismas funciones alimentarán la exportación a PDF y Excel (actividad 17), para que
todas las salidas digan exactamente lo mismo.
"""
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from analisis.explicacion import ETIQUETAS
from analisis.models import REVISIONES_UTILES, AnalisisCarga, ResultadoTransaccion
from registros.models import CargaArchivo, ErrorValidacion, RegistroContable

from .models import ReporteGenerado

CERO = Decimal("0")
# (clave, etiqueta, nivel mínimo, nivel máximo): mismos cortes que ResultadoTransaccion.nivel_texto.
NIVELES = [
    ("muy_alto", "Muy alto", 0.9, None),
    ("alto", "Alto", 0.7, 0.9),
    ("medio", "Medio", None, 0.7),
]
VALIDACIONES = {"pendiente": "Pendientes", "relevante": "Observadas", "seguimiento": "En seguimiento",
                "no_relevante": "Válidas"}
ETIQUETAS_REGLA = {
    "duplicado": "Posible duplicado",
    "fecha": "Fecha fuera de la gestión o posterior a hoy",
    "comprobante": "Sin número de comprobante",
    "naturaleza_cuenta": "Movimiento contrario a la naturaleza de la cuenta",
    "cuenta": "Cuenta nueva en el catálogo",
}
MAXIMO_CUENTAS = 15
PRIMERAS = 20
MINUTOS_SIN_REPETIR_REGISTRO = 10


# Cargas que entran en un reporte

def cargas_de_gestion(empresa, gestion):
    """Cargas de un encargo, separadas según si cuentan para los reportes."""
    todas = list(CargaArchivo.objects.filter(empresa=empresa, gestion=gestion)
                 .select_related("usuario", "revisado_por", "anulado_por")
                 .order_by("fecha_carga", "id"))
    incluidas = [c for c in todas if c.estado not in ("pendiente", "anulada")]
    return {
        "incluidas": incluidas,
        "anuladas": [c for c in todas if c.estado == "anulada"],
        "en_proceso": [c for c in todas if c.estado == "pendiente"],
        "posibles_duplicadas": posibles_duplicadas(incluidas),
    }


def posibles_duplicadas(cargas):
    """Pares de cargas válidas que parecen el mismo libro subido dos veces: mismo tipo de
    libro, mismas filas guardadas y mismo movimiento total."""
    firmas = {}
    pares = []
    for carga in cargas:
        sumas = carga.registros.aggregate(debe=Sum("debe"), haber=Sum("haber"))
        firma = (carga.formato_detectado, carga.registros_validos, sumas["debe"], sumas["haber"])
        if firma in firmas:
            pares.append((firmas[firma], carga))
        else:
            firmas[firma] = carga
    return pares


# Utilidades

def _filtro_nivel(clave):
    for codigo, _, minimo, maximo in NIVELES:
        if codigo == clave:
            condicion = Q()
            if minimo is not None:
                condicion &= Q(nivel__gte=minimo)
            if maximo is not None:
                condicion &= Q(nivel__lt=maximo)
            return condicion
    return None


def _sumas(qs, prefijo="registro__"):
    datos = qs.aggregate(debe=Sum(f"{prefijo}debe"), haber=Sum(f"{prefijo}haber"))
    return {"debe": datos["debe"] or CERO, "haber": datos["haber"] or CERO}


def _porcentaje(parte, total):
    return float(parte) * 100 / float(total) if total else 0.0


# Cuentas tributarias (IVA, IT, RC-IVA, IUE, retenciones, impuestos): en la entrevista el
# socio de la firma señaló la verificación tributaria como lo más crítico de la revisión.
PATRON_TRIBUTARIA = re.compile(
    r"\b(IVA|I\.?\s?T\.?|RC[\s-]?IVA|IUE|ICE|IMPUESTOS?|TRIBUT\w*|RETENCION\w*|"
    r"DEBITO\s+FISCAL|CREDITO\s+FISCAL|FISCO)\b"
)


def _normalizar(texto):
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return sin_tildes.upper()


def es_cuenta_tributaria(nombre):
    return bool(PATRON_TRIBUTARIA.search(_normalizar(nombre)))


def filtros_validos(nivel="", motivo="", validacion="", tributarias=""):
    """Normaliza los filtros: los valores desconocidos se ignoran."""
    return {
        "nivel": nivel if any(nivel == n[0] for n in NIVELES) else "",
        "motivo": motivo if motivo in ETIQUETAS else "",
        "validacion": validacion if validacion in VALIDACIONES else "",
        "tributarias": "1" if tributarias == "1" else "",
    }


def descripcion_filtros(filtros):
    partes = []
    if filtros["nivel"]:
        partes.append("Nivel " + next(n[1] for n in NIVELES if n[0] == filtros["nivel"]).lower())
    if filtros["motivo"]:
        partes.append("Motivo: " + ETIQUETAS[filtros["motivo"]].lower())
    if filtros["validacion"]:
        partes.append("Validación: " + VALIDACIONES[filtros["validacion"]].lower())
    if filtros["tributarias"]:
        partes.append("Solo cuentas tributarias")
    return " · ".join(partes)


def metricas_validacion(atipicas, primeras=PRIMERAS):
    """Validación del auditor sobre un conjunto de atípicas (de una o varias cargas)."""
    total = atipicas.count()
    revisadas = atipicas.exclude(revision="pendiente")
    total_revisadas = revisadas.count()
    relevantes = revisadas.filter(revision__in=REVISIONES_UTILES).count()
    top_ids = list(atipicas.order_by("-nivel", "analisis__carga_id", "registro__fila_origen")
                   .values_list("id", flat=True)[:primeras])
    top = atipicas.filter(id__in=top_ids).exclude(revision="pendiente")
    top_revisadas = top.count()
    top_relevantes = top.filter(revision__in=REVISIONES_UTILES).count()
    return {
        "atipicas": total,
        "revisadas": total_revisadas,
        # Señales útiles: observadas + en seguimiento.
        "relevantes": relevantes,
        "observadas": revisadas.filter(revision="relevante").count(),
        "seguimiento": revisadas.filter(revision="seguimiento").count(),
        "no_relevantes": total_revisadas - relevantes,
        "pendientes": total - total_revisadas,
        "precision": relevantes * 100 / total_revisadas if total_revisadas else None,
        "primeras": min(primeras, len(top_ids)),
        "primeras_revisadas": top_revisadas,
        "primeras_relevantes": top_relevantes,
        "precision_primeras": top_relevantes * 100 / top_revisadas if top_revisadas else None,
    }


# Reporte de transacciones atípicas

def reporte_atipicas(cargas, nivel="", motivo="", validacion="", tributarias=""):
    """Datos del reporte de atípicas de una o varias cargas (las de un encargo).

    Solo entran las cargas con análisis completo. Devuelve None si ninguna lo tiene.
    """
    cargas = list(cargas)
    analisis_por_carga = {
        a.carga_id: a for a in AnalisisCarga.objects.filter(carga__in=cargas, estado="completado")
    }
    analizadas = [c for c in cargas if c.id in analisis_por_carga]
    if not analizadas:
        return None
    filtros = filtros_validos(nivel, motivo, validacion, tributarias)

    todas = (ResultadoTransaccion.objects
             .filter(analisis__carga__in=analizadas, atipica=True)
             .select_related("registro", "registro__cuenta", "revisado_por", "analisis")
             .order_by("-nivel", "analisis__carga_id", "registro__fila_origen"))
    total_atipicas = todas.count()
    total_registros = sum(analisis_por_carga[c.id].total_registros for c in analizadas)

    # Movimiento total de los libros: base para medir la materialidad de lo señalado.
    movimiento = _sumas(RegistroContable.objects.filter(carga__in=analizadas), prefijo="")
    movimiento_total = movimiento["debe"] + movimiento["haber"]
    importe_atipicas = _sumas(todas)
    importe_total_atipicas = importe_atipicas["debe"] + importe_atipicas["haber"]

    por_carga = []
    for carga in cargas:
        analisis = analisis_por_carga.get(carga.id)
        por_carga.append({
            "carga": carga,
            "analisis": analisis,
            "atipicas": todas.filter(analisis__carga=carga).count() if analisis else None,
        })

    por_nivel = []
    for codigo, etiqueta, _, _ in NIVELES:
        qs = todas.filter(_filtro_nivel(codigo))
        sumas = _sumas(qs)
        cantidad = qs.count()
        por_nivel.append({"clave": codigo, "etiqueta": etiqueta, "cantidad": cantidad,
                          "porcentaje": _porcentaje(cantidad, total_atipicas),
                          "importe": sumas["debe"] + sumas["haber"]})

    por_motivo = [
        {"clave": f["motivo_principal"], "etiqueta": ETIQUETAS.get(f["motivo_principal"], "Sin motivo"),
         "cantidad": f["cantidad"], "porcentaje": _porcentaje(f["cantidad"], total_atipicas),
         "importe": (f["debe"] or CERO) + (f["haber"] or CERO)}
        for f in todas.order_by().values("motivo_principal").annotate(
            cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber"),
        ).order_by("-cantidad")
    ]

    por_cuenta = [
        {"codigo": f["registro__cuenta__codigo"], "nombre": f["registro__cuenta__nombre"],
         "tipo": f["registro__cuenta__tipo"], "cantidad": f["cantidad"],
         "importe": (f["debe"] or CERO) + (f["haber"] or CERO),
         "relevantes": f["relevantes"]}
        for f in todas.order_by().values(
            "registro__cuenta__codigo", "registro__cuenta__nombre", "registro__cuenta__tipo",
        ).annotate(
            cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber"),
            relevantes=Count("id", filter=Q(revision="relevante")),
        ).order_by("-cantidad", "registro__cuenta__codigo")[:MAXIMO_CUENTAS]
    ]

    por_mes = [
        {"mes": f["mes"], "cantidad": f["cantidad"],
         "importe": (f["debe"] or CERO) + (f["haber"] or CERO)}
        for f in todas.order_by().annotate(mes=TruncMonth("registro__fecha")).values("mes").annotate(
            cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber"),
        ).order_by("mes")
    ]

    # Cruce con las reglas de validación: avisos de la misma fila en la misma carga.
    avisos = {}
    for carga_id, fila, descripcion, estado in ErrorValidacion.objects.filter(
            carga__in=analizadas, tipo="aviso").values_list("carga_id", "fila", "descripcion",
                                                           "estado_revision"):
        avisos.setdefault((carga_id, fila), []).append({"descripcion": descripcion, "estado": estado})
    coinciden_con_reglas = sum(
        1 for clave in todas.order_by().values_list("analisis__carga_id", "registro__fila_origen")
        if tuple(clave) in avisos)

    cuentas_tributarias = {
        cuenta_id for cuenta_id, nombre in
        todas.order_by().values_list("registro__cuenta_id", "registro__cuenta__nombre").distinct()
        if es_cuenta_tributaria(nombre)
    }
    atipicas_tributarias = todas.filter(registro__cuenta_id__in=cuentas_tributarias)
    suma_tributaria = _sumas(atipicas_tributarias)
    tributarias_datos = {
        "cantidad": atipicas_tributarias.count(),
        "importe": suma_tributaria["debe"] + suma_tributaria["haber"],
        "relevantes": atipicas_tributarias.filter(revision="relevante").count(),
        "pendientes": atipicas_tributarias.filter(revision="pendiente").count(),
        "por_cuenta": [
            {"codigo": f["registro__cuenta__codigo"], "nombre": f["registro__cuenta__nombre"],
             "cantidad": f["cantidad"], "relevantes": f["relevantes"],
             "importe": (f["debe"] or CERO) + (f["haber"] or CERO)}
            for f in atipicas_tributarias.order_by().values(
                "registro__cuenta__codigo", "registro__cuenta__nombre").annotate(
                cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber"),
                relevantes=Count("id", filter=Q(revision="relevante")),
            ).order_by("-cantidad", "registro__cuenta__codigo")
        ],
    }

    prioridad = {r_id: i for i, r_id in enumerate(todas.values_list("id", flat=True), start=1)}
    listado_qs = todas
    if filtros["tributarias"]:
        listado_qs = listado_qs.filter(registro__cuenta_id__in=cuentas_tributarias)
    if filtros["nivel"]:
        listado_qs = listado_qs.filter(_filtro_nivel(filtros["nivel"]))
    if filtros["motivo"]:
        listado_qs = listado_qs.filter(motivo_principal=filtros["motivo"])
    if filtros["validacion"]:
        listado_qs = listado_qs.filter(revision=filtros["validacion"])
    listado = [
        {
            "prioridad": prioridad[resultado.id],
            "resultado": resultado,
            "registro": resultado.registro,
            "carga_id": resultado.analisis.carga_id,
            "tributaria": resultado.registro.cuenta_id in cuentas_tributarias,
            "motivos": resultado.lista_motivos,
            "avisos": avisos.get((resultado.analisis.carga_id, resultado.registro.fila_origen), []),
        }
        for resultado in listado_qs
    ]

    validacion_datos = metricas_validacion(todas)
    return {
        "cargas": cargas,
        "por_carga": por_carga,
        "varias_cargas": len(analizadas) > 1,
        "sin_analisis": [c for c in cargas if c.id not in analisis_por_carga],
        "analisis": [analisis_por_carga[c.id] for c in analizadas],
        "filtros": filtros,
        "filtros_texto": descripcion_filtros(filtros),
        "total_registros": total_registros,
        "total_atipicas": total_atipicas,
        "porcentaje_atipicas": _porcentaje(total_atipicas, total_registros),
        "movimiento_total": movimiento_total,
        "importe_atipicas": importe_atipicas,
        "importe_total_atipicas": importe_total_atipicas,
        "porcentaje_importe": _porcentaje(importe_total_atipicas, movimiento_total),
        "por_nivel": por_nivel,
        "por_motivo": por_motivo,
        "por_cuenta": por_cuenta,
        "por_mes": por_mes,
        "validacion": validacion_datos,
        "coinciden_con_reglas": coinciden_con_reglas,
        "tributarias": tributarias_datos,
        "listado": listado,
        "es_borrador": validacion_datos["pendientes"] > 0,
    }


# Reporte de transacciones observadas

def reporte_observaciones(cargas):
    """Hallazgos confirmados por el auditor (avisos marcados como observados)."""
    cargas = list(cargas)
    avisos = ErrorValidacion.objects.filter(carga__in=cargas, tipo="aviso")
    observaciones = list(avisos.filter(estado_revision="observado")
                         .select_related("revisado_por", "carga").order_by("carga_id", "fila"))
    claves = {(o.carga_id, o.fila) for o in observaciones}
    registros = {
        (r.carga_id, r.fila_origen): r
        for r in RegistroContable.objects.filter(
            carga__in=cargas, fila_origen__in={fila for _, fila in claves}).select_related("cuenta")
        if (r.carga_id, r.fila_origen) in claves
    }
    filas = []
    for observacion in observaciones:
        registro = registros.get((observacion.carga_id, observacion.fila))
        filas.append({
            "observacion": observacion,
            "registro": registro,
            "carga_id": observacion.carga_id,
            "orden": (observacion.carga_id,
                      registro.numero_comprobante if registro else "",
                      registro.cuenta.tipo if registro else ""),
        })
    filas.sort(key=lambda item: item["orden"])

    por_tipo = [
        {"campo": f["campo"], "etiqueta": ETIQUETAS_REGLA.get(f["campo"], f["campo"].capitalize()),
         "total": f["total"], "observados": f["observados"],
         "validos": f["validos"], "pendientes": f["pendientes"]}
        for f in avisos.order_by().values("campo").annotate(
            total=Count("id"),
            observados=Count("id", filter=Q(estado_revision="observado")),
            validos=Count("id", filter=Q(estado_revision="valido")),
            pendientes=Count("id", filter=Q(revisado=False)),
        ).order_by("-total")
    ]
    importe = sum(((f["registro"].debe + f["registro"].haber) for f in filas if f["registro"]), CERO)
    return {
        "cargas": cargas,
        "varias_cargas": len(cargas) > 1,
        "filas_reporte": filas,
        "avisos_total": avisos.count(),
        "avisos_pendientes": avisos.filter(revisado=False).count(),
        "avisos_validos": avisos.filter(estado_revision="valido").count(),
        "por_tipo": por_tipo,
        "importe_observado": importe,
    }


# Reporte de hallazgos

def reporte_hallazgos(cargas):
    """Hallazgos confirmados por el auditor, sin importar de dónde salieron: atípicas del
    modelo marcadas como observadas y avisos de las reglas marcados como observados. Si una
    misma transacción salió por las dos vías, aparece una sola vez con ambos orígenes."""
    cargas = list(cargas)
    hallazgos = {}

    relevantes = (ResultadoTransaccion.objects
                  .filter(analisis__carga__in=cargas, atipica=True, revision="relevante")
                  .select_related("registro", "registro__cuenta", "revisado_por", "analisis"))
    for r in relevantes:
        clave = (r.analisis.carga_id, r.registro.fila_origen)
        hallazgos[clave] = {
            "carga_id": r.analisis.carga_id, "registro": r.registro,
            "modelo": {"nivel": r.nivel, "nivel_texto": r.nivel_texto, "motivos": r.lista_motivos,
                       "comentario": r.comentario_revision, "revisado_por": r.revisado_por,
                       "fecha": r.fecha_revision},
            "reglas": [],
        }

    observados = (ErrorValidacion.objects
                  .filter(carga__in=cargas, tipo="aviso", estado_revision="observado")
                  .select_related("revisado_por"))
    registros = {
        (r.carga_id, r.fila_origen): r
        for r in RegistroContable.objects.filter(
            carga__in=cargas, fila_origen__in={o.fila for o in observados}).select_related("cuenta")
    }
    for o in observados:
        clave = (o.carga_id, o.fila)
        item = hallazgos.setdefault(clave, {
            "carga_id": o.carga_id, "registro": registros.get(clave), "modelo": None, "reglas": [],
        })
        item["reglas"].append({"descripcion": o.descripcion, "regla": ETIQUETAS_REGLA.get(o.campo, o.campo),
                               "comentario": o.comentario_revision, "revisado_por": o.revisado_por,
                               "fecha": o.fecha_revision})

    lista = []
    for item in hallazgos.values():
        registro = item["registro"]
        item["importe"] = (registro.debe + registro.haber) if registro else CERO
        item["origen"] = ("Modelo y reglas" if item["modelo"] and item["reglas"]
                          else "Modelo" if item["modelo"] else "Reglas")
        item["tributaria"] = bool(registro) and es_cuenta_tributaria(registro.cuenta.nombre)
        lista.append(item)
    lista.sort(key=lambda h: (-h["importe"], h["carga_id"], h["registro"].fila_origen if h["registro"] else 0))
    for numero, item in enumerate(lista, start=1):
        item["numero"] = numero

    importe_total = sum((h["importe"] for h in lista), CERO)
    movimiento = _sumas(RegistroContable.objects.filter(carga__in=cargas), prefijo="")
    movimiento_total = movimiento["debe"] + movimiento["haber"]
    pendientes_modelo = ResultadoTransaccion.objects.filter(
        analisis__carga__in=cargas, atipica=True, revision="pendiente").count()
    pendientes_reglas = ErrorValidacion.objects.filter(
        carga__in=cargas, tipo="aviso", revisado=False).count()
    tributarios = [h for h in lista if h["tributaria"]]
    # En seguimiento: pendiente de respaldo, sin confirmar ni descartar como hallazgo.
    en_seguimiento = list(
        ResultadoTransaccion.objects
        .filter(analisis__carga__in=cargas, atipica=True, revision="seguimiento")
        .select_related("registro", "registro__cuenta", "revisado_por", "analisis")
        .order_by("-nivel", "analisis__carga_id", "registro__fila_origen"))
    return {
        "cargas": cargas,
        "varias_cargas": len(cargas) > 1,
        "hallazgos": lista,
        "en_seguimiento": en_seguimiento,
        "importe_total": importe_total,
        "porcentaje_importe": _porcentaje(importe_total, movimiento_total),
        "movimiento_total": movimiento_total,
        "por_origen": {
            "modelo": sum(1 for h in lista if h["origen"] == "Modelo"),
            "reglas": sum(1 for h in lista if h["origen"] == "Reglas"),
            "ambos": sum(1 for h in lista if h["origen"] == "Modelo y reglas"),
        },
        "tributarios": {"cantidad": len(tributarios),
                        "importe": sum((h["importe"] for h in tributarios), CERO)},
        "pendientes_modelo": pendientes_modelo,
        "pendientes_reglas": pendientes_reglas,
        "es_borrador": bool(pendientes_modelo or pendientes_reglas or en_seguimiento),
    }


# Comparativo entre gestiones de una empresa

def comparativo_empresa(empresa):
    """Una fila por gestión de la empresa, con las cargas válidas consolidadas."""
    from registros.models import Gestion

    filas = []
    for gestion in Gestion.objects.filter(cargas__empresa=empresa).distinct().order_by("anio"):
        grupo = cargas_de_gestion(empresa, gestion)
        incluidas = grupo["incluidas"]
        atipicas = ResultadoTransaccion.objects.filter(
            analisis__carga__in=incluidas, analisis__estado="completado", atipica=True)
        registros = RegistroContable.objects.filter(carga__in=incluidas)
        movimiento = _sumas(registros, prefijo="")
        importe = _sumas(atipicas)
        total_mov = movimiento["debe"] + movimiento["haber"]
        total_imp = importe["debe"] + importe["haber"]
        m = metricas_validacion(atipicas)
        filas.append({
            "gestion": gestion,
            "cargas": len(incluidas),
            "anuladas": len(grupo["anuladas"]),
            "registros": registros.count(),
            "atipicas": m["atipicas"],
            "porcentaje_atipicas": _porcentaje(m["atipicas"], registros.count()),
            "importe_atipicas": total_imp,
            "porcentaje_importe": _porcentaje(total_imp, total_mov),
            "revisadas": m["revisadas"],
            "relevantes": m["relevantes"],
            "observadas": m["observadas"],
            "seguimiento": m["seguimiento"],
            "precision": m["precision"],
            "observaciones": ErrorValidacion.objects.filter(
                carga__in=incluidas, tipo="aviso", estado_revision="observado").count(),
        })
    anterior = None
    for fila in filas:
        fila["anterior"] = anterior.get("gestion") if anterior else None
        for clave in ("atipicas", "importe_atipicas", "observaciones", "porcentaje_atipicas"):
            fila[f"variacion_{clave}"] = _variacion(fila[clave], anterior[clave]) if anterior else None
        if fila["registros"]:
            anterior = fila
    return filas


def _variacion(actual, previo):
    """Variación porcentual; None si no hay base para comparar."""
    if not previo:
        return None
    return (float(actual) - float(previo)) * 100 / float(previo)


# Constancia de emisión

def registrar_emision(tipo, usuario, carga=None, empresa=None, gestion=None, filtros_texto="",
                      cifras=None, formato="pantalla"):
    """Deja constancia de la emisión. No repite el registro si la misma persona emitió el
    mismo reporte, con los mismos filtros y las mismas cifras, en los últimos minutos
    (por ejemplo, al recargar la página)."""
    cifras = cifras or {}
    if carga is not None:
        empresa, gestion = carga.empresa, carga.gestion
    reciente = timezone.now() - timedelta(minutes=MINUTOS_SIN_REPETIR_REGISTRO)
    previo = ReporteGenerado.objects.filter(
        tipo=tipo, carga=carga, empresa=empresa, gestion=gestion, usuario=usuario,
        filtros=filtros_texto, formato=formato, fecha__gte=reciente, **cifras,
    ).first()
    if previo:
        return previo
    return ReporteGenerado.objects.create(
        tipo=tipo, carga=carga, empresa=empresa, gestion=gestion, usuario=usuario,
        filtros=filtros_texto, formato=formato, **cifras)


# Panorama visual (actividad 16)

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MAXIMO_CUENTAS_GRAFICO = 10


def _fecha_o_none(texto):
    from datetime import date

    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def rango_dentro_de_gestion(gestion, desde, hasta):
    """Filtro de fechas válido: dentro de la gestión y con desde <= hasta. Una fecha mal
    escrita o fuera de la gestión (por ejemplo 1900 o un año futuro) se ignora."""
    def ajustar(texto):
        fecha = _fecha_o_none(texto)
        if fecha is None or not (gestion.fecha_inicio <= fecha <= gestion.fecha_fin):
            return ""
        return fecha.isoformat()

    desde, hasta = ajustar(desde), ajustar(hasta)
    if desde and hasta and desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


def panorama(cargas, desde="", hasta="", cuenta="", nivel=""):
    """Datos de los gráficos de una gestión, ya filtrados. Todo en números simples para que
    la página los dibuje sin cálculos propios."""
    cargas = list(cargas)
    base = ResultadoTransaccion.objects.filter(
        analisis__carga__in=cargas, analisis__estado="completado", atipica=True)

    filtros = {"desde": _fecha_o_none(desde), "hasta": _fecha_o_none(hasta),
               "cuenta": cuenta if str(cuenta).isdigit() else "",
               "nivel": nivel if any(nivel == n[0] for n in NIVELES) else ""}
    # Cuentas que tienen atípicas, para el selector (antes de filtrar por cuenta).
    opciones_cuenta = [
        {"id": f["registro__cuenta_id"], "texto": f"{f['registro__cuenta__codigo']} {f['registro__cuenta__nombre']}"}
        for f in base.order_by().values("registro__cuenta_id", "registro__cuenta__codigo",
                                        "registro__cuenta__nombre").distinct()
        .order_by("registro__cuenta__codigo")
    ]
    qs = base
    if filtros["desde"]:
        qs = qs.filter(registro__fecha__gte=filtros["desde"])
    if filtros["hasta"]:
        qs = qs.filter(registro__fecha__lte=filtros["hasta"])
    if filtros["cuenta"]:
        qs = qs.filter(registro__cuenta_id=int(filtros["cuenta"]))
    if filtros["nivel"]:
        qs = qs.filter(_filtro_nivel(filtros["nivel"]))

    total = qs.count()
    sumas = _sumas(qs)
    importe_total = sumas["debe"] + sumas["haber"]
    movimiento = _sumas(RegistroContable.objects.filter(carga__in=cargas), prefijo="")
    movimiento_total = movimiento["debe"] + movimiento["haber"]

    def numero(valor):
        return float(valor or 0)

    por_mes = {}
    for f in qs.order_by().annotate(mes=TruncMonth("registro__fecha")).values("mes").annotate(
            cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber")):
        if f["mes"]:
            por_mes[(f["mes"].year, f["mes"].month)] = (f["cantidad"], numero(f["debe"]) + numero(f["haber"]))
    meses = sorted(por_mes)
    serie_mes = {
        "etiquetas": [f"{MESES[m - 1]} {a}" for a, m in meses],
        "cantidades": [por_mes[k][0] for k in meses],
        "importes": [round(por_mes[k][1], 2) for k in meses],
    }

    motivos = list(qs.order_by().values("motivo_principal").annotate(
        cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber")).order_by("-cantidad"))
    serie_motivo = {
        "etiquetas": [ETIQUETAS.get(f["motivo_principal"], "Sin motivo") for f in motivos],
        "cantidades": [f["cantidad"] for f in motivos],
        "importes": [round(numero(f["debe"]) + numero(f["haber"]), 2) for f in motivos],
    }

    serie_nivel = {"etiquetas": [], "cantidades": []}
    for codigo, etiqueta, _, _ in NIVELES:
        serie_nivel["etiquetas"].append(etiqueta)
        serie_nivel["cantidades"].append(qs.filter(_filtro_nivel(codigo)).count())

    cuentas = list(qs.order_by().values("registro__cuenta__codigo", "registro__cuenta__nombre").annotate(
        cantidad=Count("id"), debe=Sum("registro__debe"), haber=Sum("registro__haber"),
    ).order_by("-cantidad", "registro__cuenta__codigo")[:MAXIMO_CUENTAS_GRAFICO])
    serie_cuenta = {
        "etiquetas": [f"{f['registro__cuenta__codigo']} {f['registro__cuenta__nombre']}"[:45] for f in cuentas],
        "cantidades": [f["cantidad"] for f in cuentas],
        "importes": [round(numero(f["debe"]) + numero(f["haber"]), 2) for f in cuentas],
    }

    revision = {
        "etiquetas": ["Observadas", "En seguimiento", "Válidas", "Pendientes de revisar"],
        "cantidades": [qs.filter(revision="relevante").count(), qs.filter(revision="seguimiento").count(),
                       qs.filter(revision="no_relevante").count(), qs.filter(revision="pendiente").count()],
    }

    mas_atipicas = [
        {"carga": r.analisis.carga_id, "fila": r.registro.fila_origen, "fecha": r.registro.fecha.isoformat(),
         "cuenta": f"{r.registro.cuenta.codigo} {r.registro.cuenta.nombre}", "glosa": r.registro.glosa[:90],
         "importe": numero(r.registro.debe) or numero(r.registro.haber),
         "lado": "D" if r.registro.debe else "H", "nivel": r.nivel_texto,
         "motivo": (r.lista_motivos[0]["texto"] if r.lista_motivos else ""),
         "revision": r.get_revision_display()}
        for r in qs.select_related("registro", "registro__cuenta", "analisis")
        .order_by("-nivel", "analisis__carga_id", "registro__fila_origen")[:10]
    ]

    return {
        "filtros": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in filtros.items()},
        "opciones_cuenta": opciones_cuenta,
        "resumen": {
            "atipicas": total,
            "importe": round(numero(importe_total), 2),
            "porcentaje_movimiento": round(_porcentaje(importe_total, movimiento_total), 2),
            "revisadas": sum(revision["cantidades"][:3]),
            "relevantes": revision["cantidades"][0] + revision["cantidades"][1],
        },
        "por_mes": serie_mes,
        "por_motivo": serie_motivo,
        "por_nivel": serie_nivel,
        "por_cuenta": serie_cuenta,
        "revision": revision,
        "mas_atipicas": mas_atipicas,
    }
