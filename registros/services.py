"""Procesamiento y validación de archivos de registros contables (RF-01, RF-02)."""
import csv
import re
import unicodedata
from datetime import date

import pandas as pd
import pdfplumber
from django.db import transaction

from .models import CuentaContable, ErrorValidacion, RegistroContable

# Nombres de columna aceptados (en minúscula y sin tildes) para cada
# campo esperado. Permite variaciones razonables en cómo viene el archivo.
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

# Cuántas filas iniciales se revisan buscando la fila real de encabezados
# (las filas de título de la empresa/periodo van antes).
FILAS_BUSQUEDA_ENCABEZADO = 30

# Código de cuenta al inicio de una fila de bloque, ej. "1-1-1-01-01 CAJA...".
PATRON_CODIGO_CUENTA = re.compile(r"^([0-9][0-9\-\.]*)\s+(.+)$")

# Plan de cuentas boliviano típico: el primer dígito del código indica el grupo
# contable.
GRUPO_CONTABLE_POR_PRIMER_DIGITO = {
    "1": "activo",
    "2": "pasivo",
    "3": "patrimonio",
    "4": "ingreso",
    "5": "gasto",
}


def _tipo_por_codigo(codigo):
    """Tipo de cuenta según el primer dígito del código; "activo" si no se reconoce."""
    primer_caracter = str(codigo).strip()[:1]
    return GRUPO_CONTABLE_POR_PRIMER_DIGITO.get(primer_caracter, "activo")

# Filas de subtotal/saldo a ignorar: "Total 01/2023", "TOTAL CUENTA 1-1-1-01-01",
# "Sumas", "Saldo anterior".
PATRON_SUBTOTAL = re.compile(
    r"^\s*(total|sumas?|saldo\s+(anterior|inicial|final))\b", re.IGNORECASE
)

