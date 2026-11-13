"""Exportación a Excel y PDF a partir de un mismo `Documento` (título, encabezado y secciones)."""
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

RAZON_SOCIAL = "ST&S AUDITORES Y CONSULTORES S.R.L."
PIE = ("Documento generado automáticamente por el sistema de apoyo a la auditoría externa de "
       "ST&S Auditores y Consultores S.R.L. Uso interno, sujeto a revisión profesional.")


@dataclass
class Seccion:
    titulo: str
    columnas: list
    filas: list
    # Proporción de ancho de cada columna (suma libre); None = repartir igual.
    anchos: list = None
    # Índices de columnas numéricas (alineadas a la derecha, con formato de importe).
    numericas: tuple = ()
    nota: str = ""
    # Nombre corto de la hoja de Excel (máximo 31 caracteres).
    hoja: str = ""
    # El detalle estadístico se incluye solo en Excel; las versiones resumidas, solo en el PDF.
    solo_excel: bool = False
    solo_pdf: bool = False


@dataclass
class Documento:
    tipo: str
    titulo: str
    subtitulo: str
    datos: list  # [(etiqueta, valor)]
    secciones: list = field(default_factory=list)
    avisos: list = field(default_factory=list)  # borrador, carga anulada, duplicadas
    emision: int = None
    nombre_archivo: str = "reporte"
    emitido_por: str = ""
    emisor_es_socio: bool = False
    # Reporte de observaciones: lo recibe el representante legal de la empresa auditada.
    firma_cliente: str = ""
    # Contador de la empresa (firma la justificación de las observaciones).
    contador: str = ""



def importe(valor):
    """1234567.8 → '1.234.567,80' (formato boliviano)."""
    if valor in (None, ""):
        return ""
    texto = f"{Decimal(valor):,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def porcentaje(valor, decimales=1):
    if valor is None:
        return "-"
    return f"{valor:.{decimales}f} %".replace(".", ",")


def fecha(valor, con_hora=False):
    if not valor:
        return ""
    if hasattr(valor, "tzinfo") and con_hora:
        valor = timezone.localtime(valor)
        return valor.strftime("%d/%m/%Y %H:%M")
    return valor.strftime("%d/%m/%Y")


def _importe_lado(registro):
    if registro is None:
        return ""
    return f"{importe(registro.debe)} D" if registro.debe else f"{importe(registro.haber)} H"


def _nombre_archivo(tipo, empresa, gestion, carga):
    partes = [tipo, slugify(empresa.nombre)[:40], str(gestion.anio)]
    if carga is not None:
        partes.append(f"carga{carga.id}")
    partes.append(timezone.localtime().strftime("%Y%m%d_%H%M"))
    return "_".join(partes)


def _nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else ""


def _nombre_corto(usuario):
    """Nombre y primer apellido, para columnas angostas."""
    if not usuario:
        return ""
    partes = [usuario.first_name.split()[0] if usuario.first_name else "",
              usuario.last_name.split()[0] if usuario.last_name else ""]
    return " ".join(p for p in partes if p) or usuario.username


def _corto(texto, limite):
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + "…"


def _libros(contexto):
    """Libros que entran en el reporte, en una línea: 'Libro Mayor (Excel/CSV), 21.849 registros'."""
    carga = contexto.get("carga")
    cargas = [carga] if carga is not None else list(contexto.get("cargas", []))
    partes = [f"{c.get_formato_detectado_display() or 'Libro contable'}, "
              f"{c.registros_validos:,} registros".replace(",", ".") for c in cargas]
    return "; ".join(partes) or "Sin libros válidos"


def _datos_comunes(contexto):
    empresa, gestion = contexto["empresa"], contexto["gestion"]
    datos = [
        ("Empresa auditada", f"{empresa}" + (f" (NIT {empresa.nit})" if empresa.nit else "")),
        ("Gestión", str(gestion)),
        ("Libros revisados", _libros(contexto)),
        ("Emitido", f"{fecha(contexto['fecha_generacion'], True)} por {_nombre(contexto['generado_por'])}"),
        ("Emisión N.º", str(contexto["emision"].id)),
    ]
    return datos


def _avisos_comunes(contexto):
    avisos = []
    carga = contexto.get("carga")
    if carga is not None and carga.estado == "anulada":
        avisos.append("CARGA ANULADA: este reporte no tiene validez como papel de trabajo.")
    grupo = contexto.get("grupo")
    if grupo:
        for a, b in grupo["posibles_duplicadas"]:
            avisos.append(f"Posible carga duplicada: N.º {a.id} y N.º {b.id} parecen el mismo libro.")
    return avisos


