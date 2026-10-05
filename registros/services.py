"""Procesamiento y validación de archivos de registros contables (RF-01, RF-02)."""
import csv
import logging
import re
import unicodedata
from collections import Counter
from datetime import date, datetime

import pandas as pd
import pdfplumber
from django.db import transaction

from .models import CargaArchivo, CuentaContable, EmpresaAuditada, ErrorValidacion, RegistroContable

logger = logging.getLogger(__name__)

# Nombres de columna aceptados por campo (en minúscula y sin tildes).
COLUMNAS_ESPERADAS = {
    "fecha": ["fecha"],
    "cuenta": ["cuenta", "codigo cuenta", "cod cuenta", "codigo_cuenta"],
    "nombre_cuenta": ["nombre cuenta", "descripcion cuenta", "cuenta nombre"],
    "glosa": ["glosa", "descripcion", "detalle", "concepto"],
    "debe": ["debe"],
    "haber": ["haber"],
    "saldo": ["saldo"],
    "comprobante": [
        "comprobante",
        "nro comprobante",
        "numero comprobante",
        "n comprobante",
        "numero",
    ],
}

# Solo estas son estrictamente obligatorias en el encabezado.
CAMPOS_REQUERIDOS = ["fecha", "debe", "haber"]

# Filas iniciales donde se busca el encabezado (antes van los títulos del reporte).
FILAS_BUSQUEDA_ENCABEZADO = 30

# Código de cuenta al inicio de una fila de bloque, ej. "1-1-1-01-01 CAJA...".
PATRON_CODIGO_CUENTA = re.compile(r"^([0-9][0-9\-\.]*)\s+(.+)$")

# Plan de cuentas boliviano: el primer dígito del código indica el grupo contable.
GRUPO_CONTABLE_POR_PRIMER_DIGITO = {
    "1": "activo",
    "2": "pasivo",
    "3": "patrimonio",
    "4": "ingreso",
    "5": "gasto",
}


PATRON_FECHA_ISO = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")