def _avisos_calidad_dato(
    carga, numero_fila, codigo_cuenta, comprobante, fecha_transaccion, debe, haber,
    claves_vistas, verificar_comprobante=True,
):
    """Avisos de calidad de dato sobre una fila válida (no la rechazan)."""
    avisos = []

    if fecha_transaccion > date.today():
        avisos.append(
            ErrorValidacion(
                carga=carga, fila=numero_fila, campo="fecha", tipo="aviso",
                descripcion=(
                    f"La fecha {fecha_transaccion:%d/%m/%Y} es posterior a hoy; "
                    "verificar que no sea un error de tipeo."
                ),
            )
        )

    if verificar_comprobante and not str(comprobante or "").strip():
        avisos.append(
            ErrorValidacion(
                carga=carga, fila=numero_fila, campo="comprobante", tipo="aviso",
                descripcion=(
                    "La fila no trae número de comprobante; dificulta rastrearla "
                    "hasta su documento de origen."
                ),
            )
        )

    # Solo se busca duplicado cuando hay número de comprobante.
    comprobante_normalizado = str(comprobante or "").strip()
    if comprobante_normalizado:
        clave = (comprobante_normalizado, fecha_transaccion, codigo_cuenta, round(debe, 2), round(haber, 2))
        fila_previa = claves_vistas.get(clave)
        if fila_previa is not None:
            avisos.append(
                ErrorValidacion(
                    carga=carga, fila=numero_fila, campo="duplicado", tipo="aviso",
                    descripcion=(
                        f"Mismo comprobante ({comprobante_normalizado}), fecha, cuenta y "
                        f"montos que la fila {fila_previa}; podría ser un registro repetido "
                        "por error."
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


# Tope del DecimalField de debe/haber/saldo (max_digits=14, decimal_places=2):
# 12 dígitos enteros como máximo.
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
        try:
            numero = float(texto)
        except ValueError:
            return None, f"Importe inválido: '{valor}' no es un número"
    if numero != numero or numero in (float("inf"), float("-inf")):
        return None, f"Importe inválido: '{valor}'"
    if abs(numero) >= IMPORTE_MAXIMO:
        return None, f"Importe fuera de rango: '{valor}'"
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
    except Exception as exc:  # PDF corrupto, protegido, escaneado sin texto, etc.
        ErrorValidacion.objects.create(
            carga=carga,
            fila=0,
            campo="archivo",
            descripcion=f"No se pudo leer el archivo PDF: {exc}",
        )
        carga.estado = "con_errores"
        carga.total_registros = 0
        carga.save()
        return None

    carga.formato_detectado = "diario_pdf"

    if not comprobantes:
        ErrorValidacion.objects.create(
            carga=carga,
            fila=0,
            campo="archivo",
            descripcion=(
                "No se encontró ningún comprobante reconocible en el PDF (se esperaba "
                "el formato de Libro Diario con bloques 'Tipo: ... Fecha: ...')."
            ),
        )
        carga.estado = "con_errores"
        carga.total_registros = 0
        carga.save()
        return None

    total = 0
    filas_validas = []
    errores_a_guardar = []
    nombre_por_codigo = {}
    primera_fila_por_codigo = {}
    claves_vistas = {}  # clave -> primera fila donde apareció, para detectar duplicados
    numero_fila = 0  # no hay una fila de hoja de cálculo real: es un conteo secuencial

    for indice_comprobante, comprobante in enumerate(comprobantes, start=1):
        fecha = pd.to_datetime(comprobante["fecha"], errors="coerce", dayfirst=True)
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
                errores_fila.append(("fecha", f"Fecha inválida: '{comprobante['fecha']}'"))
            if debe == 0 and haber == 0:
                errores_fila.append(("debe/haber", "La fila no tiene monto en Debe ni en Haber"))
            if debe < 0 or haber < 0:
                errores_fila.append(("debe/haber", "Debe y Haber no pueden ser negativos"))
            if debe > 0 and haber > 0:
                errores_fila.append(("debe/haber", "La fila tiene monto en Debe y en Haber a la vez; cada línea debe ir en uno solo"))
            if descuadre:
                errores_fila.append(
                    (
                        "comprobante",
                        f"El comprobante Nº {comprobante['nro_doc']} no cuadra: la suma "
                        f"leída (Debe {suma_debe:,.2f} / Haber {suma_haber:,.2f}) no coincide "
                        f"con el total impreso en el PDF; revisar ese comprobante en el "
                        f"documento original antes de confiar en estas filas.",
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
                            f"La fecha {fecha_transaccion:%d/%m/%Y} está fuera del período de "
                            f"la gestión {carga.gestion.anio} "
                            f"({carga.gestion.fecha_inicio:%d/%m/%Y} - "
                            f"{carga.gestion.fecha_fin:%d/%m/%Y}); verificar que corresponda a "
                            "esta gestión."
                        ),
                    )
                )

            errores_a_guardar.extend(
                _avisos_calidad_dato(
                    carga, numero_fila, codigo_cuenta, comprobante["nro_doc"],
                    fecha_transaccion, debe, haber, claves_vistas,
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
    except Exception as exc:  # archivo corrupto, formato inesperado, etc.
        ErrorValidacion.objects.create(
            carga=carga,
            fila=0,
            campo="archivo",
            descripcion=f"No se pudo leer el archivo: {exc}",
        )
        carga.estado = "con_errores"
        carga.total_registros = 0
        carga.save()
        return None

    fila_encabezado, mapeo = _ubicar_encabezado(df)
    if fila_encabezado is None:
        ErrorValidacion.objects.create(
            carga=carga,
            fila=0,
            campo="archivo",
            descripcion=(
                "No se encontraron las columnas requeridas (fecha, debe, haber) "
                "entre las primeras filas del archivo."
            ),
        )
        carga.estado = "con_errores"
        carga.total_registros = 0
        carga.save()
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
        fecha = pd.to_datetime(fecha_raw, errors="coerce", dayfirst=True)
        debe_raw = _valor(fila, mapeo, "debe")
        haber_raw = _valor(fila, mapeo, "haber")
        tiene_importe = not (_es_vacio(debe_raw) and _es_vacio(haber_raw))

        if not tiene_columna_cuenta and pd.isna(fecha):
            # Sin fecha válida puede ser un separador en blanco, un
            # subtotal, o el encabezado de un nuevo bloque de cuenta.
            texto = _texto_no_vacio_de_fila(fila)
            if not texto or PATRON_SUBTOTAL.match(texto):
                continue
            if tiene_importe:
                # Trae importes en Debe/Haber: es una transacción con la fecha mal
                # escrita, no un encabezado de cuenta.
                pass
            else:
                coincidencia = PATRON_CODIGO_CUENTA.match(texto)
                if coincidencia:
                    cuenta_actual = (coincidencia.group(1).strip(), coincidencia.group(2).strip())
                else:
                    # Bloque sin un código reconocible al inicio: se usa el texto
                    # completo como nombre y como código.
                    cuenta_actual = (texto[:30], texto)
                continue

        # A partir de aquí, se trata como fila de transacción.
        total += 1
        errores_fila = []

        if pd.isna(fecha):
            errores_fila.append(("fecha", f"Fecha inválida o vacía: '{fecha_raw}'"))

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
                ("cuenta", "Transacción encontrada antes de cualquier encabezado de cuenta")
            )

        debe, error_debe = _parsear_importe(debe_raw)
        haber, error_haber = _parsear_importe(haber_raw)
        if error_debe:
            errores_fila.append(("debe", error_debe))
        if error_haber:
            errores_fila.append(("haber", error_haber))
        # El saldo es solo informativo: se guarda si trae un número válido, o None si
        # no.
        saldo_raw = _valor(fila, mapeo, "saldo")
        saldo, error_saldo = _parsear_importe(saldo_raw)
        if error_saldo or _es_vacio(saldo_raw):
            saldo = None
        if not (error_debe or error_haber):
            if debe == 0 and haber == 0:
                errores_fila.append(("debe/haber", "La fila no tiene monto en Debe ni en Haber"))
            if debe < 0 or haber < 0:
                errores_fila.append(("debe/haber", "Debe y Haber no pueden ser negativos"))
            if debe > 0 and haber > 0:
                errores_fila.append(("debe/haber", "La fila tiene monto en Debe y en Haber a la vez; cada línea debe ir en uno solo"))

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
                        f"La fecha {fecha_transaccion:%d/%m/%Y} está fuera del período de "
                        f"la gestión {carga.gestion.anio} "
                        f"({carga.gestion.fecha_inicio:%d/%m/%Y} - "
                        f"{carga.gestion.fecha_fin:%d/%m/%Y}); verificar que corresponda a "
                        "esta gestión."
                    ),
                )
            )

        comprobante_valor = str(_valor(fila, mapeo, "comprobante") or "").strip()
        errores_a_guardar.extend(
            _avisos_calidad_dato(
                carga, numero_fila, codigo_cuenta, comprobante_valor,
                fecha_transaccion, debe, haber, claves_vistas,
                verificar_comprobante="comprobante" in mapeo,
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
                        f"La cuenta de ingreso '{cuenta.codigo} - {cuenta.nombre}' tiene "
                        "movimiento en el Debe; lo habitual es que los ingresos se "
                        "registren en el Haber."
                    ),
                )
            )
        elif cuenta.tipo == "gasto" and f["haber"] > 0:
            avisos.append(
                ErrorValidacion(
                    carga=carga, fila=f["fila_origen"], campo="naturaleza_cuenta",
                    tipo="aviso",
                    descripcion=(
                        f"La cuenta de gasto '{cuenta.codigo} - {cuenta.nombre}' tiene "
                        "movimiento en el Haber; lo habitual es que los gastos se "
                        "registren en el Debe."
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
        ErrorValidacion.objects.bulk_create(errores_a_guardar)
        ErrorValidacion.objects.create(
            carga=carga, fila=0, campo="archivo",
            descripcion="El archivo no contiene ninguna transacción para importar.",
        )
        carga.total_registros = 0
        carga.registros_validos = 0
        carga.registros_con_error = 0
        carga.registros_con_aviso = 0
        carga.estado = "con_errores"
        carga.save()
        return

    # Todo lo que sigue se guarda en una sola transacción de base de datos: si falla
    # cualquier paso, no queda nada guardado a medias.
    with transaction.atomic():
        _guardar_resultado(
            carga, filas_validas, errores_a_guardar, total,
            nombre_por_codigo, primera_fila_por_codigo,
        )


def _guardar_resultado(
    carga, filas_validas, errores_a_guardar, total, nombre_por_codigo, primera_fila_por_codigo
):
    # Cuentas contables: una consulta y, si hace falta, un bulk_create.
    codigos_necesarios = set(nombre_por_codigo)
    cuentas_por_codigo = {
        c.codigo: c for c in CuentaContable.objects.filter(codigo__in=codigos_necesarios)
    }
    codigos_nuevos = codigos_necesarios - cuentas_por_codigo.keys()
    if codigos_nuevos:
        CuentaContable.objects.bulk_create(
            [
                CuentaContable(
                    codigo=codigo,
                    nombre=nombre_por_codigo[codigo],
                    tipo=_tipo_por_codigo(codigo),
                )
                for codigo in codigos_nuevos
            ]
        )
        # MySQL no devuelve los id tras bulk_create; se vuelven a consultar.
        for cuenta in CuentaContable.objects.filter(codigo__in=codigos_nuevos):
            cuentas_por_codigo[cuenta.codigo] = cuenta
            errores_a_guardar.append(
                ErrorValidacion(
                    carga=carga,
                    fila=primera_fila_por_codigo[cuenta.codigo],
                    campo="cuenta",
                    tipo="aviso",
                    descripcion=(
                        f"La cuenta '{cuenta.codigo}' no existía en el catálogo y se creó "
                        f"automáticamente como '{cuenta.get_tipo_display()}' según su código; "
                        "verificar su clasificación real."
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