def _seccion_alcance(contexto, sin_analisis=()):
    grupo = contexto.get("grupo")
    if not grupo:
        return None
    filas = []
    for c in grupo["incluidas"]:
        filas.append([f"N.º {c.id}", c.get_formato_detectado_display() or "Sin dato",
                      f"{fecha(c.fecha_carga)} · {_nombre(c.usuario)}", f"{c.registros_validos:,}".replace(",", "."),
                      "No: sin análisis del modelo" if c in sin_analisis else "Sí"])
    excluidas = len(grupo["anuladas"]) + len(grupo["en_proceso"])
    return Seccion(f"Libros de la gestión {contexto['gestion'].anio} incluidos",
                   ["Carga", "Libro", "Subido", "Registros", "En este reporte"],
                   filas, anchos=[1, 2, 2.4, 1.2, 2.5], numericas=(3,), hoja="Alcance",
                   nota=("" if not excluidas else "No se incluye 1 carga anulada o en proceso." if excluidas == 1
                         else f"No se incluyen {excluidas} cargas anuladas o en proceso."))



def documento_atipicas(d):
    v = d["validacion"]
    datos = _datos_comunes(d) + [
        ("Modelo aplicado", "; ".join(sorted({f"{a.algoritmo} ({a.configuracion})" for a in d["analisis"]}))),
        ("Filtros del listado", d["filtros_texto"] or "Sin filtros"),
    ]
    avisos = _avisos_comunes(d)
    if d["es_borrador"]:
        avisos.append(f"VERSIÓN PRELIMINAR: revisión en curso, {v['revisadas']} de {d['total_atipicas']} "
                      "atípicas revisadas.")

    secciones = []
    alcance = _seccion_alcance(d, d["sin_analisis"])
    if alcance:
        secciones.append(alcance)
    secciones.append(Seccion("1. Resumen", ["Indicador", "Valor"], [
        ["Registros analizados", f"{d['total_registros']:,}".replace(",", ".")],
        ["Transacciones atípicas", f"{d['total_atipicas']} ({porcentaje(d['porcentaje_atipicas'], 2)} de los registros)"],
        ["Importe involucrado", importe(d["importe_total_atipicas"])],
        ["   Debe / Haber", f"{importe(d['importe_atipicas']['debe'])} / {importe(d['importe_atipicas']['haber'])}"],
        ["% del movimiento total", f"{porcentaje(d['porcentaje_importe'], 2)} de {importe(d['movimiento_total'])}"],
        ["También señaladas por las reglas", str(d["coinciden_con_reglas"])],
    ], anchos=[2, 3], hoja="Indicadores"))
    secciones.append(Seccion("2. Validación del auditor",
        ["Revisadas", "Pendientes", "Observadas", "En seguimiento", "Válidas"],
        [[f"{v['revisadas']} de {v['atipicas']}", v["pendientes"], v["observadas"], v["seguimiento"],
          v["no_relevantes"]]], hoja="Validación"))
    secciones.append(Seccion("Utilidad de las señales del modelo",
        ["Útiles sobre lo revisado", f"Útiles entre las {v['primeras']} primeras"],
        [[porcentaje(v["precision"]), porcentaje(v["precision_primeras"])]],
        nota="Útil = observada o en seguimiento.", hoja="Utilidad del modelo", solo_excel=True))
    columnas = ["N.º", "Nivel", "Fecha", "Fila", "Comprobante", "Cuenta", "Glosa", "Importe",
                "Por qué la señala el modelo", "Reglas", "Validación", "Comentario", "Revisado por"]
    anchos = [0.55, 1.05, 0.95, 0.65, 1.3, 1.9, 2.3, 1.35, 3.0, 1.5, 1.2, 1.9, 1.2]
    if d["varias_cargas"]:
        columnas.insert(1, "Carga")
        anchos.insert(1, 0.6)
    filas = []
    for item in d["listado"]:
        r, res = item["registro"], item["resultado"]
        fila = [item["prioridad"], f"{res.nivel_texto} ({res.nivel:.2f})".replace(".", ","),
                fecha(r.fecha), r.fila_origen, r.numero_comprobante,
                f"{r.cuenta.codigo} {r.cuenta.nombre}" + (" [tributaria]" if item["tributaria"] else ""),
                r.glosa, _importe_lado(r),
                "\n".join(("También: " + m["texto"]) if i else m["texto"][:1].upper() + m["texto"][1:]
                          for i, m in enumerate(item["motivos"])),
                "; ".join(a["descripcion"] for a in item["avisos"]),
                res.get_revision_display(), res.comentario_revision,
                f"{_nombre_corto(res.revisado_por)} {fecha(res.fecha_revision)}".strip()]
        if d["varias_cargas"]:
            fila.insert(1, item["carga_id"])
        filas.append(fila)
    secciones.append(Seccion(f"3. Listado priorizado ({len(filas)} transacciones)", columnas, filas,
                             anchos=anchos, numericas=(columnas.index("Importe"),), hoja="Listado",
                             solo_excel=True))
    # PDF: una línea por motivo principal y textos acotados, para que el listado sea legible.
    columnas_pdf = ["N.º", "Nivel", "Fecha", "Comprobante", "Cuenta", "Glosa", "Importe",
                    "Motivo principal", "Validación", "Comentario del auditor"]
    anchos_pdf = [0.45, 0.85, 1.15, 1.4, 1.9, 2.1, 1.55, 2.6, 1.3, 2.15]
    if d["varias_cargas"]:
        columnas_pdf.insert(1, "Carga")
        anchos_pdf.insert(1, 0.55)
    filas_pdf = []
    for item in d["listado"]:
        r, res = item["registro"], item["resultado"]
        motivo = item["motivos"][0]["texto"] if item["motivos"] else ""
        motivo = motivo[:1].upper() + motivo[1:]
        fila = [item["prioridad"], res.nivel_texto, fecha(r.fecha), r.numero_comprobante,
                _corto(f"{r.cuenta.codigo} {r.cuenta.nombre}", 40), _corto(r.glosa, 45), _importe_lado(r),
                _corto(motivo, 95), res.get_revision_display(), _corto(res.comentario_revision, 80)]
        if d["varias_cargas"]:
            fila.insert(1, item["carga_id"])
        filas_pdf.append(fila)
    secciones.append(Seccion(f"3. Listado priorizado ({len(filas_pdf)} transacciones)", columnas_pdf, filas_pdf,
                             anchos=anchos_pdf, numericas=(columnas_pdf.index("Importe"),), solo_pdf=True,
                             nota="El detalle completo (todos los motivos, fila del archivo y reglas) está en el Excel."))
    t = d["tributarias"]
    secciones.append(Seccion("4. Atípicas en cuentas tributarias",
        ["Cuenta", "Nombre", "Atípicas", "Observadas", "Importe"],
        [[c["codigo"], c["nombre"], c["cantidad"], c["relevantes"], importe(c["importe"])] for c in t["por_cuenta"]],
        anchos=[1.3, 3.5, 1, 1, 1.5], numericas=(2, 3, 4),
        nota=(f"{t['cantidad']} atípicas por {importe(t['importe'])}: {t['relevantes']} observadas y "
              f"{t['pendientes']} pendientes. Cuentas de IVA, IT, RC-IVA, IUE, ICE, retenciones o "
              "impuestos; apoya la revisión para el Informe Tributario Complementario."),
        hoja="Tributarias"))
    secciones.append(Seccion("Distribución por nivel de anomalía",
        ["Nivel", "Cantidad", "% de las atípicas", "Importe"],
        [[n["etiqueta"], n["cantidad"], porcentaje(n["porcentaje"]), importe(n["importe"])] for n in d["por_nivel"]],
        numericas=(1, 3), hoja="Por nivel", solo_excel=True))
    secciones.append(Seccion("Distribución por motivo principal",
        ["Motivo principal", "Cantidad", "% de las atípicas", "Importe"],
        [[m["etiqueta"], m["cantidad"], porcentaje(m["porcentaje"]), importe(m["importe"])] for m in d["por_motivo"]],
        anchos=[3, 1, 1.3, 1.5], numericas=(1, 3), hoja="Por motivo", solo_excel=True))
    secciones.append(Seccion("Cuentas con más transacciones atípicas",
        ["Cuenta", "Nombre", "Grupo", "Atípicas", "Observadas", "Importe"],
        [[c["codigo"], c["nombre"], (c["tipo"] or "").capitalize(), c["cantidad"], c["relevantes"],
          importe(c["importe"])] for c in d["por_cuenta"]],
        anchos=[1.3, 3, 1.1, 0.9, 0.9, 1.5], numericas=(3, 4, 5),
        nota="Hasta 15 cuentas, de la que más atípicas tiene a la que menos.", hoja="Por cuenta",
        solo_excel=True))
    secciones.append(Seccion("Distribución por mes", ["Mes", "Atípicas", "Importe"],
        [[f["mes"].strftime("%m/%Y") if f["mes"] else "", f["cantidad"], importe(f["importe"])] for f in d["por_mes"]],
        numericas=(1, 2), hoja="Por mes", solo_excel=True))

    return Documento(
        tipo="atipicas", titulo="Reporte de transacciones atípicas priorizadas",
        subtitulo=("Transacciones que el modelo de Machine Learning señaló como atípicas, de la más a "
                   "la menos atípica, con su motivo y la validación del auditor. El modelo señala casos "
                   "para revisar; la conclusión corresponde al criterio profesional del auditor."),
        datos=datos, secciones=secciones, avisos=avisos, emision=d["emision"].id,
        nombre_archivo=_nombre_archivo("atipicas", d["empresa"], d["gestion"], d.get("carga")))