def _parsear_fecha(valor):
    """Celda de fecha a Timestamp (NaT si no es fecha). "2023-01-02" se lee como
    año-mes-día; solo "02/01/2023" se lee con el día primero (si no, el 2 de enero
    quedaría como 1 de febrero).
    """
    if isinstance(valor, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(valor)
    if _es_vacio(valor):
        return pd.NaT
    texto = str(valor).strip()
    if PATRON_FECHA_ISO.match(texto):
        return pd.to_datetime(texto, errors="coerce", yearfirst=True, dayfirst=False)
    return pd.to_datetime(texto, errors="coerce", dayfirst=True)


def _monto_bo(monto):
    """Monto con punto de miles y coma decimal (1.087,50), como en la interfaz."""
    return f"{monto:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _sigue_en_proceso(carga):
    """Bloquea la fila de la carga hasta el fin de la transacción y confirma que sigue
    "en proceso". Si mientras se leía el archivo se anuló o se marcó como interrumpida,
    el resultado ya no se guarda (no se pisa la decisión del usuario).
    """
    actual = CargaArchivo.objects.select_for_update().only("estado").get(pk=carga.pk)
    if actual.estado != "pendiente":
        logger.warning("Carga #%s: estado '%s' al terminar; no se guarda el resultado.", carga.pk, actual.estado)
        return False
    return True


def _rechazar_carga(carga, descripcion, total=0, errores=()):
    """Termina la carga como fallida, sin guardar registros."""
    with transaction.atomic():
        if not _sigue_en_proceso(carga):
            return
        ErrorValidacion.objects.bulk_create(list(errores))
        ErrorValidacion.objects.create(carga=carga, fila=0, campo="archivo", descripcion=descripcion)
        carga.total_registros = total
        carga.registros_validos = 0
        carga.registros_con_error = total
        carga.registros_con_aviso = 0
        carga.estado = "con_errores"
        carga.save()


def _es_total_general(fila, mapeo, debe_raw, haber_raw):
    """Fila de sumas finales del libro: solo trae Debe y Haber, iguales y mayores a 0."""
    for campo in ("glosa", "comprobante", "cuenta"):
        if campo in mapeo and not _es_vacio(_valor(fila, mapeo, campo)):
            return False
    debe, error_debe = _parsear_importe(debe_raw)
    haber, error_haber = _parsear_importe(haber_raw)
    return not (error_debe or error_haber) and debe > 0 and debe == haber


# Si más de esta proporción de fechas cae fuera de la gestión, la carga se rechaza.
PROPORCION_MAXIMA_FUERA_DE_GESTION = 0.5


def _tipo_por_codigo(codigo):
    """Tipo de cuenta según el primer dígito del código; "activo" si no se reconoce."""
    primer_caracter = str(codigo).strip()[:1]
    return GRUPO_CONTABLE_POR_PRIMER_DIGITO.get(primer_caracter, "activo")

# Subtotales a ignorar: "Total 01/2023", "TOTAL CUENTA 1-1-1-01-01", "Sumas", "Saldo anterior".
PATRON_SUBTOTAL = re.compile(
    r"^\s*(total|sumas?|saldo\s+(anterior|inicial|final))\b", re.IGNORECASE
)

# Documentos de respaldo citados en la glosa: facturas ("F.526", "F-526", "FACTURA N° 526")
# y recibos ("R-261", "R.261", "RECIBO N° 261").
PATRON_DOCUMENTO = re.compile(
    r"\b(?:(F)(?:ACT(?:URA)?)?|(R)(?:EC(?:IBO)?)?)\s*[.\-]?\s*(?:N\s*[°º.]?\s*)?(\d+)",
    re.IGNORECASE,
)


def _documentos_en_glosa(glosa):
    return frozenset(
        ("F" if factura else "R", numero)
        for factura, recibo, numero in PATRON_DOCUMENTO.findall(str(glosa or ""))
    )


def _avisos_calidad_dato(
    carga, numero_fila, codigo_cuenta, comprobante, fecha_transaccion, debe, haber,
    claves_vistas, verificar_comprobante=True, glosa="",
):
    """Avisos de calidad de dato sobre una fila válida (no la rechazan)."""
    avisos = []

    if fecha_transaccion > date.today():
        avisos.append(
            ErrorValidacion(
                carga=carga, fila=numero_fila, campo="fecha", tipo="aviso",
                descripcion=(
                    f"Fecha posterior a hoy: {fecha_transaccion:%d/%m/%Y}"
                ),
            )
        )

    if verificar_comprobante and not str(comprobante or "").strip():
        avisos.append(
            ErrorValidacion(
                carga=carga, fila=numero_fila, campo="comprobante", tipo="aviso",
                descripcion=(
                    "Sin número de comprobante"
                ),
            )
        )

    # Duplicado solo con comprobante. Si la glosa cita facturas o recibos, también deben
    # coincidir: un comprobante puede agrupar varias facturas del mismo monto.
    comprobante_normalizado = str(comprobante or "").strip()
    if comprobante_normalizado:
        clave = (
            comprobante_normalizado, fecha_transaccion, codigo_cuenta,
            round(debe, 2), round(haber, 2), _documentos_en_glosa(glosa),
        )
        fila_previa = claves_vistas.get(clave)
        if fila_previa is not None:
            avisos.append(
                ErrorValidacion(
                    carga=carga, fila=numero_fila, campo="duplicado", tipo="aviso",
                    descripcion=(
                        f"Posible duplicado de la fila {fila_previa}"
                    ),
                )
            )
        else:
            claves_vistas[clave] = numero_fila

    return avisos


# --- Parseo del Libro Diario en PDF ---
PATRON_PDF_TIPO_FECHA = re.compile(r"^Tipo:\s*(\S+)\s+Fecha:\s*(\d{2}/\d{2}/\d{4})")
PATRON_PDF_NRO_DOC = re.compile(r"^Nro\.\s*Doc\.:\s*(\S+)")
PATRON_PDF_GLOSA = re.compile(r"^Glosa:\s*(.*)$")
PATRON_PDF_TOTAL = re.compile(r"^Total:\s*([\d,\.]+)\s+([\d,\.]+)\s*$")
PATRON_PDF_CODIGO_CUENTA = re.compile(r"^[0-9][0-9\-\.]*$")
PATRON_PDF_MONTO = re.compile(r"^-?[\d,]+\.\d{2}$")
PATRON_PDF_HEADER_TABLA = re.compile(r"^CUENTA\s+NOMBRE\s+DE\s+CUENTA")
TOLERANCIA_VERTICAL_LINEA_PDF = 3.0


def _normalizar(texto):
    texto = str(texto).strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto


def _mapear_columnas(valores_fila):
    """Empareja los valores de una fila (candidata a encabezado) con los
    campos esperados, devolviendo {campo: posición_de_columna}."""
    normalizadas = {}
    for posicion, valor in enumerate(valores_fila):
        if valor is None or str(valor).strip() == "":
            continue
        normalizadas[_normalizar(valor)] = posicion

    mapeo = {}
    for campo, alias in COLUMNAS_ESPERADAS.items():
        for a in alias:
            if a in normalizadas:
                mapeo[campo] = normalizadas[a]
                break
    return mapeo


def _leer_csv_con_filas_irregulares(ruta):
    """Lee un CSV fila por fila y rellena las filas cortas con None."""
    with open(ruta, newline="", encoding="utf-8-sig") as archivo:
        filas = list(csv.reader(archivo))
    ancho = max((len(f) for f in filas), default=0)
    filas_parejas = [fila + [None] * (ancho - len(fila)) for fila in filas]
    return pd.DataFrame(filas_parejas)


def _leer_filas_crudas(carga):
    """Lee el archivo completo sin asumir en qué fila están los encabezados."""
    nombre = carga.archivo.name.lower()
    if nombre.endswith(".csv"):
        return _leer_csv_con_filas_irregulares(carga.archivo.path)
    return pd.read_excel(carga.archivo.path, dtype=str, header=None, sheet_name=0)


def _ubicar_encabezado(df):
    """Busca la fila de encabezados entre las primeras filas del archivo."""
    limite = min(len(df), FILAS_BUSQUEDA_ENCABEZADO)
    for indice in range(limite):
        mapeo = _mapear_columnas(df.iloc[indice].tolist())
        if all(campo in mapeo for campo in CAMPOS_REQUERIDOS):
            return indice, mapeo
    return None, {}


def _valor(fila, mapeo, campo):
    posicion = mapeo.get(campo)
    if posicion is None or posicion >= len(fila):
        return None
    return fila.iloc[posicion]


def _es_vacio(valor):
    return valor is None or (isinstance(valor, str) and valor.strip() == "") or pd.isna(valor)


# Tope del DecimalField (max_digits=14, decimal_places=2): 12 dígitos enteros.
IMPORTE_MAXIMO = 10 ** 12


def _parsear_importe(valor):
    """Convierte una celda de importe a float, sin inventar datos."""
    if _es_vacio(valor):
        return 0.0, None
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        texto = str(valor).strip().replace(" ", "").replace(" ", "")
        if texto.lower().startswith("bs"):
            texto = texto[2:].lstrip(".")
        if "," in texto and "." in texto:
            # El separador que aparece último es el decimal.
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            if re.fullmatch(r"-?\d{1,3}(,\d{3})+", texto):
                texto = texto.replace(",", "")  # 1,234,567 (miles)
            else:
                texto = texto.replace(",", ".")  # 1234,56 (coma decimal)
        elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
            # 1.234 o 1.234.567: punto de miles (los importes llevan 2 decimales, no 3).
            texto = texto.replace(".", "")
        try:
            numero = float(texto)
        except ValueError:
            return None, f"Importe no numérico: {valor}"
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None, f"Importe no válido: {valor}"
    if abs(numero) >= IMPORTE_MAXIMO:
        return None, f"Importe fuera de rango: {valor}"
    return round(numero, 2), None


def _texto_no_vacio_de_fila(fila):
    """Concatena las celdas no vacías de una fila de título/bloque en un
    solo texto (normalmente solo la primera celda trae algo)."""
    partes = [str(v).strip() for v in fila.tolist() if not _es_vacio(v)]
    return " ".join(partes).strip()


def _agrupar_lineas_pdf(words):
    """Agrupa las palabras que devuelve pdfplumber (cada una con su posición x0/top)
    en líneas de texto, según su posición vertical.
    """
    lineas = []
    for palabra in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lineas and abs(palabra["top"] - lineas[-1]["top"]) <= TOLERANCIA_VERTICAL_LINEA_PDF:
            lineas[-1]["palabras"].append(palabra)
            lineas[-1]["top"] = (lineas[-1]["top"] + palabra["top"]) / 2
        else:
            lineas.append({"top": palabra["top"], "palabras": [palabra]})
    for linea in lineas:
        linea["palabras"].sort(key=lambda w: w["x0"])
        linea["texto"] = " ".join(w["text"] for w in linea["palabras"])
    return lineas


def _extraer_comprobantes_pdf(ruta_archivo):
    """Devuelve los comprobantes encontrados en el PDF con sus cuentas y totales."""
    comprobantes = []
    actual = None
    # Umbral horizontal entre las columnas Debe y Haber, medido en un Libro Diario real.
    umbral_debe_haber = (458.8 + 513.9) / 2 + 15

    with pdfplumber.open(ruta_archivo) as pdf:
        for palabras in (page.extract_words() for page in pdf.pages):
            for linea in _agrupar_lineas_pdf(palabras):
                texto = linea["texto"]

                coincidencia = PATRON_PDF_TIPO_FECHA.match(texto)
                if coincidencia:
                    if actual is not None:
                        comprobantes.append(actual)
                    actual = {
                        "tipo": coincidencia.group(1),
                        "fecha": coincidencia.group(2),
                        "nro_doc": "",
                        "glosa": "",
                        "cuentas": [],
                        "total_debe": None,
                        "total_haber": None,
                    }
                    continue

                if actual is None:
                    continue  # texto antes del primer comprobante (título de página, etc.)

                coincidencia = PATRON_PDF_NRO_DOC.match(texto)
                if coincidencia:
                    actual["nro_doc"] = coincidencia.group(1)
                    continue

                coincidencia = PATRON_PDF_GLOSA.match(texto)
                if coincidencia:
                    actual["glosa"] = coincidencia.group(1).strip()
                    continue

                if PATRON_PDF_HEADER_TABLA.match(texto):
                    continue

                coincidencia = PATRON_PDF_TOTAL.match(texto)
                if coincidencia:
                    actual["total_debe"] = float(coincidencia.group(1).replace(",", ""))
                    actual["total_haber"] = float(coincidencia.group(2).replace(",", ""))
                    continue

                primera_palabra = linea["palabras"][0]["text"] if linea["palabras"] else ""
                if PATRON_PDF_CODIGO_CUENTA.match(primera_palabra):
                    codigo = primera_palabra
                    montos = [w for w in linea["palabras"][1:] if PATRON_PDF_MONTO.match(w["text"])]
                    nombre = " ".join(
                        w["text"] for w in linea["palabras"][1:] if w not in montos
                    ).strip()
                    debe = sum(
                        float(w["text"].replace(",", "")) for w in montos if w["x0"] < umbral_debe_haber
                    )
                    haber = sum(
                        float(w["text"].replace(",", "")) for w in montos if w["x0"] >= umbral_debe_haber
                    )
                    actual["cuentas"].append((codigo, nombre or codigo, debe, haber))
                # cualquier otra línea (nombre de empresa, NIT, "Razon Social", etc.) se ignora

    if actual is not None:
        comprobantes.append(actual)
    return comprobantes


def _procesar_pdf(carga):
    """Variante de `procesar_carga` para el Libro Diario en PDF."""
    try:
        comprobantes = _extraer_comprobantes_pdf(carga.archivo.path)
    except Exception:  # PDF corrupto, protegido, escaneado sin texto, etc.
        logger.exception("Carga #%s: no se pudo leer el PDF", carga.pk)
        _rechazar_carga(
            carga,
            "No se pudo leer el archivo PDF. Verificar que no esté dañado, protegido con "
            "contraseña ni escaneado como imagen.",
        )
        return None

    carga.formato_detectado = "diario_pdf"

    if not comprobantes:
        _rechazar_carga(
            carga,
            "PDF sin comprobantes reconocibles (se espera el Libro Diario con bloques 'Tipo: ... Fecha: ...')",
        )
        return None

    total = 0
    filas_validas = []
    errores_a_guardar = []
    nombre_por_codigo = {}
    primera_fila_por_codigo = {}
    claves_vistas = {}  # clave -> primera fila donde apareció, para detectar duplicados
    numero_fila = 0  # no hay una fila de hoja de cálculo real: es un conteo secuencial

    for indice_comprobante, comprobante in enumerate(comprobantes, start=1):
        fecha = _parsear_fecha(comprobante["fecha"])
        suma_debe = sum(c[2] for c in comprobante["cuentas"])
        suma_haber = sum(c[3] for c in comprobante["cuentas"])
        descuadre = (
            comprobante["total_debe"] is None
            or abs(suma_debe - comprobante["total_debe"]) >= 0.01
            or abs(suma_haber - comprobante["total_haber"]) >= 0.01
        )

        for codigo_cuenta, nombre_cuenta, debe, haber in comprobante["cuentas"]:
            numero_fila += 1
            total += 1
            errores_fila = []

            if pd.isna(fecha):
                errores_fila.append(("fecha", f"Fecha no válida: {comprobante['fecha']}"))
            if debe == 0 and haber == 0:
                errores_fila.append(("debe/haber", "Sin monto en Debe ni en Haber"))
            if debe < 0 or haber < 0:
                errores_fila.append(("debe/haber", "Monto negativo"))
            if debe > 0 and haber > 0:
                errores_fila.append(("debe/haber", "Monto en Debe y en Haber a la vez"))
            if descuadre:
                errores_fila.append(
                    (
                        "comprobante",
                        f"Comprobante {comprobante['nro_doc']} no cuadra con su total impreso "
                        f"(leído: Debe {_monto_bo(suma_debe)} / Haber {_monto_bo(suma_haber)})",
                    )
                )

            if errores_fila:
                for campo, descripcion in errores_fila:
                    errores_a_guardar.append(
                        ErrorValidacion(
                            carga=carga, fila=numero_fila, campo=campo, descripcion=descripcion
                        )
                    )
                continue

            fecha_transaccion = fecha.date()
            if not (carga.gestion.fecha_inicio <= fecha_transaccion <= carga.gestion.fecha_fin):
                errores_a_guardar.append(
                    ErrorValidacion(
                        carga=carga,
                        fila=numero_fila,
                        campo="fecha",
                        tipo="aviso",
                        descripcion=(
                            f"Fecha fuera de la gestión {carga.gestion.anio}: {fecha_transaccion:%d/%m/%Y}"
                        ),
                    )
                )

            errores_a_guardar.extend(
                _avisos_calidad_dato(
                    carga, numero_fila, codigo_cuenta, comprobante["nro_doc"],
                    fecha_transaccion, debe, haber, claves_vistas,
                    glosa=comprobante["glosa"],
                )
            )

            nombre_por_codigo.setdefault(codigo_cuenta, nombre_cuenta or codigo_cuenta)
            primera_fila_por_codigo.setdefault(codigo_cuenta, numero_fila)
            filas_validas.append(
                {
                    "codigo_cuenta": codigo_cuenta,
                    "fecha": fecha.date(),
                    "comprobante": comprobante["nro_doc"],
                    "glosa": comprobante["glosa"],
                    "debe": debe,
                    "haber": haber,
                    "saldo": None,  # el Libro Diario en PDF no trae columna de saldo
                    "fila_origen": numero_fila,
                }
            )

    return filas_validas, errores_a_guardar, total, nombre_por_codigo, primera_fila_por_codigo


def _procesar_hoja_calculo(carga):
    """Variante de `procesar_carga` para Excel/CSV."""
    try:
        df = _leer_filas_crudas(carga)
    except Exception:  # archivo corrupto, formato inesperado, etc.
        logger.exception("Carga #%s: no se pudo leer la hoja de cálculo", carga.pk)
        _rechazar_carga(
            carga,
            "No se pudo leer el archivo. Verificar que no esté dañado ni protegido con "
            "contraseña, y que sea .xlsx, .xls o .csv.",
        )
        return None

    fila_encabezado, mapeo = _ubicar_encabezado(df)
    if fila_encabezado is None:
        _rechazar_carga(carga, "Faltan las columnas fecha, debe y haber")
        return None

    tiene_columna_cuenta = "cuenta" in mapeo
    carga.formato_detectado = "diario_plano" if tiene_columna_cuenta else "mayor"

    total = 0
    filas_validas = []  # datos ya validados, listos para RegistroContable
    errores_a_guardar = []  # instancias de ErrorValidacion sin guardar todavía
    cuenta_actual = None  # (codigo, nombre), solo en el formato agrupado
    nombre_por_codigo = {}  # primer nombre visto para cada código nuevo
    primera_fila_por_codigo = {}  # primera fila donde apareció cada código nuevo
    claves_vistas = {}  # clave -> primera fila donde apareció, para detectar duplicados

    for indice in range(fila_encabezado + 1, len(df)):
        fila = df.iloc[indice]
        numero_fila = indice + 1  # fila real del archivo (1 = primera fila)

        fecha_raw = _valor(fila, mapeo, "fecha")
        fecha = _parsear_fecha(fecha_raw)
        if pd.isna(fecha) and all(
            campo in _mapear_columnas(fila.tolist()) for campo in CAMPOS_REQUERIDOS
        ):
            # Encabezado repetido en cada página o bloque: no es una transacción.
            continue
        debe_raw = _valor(fila, mapeo, "debe")
        haber_raw = _valor(fila, mapeo, "haber")
        tiene_importe = not (_es_vacio(debe_raw) and _es_vacio(haber_raw))

        if pd.isna(fecha) and _es_total_general(fila, mapeo, debe_raw, haber_raw):
            # Total general al pie del libro: no es una transacción.
            continue

        if not tiene_columna_cuenta and pd.isna(fecha):
            # Sin fecha: separador, subtotal o encabezado de un bloque de cuenta.
            texto = _texto_no_vacio_de_fila(fila)
            if not texto or PATRON_SUBTOTAL.match(texto):
                continue
            if tiene_importe:
                # Con importes: es una transacción con la fecha mal escrita.
                pass
            else:
                coincidencia = PATRON_CODIGO_CUENTA.match(texto)
                if coincidencia:
                    cuenta_actual = (coincidencia.group(1).strip(), coincidencia.group(2).strip())
                else:
                    # Sin código reconocible: el texto sirve de nombre y de código.
                    cuenta_actual = (texto[:30], texto)
                continue

        total += 1
        errores_fila = []

        if pd.isna(fecha):
            errores_fila.append(
                ("fecha", "Fecha vacía" if _es_vacio(fecha_raw) else f"Fecha no válida: {fecha_raw}")
            )

        if tiene_columna_cuenta:
            codigo_cuenta = str(_valor(fila, mapeo, "cuenta") or "").strip()
            nombre_cuenta = str(_valor(fila, mapeo, "nombre_cuenta") or codigo_cuenta).strip()
            if not codigo_cuenta or codigo_cuenta.lower() == "nan":
                errores_fila.append(("cuenta", "Código de cuenta vacío"))
        elif cuenta_actual is not None:
            codigo_cuenta, nombre_cuenta = cuenta_actual
        else:
            codigo_cuenta = None
            errores_fila.append(
                ("cuenta", "Movimiento sin cuenta (antes del primer encabezado)")
            )

        debe, error_debe = _parsear_importe(debe_raw)
        haber, error_haber = _parsear_importe(haber_raw)
        if error_debe:
            errores_fila.append(("debe", error_debe))
        if error_haber:
            errores_fila.append(("haber", error_haber))
        # El saldo es informativo: None si no trae un número válido.
        saldo_raw = _valor(fila, mapeo, "saldo")
        saldo, error_saldo = _parsear_importe(saldo_raw)
        if error_saldo or _es_vacio(saldo_raw):
            saldo = None
        if not (error_debe or error_haber):
            if debe == 0 and haber == 0:
                errores_fila.append(("debe/haber", "Sin monto en Debe ni en Haber"))
            if debe < 0 or haber < 0:
                errores_fila.append(("debe/haber", "Monto negativo"))
            if debe > 0 and haber > 0:
                errores_fila.append(("debe/haber", "Monto en Debe y en Haber a la vez"))

        if errores_fila:
            for campo, descripcion in errores_fila:
                errores_a_guardar.append(
                    ErrorValidacion(
                        carga=carga, fila=numero_fila, campo=campo, descripcion=descripcion
                    )
                )
            continue

        fecha_transaccion = fecha.date()
        if not (carga.gestion.fecha_inicio <= fecha_transaccion <= carga.gestion.fecha_fin):
            errores_a_guardar.append(
                ErrorValidacion(
                    carga=carga,
                    fila=numero_fila,
                    campo="fecha",
                    tipo="aviso",
                    descripcion=(
                        f"Fecha fuera de la gestión {carga.gestion.anio}: {fecha_transaccion:%d/%m/%Y}"
                    ),
                )
            )

        comprobante_valor = str(_valor(fila, mapeo, "comprobante") or "").strip()
        errores_a_guardar.extend(
            _avisos_calidad_dato(
                carga, numero_fila, codigo_cuenta, comprobante_valor,
                fecha_transaccion, debe, haber, claves_vistas,
                verificar_comprobante="comprobante" in mapeo,
                glosa=_valor(fila, mapeo, "glosa"),
            )
        )

        nombre_por_codigo.setdefault(codigo_cuenta, nombre_cuenta or codigo_cuenta)
        primera_fila_por_codigo.setdefault(codigo_cuenta, numero_fila)
        filas_validas.append(
            {
                "codigo_cuenta": codigo_cuenta,
                "fecha": fecha.date(),
                "comprobante": comprobante_valor,
                "glosa": str(_valor(fila, mapeo, "glosa") or "").strip(),
                "debe": debe,
                "haber": haber,
                "saldo": saldo,
                "fila_origen": numero_fila,
            }
        )

    return filas_validas, errores_a_guardar, total, nombre_por_codigo, primera_fila_por_codigo


def _avisos_naturaleza_cuenta(carga, filas_validas, cuentas_por_codigo):
    """Avisos de saldo/movimiento fuera de lo normal según el grupo contable de la
    cuenta.
    """
    avisos = []
    for f in filas_validas:
        cuenta = cuentas_por_codigo[f["codigo_cuenta"]]
        if cuenta.tipo == "ingreso" and f["debe"] > 0:
            avisos.append(
                ErrorValidacion(
                    carga=carga, fila=f["fila_origen"], campo="naturaleza_cuenta",
                    tipo="aviso",
                    descripcion=(
                        f"Ingreso registrado en el Debe · {cuenta.codigo} {cuenta.nombre}"
                    ),
                )
            )
        elif cuenta.tipo == "gasto" and f["haber"] > 0:
            avisos.append(
                ErrorValidacion(
                    carga=carga, fila=f["fila_origen"], campo="naturaleza_cuenta",
                    tipo="aviso",
                    descripcion=(
                        f"Gasto registrado en el Haber · {cuenta.codigo} {cuenta.nombre}"
                    ),
                )
            )
    return avisos


def procesar_carga(carga):
    """Procesa el archivo de una CargaArchivo y guarda los resultados."""
    nombre = carga.archivo.name.lower()
    if nombre.endswith(".pdf"):
        resultado = _procesar_pdf(carga)
    else:
        resultado = _procesar_hoja_calculo(carga)

    if resultado is None:
        return  # ya se guardó el error fatal correspondiente

    filas_validas, errores_a_guardar, total, nombre_por_codigo, primera_fila_por_codigo = resultado

    if total == 0:
        # Un archivo sin transacciones no puede quedar validado.
        _rechazar_carga(carga, "El archivo no contiene transacciones", errores=errores_a_guardar)
        return

    fuera_de_gestion = [f for f in filas_validas if not (
        carga.gestion.fecha_inicio <= f["fecha"] <= carga.gestion.fecha_fin
    )]
    if filas_validas and len(fuera_de_gestion) / len(filas_validas) > PROPORCION_MAXIMA_FUERA_DE_GESTION:
        # Gestión equivocada: no se guarda nada y se indica a qué año corresponde.
        anios = Counter(f["fecha"].year for f in fuera_de_gestion)
        anio_probable = anios.most_common(1)[0][0]
        _rechazar_carga(
            carga,
            f"{len(fuera_de_gestion)} de {len(filas_validas)} fechas están fuera de la "
            f"gestión {carga.gestion.anio}; la mayoría son de {anio_probable}. "
            "Corresponde anular esta carga y subir el archivo con la gestión correcta.",
            total=total,
        )
        return

    # Una sola transacción: si algo falla, no queda nada a medias.
    with transaction.atomic():
        if not _sigue_en_proceso(carga):
            return
        # Una carga por empresa a la vez: evita crear dos veces las mismas cuentas.
        EmpresaAuditada.objects.select_for_update().only("id").get(pk=carga.empresa_id)
        _guardar_resultado(
            carga, filas_validas, errores_a_guardar, total,
            nombre_por_codigo, primera_fila_por_codigo,
        )


def _guardar_resultado(
    carga, filas_validas, errores_a_guardar, total, nombre_por_codigo, primera_fila_por_codigo
):
    # Plan de cuentas de la empresa: una consulta y, si hace falta, un bulk_create.
    cuentas_empresa = CuentaContable.objects.filter(empresa=carga.empresa)
    codigos_necesarios = set(nombre_por_codigo)
    cuentas_por_codigo = {
        c.codigo: c for c in cuentas_empresa.filter(codigo__in=codigos_necesarios)
    }
    codigos_nuevos = codigos_necesarios - cuentas_por_codigo.keys()
    if codigos_nuevos:
        CuentaContable.objects.bulk_create(
            [
                CuentaContable(
                    empresa=carga.empresa,
                    codigo=codigo,
                    nombre=nombre_por_codigo[codigo],
                    tipo=_tipo_por_codigo(codigo),
                )
                for codigo in codigos_nuevos
            ]
        )
        # MySQL no devuelve los id tras bulk_create; se vuelven a consultar.
        for cuenta in cuentas_empresa.filter(codigo__in=codigos_nuevos):
            cuentas_por_codigo[cuenta.codigo] = cuenta
            errores_a_guardar.append(
                ErrorValidacion(
                    carga=carga,
                    fila=primera_fila_por_codigo[cuenta.codigo],
                    campo="cuenta",
                    tipo="aviso",
                    descripcion=(
                        f"Cuenta nueva en el catálogo · {cuenta.codigo}, clasificada como "
                        f"{cuenta.get_tipo_display()}"
                    ),
                )
            )

    errores_a_guardar.extend(
        _avisos_naturaleza_cuenta(carga, filas_validas, cuentas_por_codigo)
    )

    registros = [
        RegistroContable(
            carga=carga,
            cuenta=cuentas_por_codigo[f["codigo_cuenta"]],
            fecha=f["fecha"],
            numero_comprobante=f["comprobante"],
            glosa=f["glosa"],
            debe=f["debe"],
            haber=f["haber"],
            saldo=f.get("saldo"),
            fila_origen=f["fila_origen"],
        )
        for f in filas_validas
    ]
    RegistroContable.objects.bulk_create(registros, batch_size=1000)
    ErrorValidacion.objects.bulk_create(errores_a_guardar, batch_size=1000)

    validos = len(filas_validas)
    con_error = total - validos
    # Los avisos son de filas guardadas; se cuentan aparte de los errores.
    con_aviso = sum(1 for e in errores_a_guardar if e.tipo == "aviso")
    carga.total_registros = total
    carga.registros_validos = validos
    carga.registros_con_error = con_error
    carga.registros_con_aviso = con_aviso
    if con_error and validos == 0:
        # No se guardó ni una sola fila: ahí sí es un fracaso real.
        carga.estado = "con_errores"
    elif con_error or con_aviso:
        # Filas rechazadas o avisos pendientes: requiere revisión del auditor.
        carga.estado = "con_observaciones"
    else:
        carga.estado = "validado"
    carga.save()