def documento_observaciones(d):
    # Documento que se entrega y firma con la empresa: solo lo que ella necesita saber.
    avisos = _avisos_comunes(d)
    seguimiento = d.get("avisos_seguimiento", 0)
    if d["avisos_pendientes"] or seguimiento:
        avisos.append("VERSIÓN PRELIMINAR: la revisión todavía no concluye. No firmar hasta recibir la versión definitiva.")
    datos = _datos_comunes(d) + [
        ("Observaciones", str(len(d["filas_reporte"]))),
        ("Importe observado", importe(d["importe_observado"])),
    ]
    secciones = []
    alcance = _seccion_alcance(d)
    if alcance:
        alcance.solo_excel = True
        secciones.append(alcance)
    secciones.append(Seccion("Cobertura de la revisión", ["Indicador", "Valor"], [
        ["Avisos generados por las reglas", d["avisos_total"]],
        ["Observados", d.get("avisos_observados", 0)],
        ["En seguimiento", seguimiento],
        ["Válidos", d["avisos_validos"]],
        ["Pendientes", d["avisos_pendientes"]],
        ["Transacciones atípicas observadas", d.get("atipicas_observadas", 0)],
    ], anchos=[3, 1], numericas=(1,), hoja="Cobertura", solo_excel=True))
    secciones.append(Seccion("Avisos por tipo de regla",
        ["Regla", "Avisos", "Observados", "En seguimiento", "Válidos", "Pendientes"],
        [[t["etiqueta"], t["total"], t["observados"], t.get("seguimiento", 0), t["validos"], t["pendientes"]]
         for t in d["por_tipo"]],
        # Conteo interno de la revisión: el documento que firma la empresa trae solo las observaciones.
        anchos=[3.5, 1, 1, 1.2, 1, 1], numericas=(1, 2, 3, 4, 5), hoja="Por regla", solo_excel=True))
    columnas = ["N.º", "Fecha", "Comprobante", "Cuenta", "Importe", "Observación", "Glosa",
                "Fundamento del auditor", "Justificación del contador"]
    anchos = [0.5, 1.2, 1.5, 2.0, 1.35, 2.4, 2.2, 2.3, 2.6]
    filas = []
    for numero, item in enumerate(d["filas_reporte"], start=1):
        o, r = item["observacion"], item["registro"]
        filas.append([numero, fecha(r.fecha) if r else "", r.numero_comprobante if r else "",
                      f"{r.cuenta.codigo} {r.cuenta.nombre}" if r else "",
                      _importe_lado(r), item.get("texto", o.descripcion), r.glosa if r else "",
                      o.comentario_revision, "\n\n\n"])
    secciones.append(Seccion("Detalle de las observaciones", columnas, filas, anchos=anchos,
                             numericas=(columnas.index("Importe"),), hoja="Observaciones"))
    return Documento(
        tipo="observaciones", titulo="Reporte de transacciones observadas",
        subtitulo="Hallazgos confirmados por el auditor en los avisos de las reglas de validación y en las transacciones atípicas.",
        datos=datos, secciones=secciones, avisos=avisos, emision=d["emision"].id,
        nombre_archivo=_nombre_archivo("observaciones", d["empresa"], d["gestion"], d.get("carga")),
        contador=d["empresa"].contador_nombre,
        firma_cliente=(f"Recibido por: {d['empresa'].contacto_nombre}" if d['empresa'].contacto_nombre
                       else "Recibido por") + f"\nRepresentante legal · {d['empresa']}")


def documento_hallazgos(d):
    avisos = _avisos_comunes(d)
    if d["es_borrador"]:
        avisos.append(f"VERSIÓN PRELIMINAR: faltan revisar {d['pendientes_modelo']} atípicas y {d['pendientes_reglas']} "
                      f"avisos; {len(d.get('en_seguimiento', []))} en seguimiento (no incluidos).")
    o = d["por_origen"]
    datos = _datos_comunes(d) + [
        ("Hallazgos", f"{len(d['hallazgos'])} (modelo {o['modelo']}, reglas {o['reglas']}, ambos {o['ambos']})"),
        ("Importe de los hallazgos", f"{importe(d['importe_total'])} ({porcentaje(d['porcentaje_importe'], 2)} "
                                     f"del movimiento total)"),
        ("En cuentas tributarias", f"{d['tributarios']['cantidad']} por {importe(d['tributarios']['importe'])}"),
    ]
    secciones = []
    alcance = _seccion_alcance(d)
    if alcance:
        secciones.append(alcance)
    columnas = ["N.º", "Fecha", "Fila", "Comprobante", "Cuenta", "Glosa", "Importe", "Origen",
                "Qué se detectó", "Fundamento del auditor"]
    anchos = [0.5, 1.2, 0.8, 1.5, 2.0, 2.3, 1.35, 0.95, 3.0, 2.6]
    if d["varias_cargas"]:
        columnas.insert(1, "Carga")
        anchos.insert(1, 0.6)
    filas = []
    for h in d["hallazgos"]:
        r = h["registro"]
        detectado = []
        fundamento = []
        if h["modelo"]:
            detectado += [("También: " if i else "") + m["texto"] for i, m in enumerate(h["modelo"]["motivos"])]
            if h["modelo"]["comentario"]:
                fundamento.append(h["modelo"]["comentario"])
        for regla in h["reglas"]:
            detectado.append(("Regla: " if h["modelo"] else "") + regla["descripcion"])
            if regla["comentario"]:
                fundamento.append(regla["comentario"])
        fila = [h["numero"], fecha(r.fecha) if r else "", r.fila_origen if r else "",
                r.numero_comprobante if r else "",
                (f"{r.cuenta.codigo} {r.cuenta.nombre}" + (" [tributaria]" if h["tributaria"] else "")) if r else "",
                r.glosa if r else "", _importe_lado(r), h["origen"], "\n".join(detectado), "\n".join(fundamento)]
        if d["varias_cargas"]:
            fila.insert(1, h["carga_id"])
        filas.append(fila)
    secciones.append(Seccion("Detalle de los hallazgos (de mayor a menor importe)", columnas, filas,
                             anchos=anchos, numericas=(columnas.index("Importe"),), hoja="Hallazgos"))
    if d.get("en_seguimiento"):
        secciones.append(Seccion(
            "En seguimiento: atípicas y avisos por cotejar con otros documentos o cuentas",
            ["Carga", "Fecha", "Fila", "Cuenta", "Glosa", "Importe", "Qué falta revisar", "Anotado por"],
            [[s.analisis.carga_id, fecha(s.registro.fecha), s.registro.fila_origen,
              f"{s.registro.cuenta.codigo} {s.registro.cuenta.nombre}", s.registro.glosa,
              _importe_lado(s.registro), s.comentario_revision,
              f"{_nombre_corto(s.revisado_por)} {fecha(s.fecha_revision)}".strip()]
             for s in d["en_seguimiento"]],
            anchos=[0.6, 1, 0.6, 2.2, 2.6, 1.3, 3.2, 1.5], numericas=(5,), hoja="En seguimiento",
            nota="No forman parte de los hallazgos hasta que el auditor las marque como observadas o válidas.",
            # Puntos abiertos: el papel de trabajo impreso o en PDF lleva solo hallazgos.
            solo_excel=True))
    return Documento(
        tipo="hallazgos", titulo="Hallazgos de auditoría",
        subtitulo=("Transacciones confirmadas como hallazgo por el auditor: atípicas del modelo marcadas "
                   "como observadas y avisos de las reglas marcados como observados."),
        datos=datos, secciones=secciones, avisos=avisos, emision=d["emision"].id,
        nombre_archivo=_nombre_archivo("hallazgos", d["empresa"], d["gestion"], d.get("carga")))


def documento_expediente(empresa, filas, usuario):
    """Comparativo de las gestiones de una empresa."""
    def var(valor):
        return "" if valor is None else ("+" if valor > 0 else "") + porcentaje(valor, 0)
    tabla = [[f["gestion"].anio, f"{fecha(f['gestion'].fecha_inicio)} al {fecha(f['gestion'].fecha_fin)}",
              f["cargas"], f"{f['registros']:,}".replace(",", "."), f["atipicas"], var(f["variacion_atipicas"]),
              importe(f["importe_atipicas"]), var(f["variacion_importe_atipicas"]),
              porcentaje(f["porcentaje_importe"]), f["revisadas"], f["relevantes"], f["observaciones"],
              var(f["variacion_observaciones"])] for f in filas]
    return Documento(
        tipo="expediente", titulo="Comparativo de gestiones",
        subtitulo="Cifras de cada gestión de la empresa, con su variación frente a la gestión anterior.",
        datos=[("Empresa auditada", f"{empresa}" + (f" (NIT {empresa.nit})" if empresa.nit else "")),
               ("Emitido", f"{fecha(timezone.now(), True)} por {_nombre(usuario)}")],
        secciones=[Seccion("Gestiones",
            ["Gestión", "Período", "Libros", "Registros", "Atípicas", "Var. atípicas",
             "Importe atípico", "Var. importe", "% del movimiento", "Revisadas", "Útiles",
             "Observaciones", "Var. observ."],
            tabla, anchos=[0.8, 2, 0.7, 1, 0.9, 0.9, 1.5, 0.9, 1, 0.9, 0.9, 1, 0.9],
            numericas=(2, 3, 4, 6, 9, 10, 11), hoja="Gestiones")],
        nombre_archivo=f"comparativo_{slugify(empresa.nombre)[:40]}_{timezone.localtime().strftime('%Y%m%d_%H%M')}")



_INICIO_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _celda(hoja, fila, columna, valor):
    """Escribe un valor; los textos que empiezan como fórmula se guardan como texto
    (protección contra inyección de fórmulas en Excel)."""
    celda = hoja.cell(row=fila, column=columna, value=valor)
    if isinstance(valor, str) and _INICIO_FORMULA.match(valor):
        celda.data_type = "s"
    return celda


_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_IMPORTE = re.compile(r"^(-?[\d.]+,\d{2})(?: ([DH]))?$")
_ENTERO = re.compile(r"^\d{1,3}(\.\d{3})+$")
_PORCENTAJE = re.compile(r"^(-?\d+(?:,\d+)?) %$")


def _valor_excel(valor, numerica=False):
    """Convierte los textos con formato boliviano en números y fechas reales de Excel."""
    import datetime
    if not isinstance(valor, str):
        return valor, None
    texto = valor.strip()
    if not texto:
        return None, None
    m = _FECHA.match(texto)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))), "dd/mm/yyyy"
    m = _IMPORTE.match(texto) if numerica else None
    if m:
        numero = float(m.group(1).replace(".", "").replace(",", "."))
        return numero, '#,##0.00' + (f' "{m.group(2)}"' if m.group(2) else "")
    if numerica and _ENTERO.match(texto):
        return int(texto.replace(".", "")), "#,##0"
    m = _PORCENTAJE.match(texto)
    if m:
        return float(m.group(1).replace(",", ".")) / 100, "0.0 %"
    return valor, None


def a_excel(doc):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    guindo = "6E1B3F"
    cabecera = PatternFill("solid", fgColor=guindo)
    suave = PatternFill("solid", fgColor="F8F2F5")
    borde = Border(*(Side(style="thin", color="D9C9D1"),) * 4)
    libro = Workbook()

    portada = libro.active
    portada.title = "Resumen"
    portada["A1"] = RAZON_SOCIAL
    portada["A1"].font = Font(bold=True, size=12, color=guindo)
    portada["A2"] = doc.titulo
    portada["A2"].font = Font(bold=True, size=14)
    _celda(portada, 3, 1, doc.subtitulo).alignment = Alignment(wrap_text=True, vertical="top")
    portada.merge_cells("A3:D3")
    portada.row_dimensions[3].height = 45
    fila = 5
    for aviso in doc.avisos:
        _celda(portada, fila, 1, aviso).font = Font(bold=True, color="9C5700")
        fila += 1
    if doc.avisos:
        fila += 1
    for etiqueta, valor in doc.datos:
        _celda(portada, fila, 1, etiqueta).font = Font(bold=True)
        _celda(portada, fila, 2, valor)
        fila += 1
    _celda(portada, fila + 1, 1, PIE).font = Font(italic=True, size=8, color="777777")
    portada.column_dimensions["A"].width = 30
    portada.column_dimensions["B"].width = 80

    usados = set()
    for seccion in doc.secciones:
        if seccion.solo_pdf:
            continue
        nombre = re.sub(r"[\[\]:*?/\\]", "", seccion.hoja or seccion.titulo)[:28].strip() or "Hoja"
        base, n = nombre, 2
        while nombre in usados:
            nombre = f"{base[:25]} {n}"
            n += 1
        usados.add(nombre)
        hoja = libro.create_sheet(nombre)
        hoja["A1"] = seccion.titulo
        hoja["A1"].font = Font(bold=True, size=12, color=guindo)
        for c, titulo in enumerate(seccion.columnas, start=1):
            celda = hoja.cell(row=3, column=c, value=titulo)
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = cabecera
            celda.alignment = Alignment(wrap_text=True, vertical="center")
            celda.border = borde
        for f, valores in enumerate(seccion.filas, start=4):
            for c, valor in enumerate(valores, start=1):
                valor, formato = _valor_excel(valor, numerica=(c - 1) in seccion.numericas)
                celda = _celda(hoja, f, c, valor)
                if formato:
                    celda.number_format = formato
                celda.border = borde
                celda.alignment = Alignment(wrap_text=True, vertical="top",
                                            horizontal="right" if (c - 1) in seccion.numericas else "left")
                if f % 2 == 1:
                    celda.fill = suave
        if seccion.nota:
            _celda(hoja, len(seccion.filas) + 5, 1, seccion.nota).font = Font(italic=True, size=9)
        anchos = seccion.anchos or [1] * len(seccion.columnas)
        for c, proporcion in enumerate(anchos, start=1):
            hoja.column_dimensions[get_column_letter(c)].width = max(9, min(60, proporcion * 11))
        hoja.freeze_panes = "A4"
        if seccion.filas:
            hoja.auto_filter.ref = f"A3:{get_column_letter(len(seccion.columnas))}{len(seccion.filas) + 3}"
        hoja.page_setup.orientation = "landscape"
        hoja.page_setup.fitToWidth = 1
        hoja.sheet_properties.pageSetUpPr.fitToPage = True
        hoja.page_setup.fitToHeight = 0

    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()



def a_pdf(doc):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (CondPageBreak, Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)
    from xml.sax.saxutils import escape

    guindo = colors.HexColor("#6E1B3F")
    oscuro = colors.HexColor("#4A1129")
    borde = colors.HexColor("#D9C9D1")
    suave = colors.HexColor("#F8F2F5")
    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=estilos["Normal"], fontName="Helvetica", fontSize=7.5, leading=9)
    derecha = ParagraphStyle("derecha", parent=normal, alignment=TA_RIGHT)
    cabeza = ParagraphStyle("cabeza", parent=normal, fontName="Helvetica-Bold", textColor=colors.white)
    titulo = ParagraphStyle("titulo", parent=estilos["Title"], fontName="Times-Bold", fontSize=16,
                            textColor=oscuro, alignment=0, spaceAfter=4)
    subtitulo = ParagraphStyle("subtitulo", parent=normal, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    seccion_estilo = ParagraphStyle("seccion", parent=estilos["Heading3"], fontName="Times-Bold", fontSize=11,
                                    textColor=oscuro, spaceBefore=10, spaceAfter=4)
    aviso_estilo = ParagraphStyle("aviso", parent=normal, fontSize=8.5, textColor=colors.HexColor("#8A4B00"),
                                  backColor=colors.HexColor("#FFF4DE"), borderPadding=4, spaceAfter=6)

    def p(texto, estilo=normal):
        return Paragraph(escape(str(texto if texto is not None else "")).replace("\n", "<br/>"), estilo)

    salida = io.BytesIO()
    pagina = landscape(letter)
    ancho_util = pagina[0] - 24 * mm

    rubricas = []
    if doc.tipo == "observaciones":
        rubricas = ["Auditor", "Contador", "Rep. legal"]
    elif doc.tipo == "hallazgos":
        rubricas = ["Socio"] if doc.emisor_es_socio else ["Auditor", "Socio"]

    from reportlab.pdfgen.canvas import Canvas

    class CanvasFoliado(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas)
            for estado in self._paginas:
                self.__dict__.update(estado)
                self._pie(total)
                super().showPage()
            super().save()

        def _pie(self, total):
            self.saveState()
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.HexColor("#777777"))
            texto = f"{RAZON_SOCIAL} · {doc.titulo}"
            if doc.emision:
                texto += f" · Emisión N.º {doc.emision}"
            self.drawString(12 * mm, 8 * mm, texto)
            self.drawRightString(pagina[0] - 12 * mm, 8 * mm, f"Página {self._pageNumber} de {total}")
            if rubricas and self._pageNumber < total:  # la última hoja lleva las firmas completas
                ancho, alto, x = 24 * mm, 9 * mm, pagina[0] - 12 * mm
                for nombre in reversed(rubricas):
                    x -= ancho
                    self.setStrokeColor(borde)
                    self.rect(x, 12 * mm, ancho - 2 * mm, alto)
                    self.drawCentredString(x + (ancho - 2 * mm) / 2, 12.8 * mm, nombre)
                self.drawRightString(x - 2 * mm, 15.5 * mm, "Rúbricas:")
            self.restoreState()

    plantilla = SimpleDocTemplate(salida, pagesize=pagina, leftMargin=12 * mm, rightMargin=12 * mm,
                                  topMargin=12 * mm, bottomMargin=(24 if rubricas else 14) * mm, title=doc.titulo,
                                  author=RAZON_SOCIAL)
    elementos = []

    logo = Path(settings.BASE_DIR) / "static" / "img" / "logo-sts-claro.png"
    membrete = [p(RAZON_SOCIAL, ParagraphStyle("rs", parent=normal, fontName="Times-Bold", fontSize=11,
                                              leading=14, textColor=oscuro)),
                p("Auditoría externa e informática forense", subtitulo)]
    if logo.exists():
        try:
            imagen = Image(str(logo), width=38 * mm, height=13 * mm, kind="proportional")
            etiqueta = {"hallazgos": "PAPEL DE TRABAJO",
                        "observaciones": "PARA FIRMAR CON LA EMPRESA"}.get(doc.tipo, "REPORTE DE APOYO")
            encabezado = Table([[imagen, membrete, p(etiqueta, ParagraphStyle(
                "pt", parent=normal, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=oscuro))]],
                colWidths=[42 * mm, ancho_util - 92 * mm, 50 * mm])
        except Exception:
            encabezado = Table([[membrete]], colWidths=[ancho_util])
    else:
        encabezado = Table([[membrete]], colWidths=[ancho_util])
    encabezado.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("LINEBELOW", (0, 0), (-1, 0), 2, guindo),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    elementos += [encabezado, Spacer(1, 6)]
    for aviso in doc.avisos:
        elementos.append(p(aviso, aviso_estilo))
    elementos += [p(doc.titulo, titulo), p(doc.subtitulo, subtitulo), Spacer(1, 6)]

    datos = Table([[p(e, ParagraphStyle("e", parent=normal, fontName="Helvetica-Bold")), p(v)] for e, v in doc.datos],
                  colWidths=[45 * mm, ancho_util - 45 * mm])
    datos.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), suave), ("BOX", (0, 0), (-1, -1), 0.5, borde),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    elementos.append(datos)

    firmas = None
    if doc.tipo in ("hallazgos", "observaciones"):
        firma = ParagraphStyle("firma", parent=normal, fontSize=8, leading=10, alignment=1)  # centrado
        if doc.tipo == "observaciones":
            cargo = "Socio de auditoría" if doc.emisor_es_socio else "Auditor"
            bloques = [f"Elaborado por: {escape(doc.emitido_por)}<br/>{cargo} · {escape(RAZON_SOCIAL)}",
                       (f"{escape(doc.contador)}<br/>Contador de la empresa" if doc.contador
                        else "Contador de la empresa<br/>Nombre: ______________________"),
                       escape(doc.firma_cliente).replace("\n", "<br/>")]
        elif doc.emisor_es_socio:
            bloques = [f"Elaborado y aprobado por: {escape(doc.emitido_por)}<br/>Socio de auditoría · Fecha: ____/____/______"]
        else:
            bloques = [f"Elaborado por: {escape(doc.emitido_por)}<br/>Auditor · Fecha: ____/____/______",
                       "Revisado y aprobado por<br/>Socio de auditoría · Fecha: ____/____/______"]
        celdas = [Paragraph(b, firma) for b in bloques]
        firmas = Table([celdas], colWidths=[ancho_util / max(2, len(celdas))] * len(celdas), hAlign="LEFT")
        firmas.setStyle(TableStyle([("LINEABOVE", (i, 0), (i, 0), 0.6, colors.black) for i in range(len(celdas))]
                                   + [("LEFTPADDING", (0, 0), (-1, -1), 18), ("RIGHTPADDING", (0, 0), (-1, -1), 18)]))

    visibles = [s for s in doc.secciones if not s.solo_excel]
    ultima = visibles[-1] if firmas is not None and visibles and visibles[-1].filas else None
    for seccion in visibles:
        bloque = [p(seccion.titulo, seccion_estilo)]
        if seccion.filas:
            proporciones = seccion.anchos or [1] * len(seccion.columnas)
            total = sum(proporciones)
            anchos = [ancho_util * x / total for x in proporciones]
            filas = [[p(c, cabeza) for c in seccion.columnas]]
            for valores in seccion.filas:
                filas.append([p(v, derecha if i in seccion.numericas else normal) for i, v in enumerate(valores)])
            n = len(filas)
            estilo = [("BACKGROUND", (0, 0), (-1, 0), oscuro), ("GRID", (0, 0), (-1, n - 1), 0.4, borde),
                      ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
            for i in range(2, n, 2):
                estilo.append(("BACKGROUND", (0, i), (-1, i), suave))
            if seccion is ultima:
                filas.append([[Spacer(1, 70), firmas, Spacer(1, 12), p(PIE, subtitulo)]]
                             + [""] * (len(seccion.columnas) - 1))
                estilo += [("SPAN", (0, n), (-1, n)), ("LEFTPADDING", (0, n), (-1, n), 0),
                           ("RIGHTPADDING", (0, n), (-1, n), 0),
                           ("NOSPLIT", (0, max(1, n - 2)), (-1, n))]
            tabla = Table(filas, colWidths=anchos, repeatRows=1)
            tabla.setStyle(TableStyle(estilo))
            bloque.append(tabla)
        else:
            bloque.append(p("Sin datos.", subtitulo))
        if seccion.nota:
            bloque += [Spacer(1, 2), p(seccion.nota, subtitulo)]
        if len(seccion.filas) <= 5 and seccion is not ultima:
            elementos.append(KeepTogether(bloque))
        else:
            elementos += [CondPageBreak(45 * mm)] + bloque

    if firmas is not None and ultima is None:
        elementos += [Spacer(1, 70), KeepTogether([firmas, Spacer(1, 12), p(PIE, subtitulo)])]
    elif firmas is None:
        elementos += [Spacer(1, 10), p(PIE, subtitulo)]

    plantilla.build(elementos, canvasmaker=CanvasFoliado)
    return salida.getvalue()
