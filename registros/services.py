"""
Procesamiento y validación de archivos de registros contables (RF-01, RF-02).

Soporta dos formatos de archivo:

1. Tabla plana: cada fila trae su propia columna "cuenta" (código de la
   cuenta contable). Es el formato más simple, útil para pruebas o
   exportaciones ya limpias.

2. Libro Mayor agrupado por cuenta (el formato real que exportan los
   sistemas contables, ej. el Libro Mayor de una empresa auditada): el
   archivo trae varias filas de título antes del encabezado real
   (nombre de la empresa, NIT, periodo...), y las transacciones vienen
   agrupadas en bloques — una fila de encabezado con el código y nombre
   de la cuenta (ej. "1-1-1-01-01 CAJA MONEDA NACIONAL"), seguida de sus
   transacciones, hasta la siguiente cuenta. También trae filas de
   subtotal intercaladas ("Total 01/2023", "TOTAL CUENTA 1-1-1-01-01")
   que no son transacciones y se deben ignorar.

En ambos casos, las filas válidas se guardan en RegistroContable y las
filas con problemas se registran en ErrorValidacion en lugar de
descartarse sin rastro, para que el auditor pueda revisarlas.
"""
import csv
import re
import unicodedata

import pandas as pd

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
    "comprobante": [
        "comprobante",
        "nro comprobante",
        "numero comprobante",
        "n comprobante",
        "numero",
    ],
}

# Solo estas son estrictamente obligatorias en el encabezado. "cuenta" es
# opcional: si no viene como columna, se asume el formato agrupado por
# cuenta (bloques), donde la cuenta se extrae de las filas de título.
CAMPOS_REQUERIDOS = ["fecha", "debe", "haber"]

# Cuántas filas iniciales se revisan buscando la fila real de encabezados
# (las filas de título de la empresa/periodo van antes).
FILAS_BUSQUEDA_ENCABEZADO = 30

# Código de cuenta al inicio de una fila de bloque, ej. "1-1-1-01-01 CAJA...".
PATRON_CODIGO_CUENTA = re.compile(r"^([0-9][0-9\-\.]*)\s+(.+)$")

# Filas de subtotal a ignorar: "Total 01/2023", "TOTAL CUENTA 1-1-1-01-01".
PATRON_SUBTOTAL = re.compile(r"^\s*total\b", re.IGNORECASE)


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
    """Lee un CSV fila por fila (sin asumir que todas tienen el mismo
    número de columnas) y rellena las filas cortas con None.

    Los libros mayores reales suelen traer filas de título con una sola
    celda (ej. el nombre de la empresa) mezcladas con filas de datos de
    varias columnas; el parser rápido de pandas (`read_csv`) rechaza esos
    archivos por "número de campos inconsistente", así que se arma el
    DataFrame a mano con el módulo `csv` de la librería estándar.
    """
    with open(ruta, newline="", encoding="utf-8-sig") as archivo:
        filas = list(csv.reader(archivo))
    ancho = max((len(f) for f in filas), default=0)
    filas_parejas = [fila + [None] * (ancho - len(fila)) for fila in filas]
    return pd.DataFrame(filas_parejas)


def _leer_filas_crudas(carga):
    """Lee el archivo completo sin asumir en qué fila están los
    encabezados, porque los libros mayores reales traen varias filas de
    título (empresa, NIT, periodo) antes de la fila real de columnas."""
    nombre = carga.archivo.name.lower()
    if nombre.endswith(".csv"):
        return _leer_csv_con_filas_irregulares(carga.archivo.path)
    return pd.read_excel(carga.archivo.path, dtype=str, header=None, sheet_name=0)


def _ubicar_encabezado(df):
    """Busca, entre las primeras filas del archivo, la que realmente
    contiene los nombres de columna (fecha, debe, haber...). Devuelve
    (índice_de_fila, mapeo) o (None, {}) si no la encuentra."""
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


def _texto_no_vacio_de_fila(fila):
    """Concatena las celdas no vacías de una fila de título/bloque en un
    solo texto (normalmente solo la primera celda trae algo)."""
    partes = [str(v).strip() for v in fila.tolist() if not _es_vacio(v)]
    return " ".join(partes).strip()


def procesar_carga(carga):
    """Procesa el archivo de una CargaArchivo y guarda los resultados.

    Para archivos grandes (un libro mayor real puede tener más de 20 mil
    filas), guardar de a una fila a la vez es demasiado lento — se
    recolectan las filas válidas y los errores/avisos en memoria mientras
    se recorre el archivo, y se guardan al final en bloque
    (`bulk_create`), en vez de hacer una consulta a la base de datos por
    cada fila.
    """
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
        return

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
        return

    tiene_columna_cuenta = "cuenta" in mapeo

    total = 0
    filas_validas = []  # datos ya validados, listos para RegistroContable
    errores_a_guardar = []  # instancias de ErrorValidacion sin guardar todavía
    cuenta_actual = None  # (codigo, nombre) — solo se usa en el formato agrupado
    nombre_por_codigo = {}  # primer nombre visto para cada código nuevo
    primera_fila_por_codigo = {}  # primera fila donde apareció cada código nuevo

    for indice in range(fila_encabezado + 1, len(df)):
        fila = df.iloc[indice]
        numero_fila = indice + 1  # fila real del archivo (1 = primera fila)

        fecha_raw = _valor(fila, mapeo, "fecha")
        fecha = pd.to_datetime(fecha_raw, errors="coerce", dayfirst=True)

        if not tiene_columna_cuenta and pd.isna(fecha):
            # No es una fila de transacción: puede ser un separador en
            # blanco, un subtotal, o el encabezado de un nuevo bloque de
            # cuenta. Ninguna de las tres cuenta como fila de datos.
            texto = _texto_no_vacio_de_fila(fila)
            if not texto or PATRON_SUBTOTAL.match(texto):
                continue
            coincidencia = PATRON_CODIGO_CUENTA.match(texto)
            if coincidencia:
                cuenta_actual = (coincidencia.group(1).strip(), coincidencia.group(2).strip())
            else:
                # Bloque sin un código reconocible al inicio: se usa el
                # texto completo como nombre y como código (mejor dejar
                # constancia que descartarlo en silencio).
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

        debe = pd.to_numeric(_valor(fila, mapeo, "debe"), errors="coerce")
        haber = pd.to_numeric(_valor(fila, mapeo, "haber"), errors="coerce")
        # pandas devuelve tipos numpy (numpy.int64/float64), que Django no
        # puede guardar directo en un DecimalField — hay que convertirlos
        # a float (tipo nativo de Python) antes de usarlos.
        debe = 0.0 if pd.isna(debe) else float(debe)
        haber = 0.0 if pd.isna(haber) else float(haber)
        if debe == 0 and haber == 0:
            errores_fila.append(("debe/haber", "La fila no tiene monto en Debe ni en Haber"))
        if debe < 0 or haber < 0:
            errores_fila.append(("debe/haber", "Debe y Haber no pueden ser negativos"))

        if errores_fila:
            for campo, descripcion in errores_fila:
                errores_a_guardar.append(
                    ErrorValidacion(
                        carga=carga, fila=numero_fila, campo=campo, descripcion=descripcion
                    )
                )
            continue

        nombre_por_codigo.setdefault(codigo_cuenta, nombre_cuenta or codigo_cuenta)
        primera_fila_por_codigo.setdefault(codigo_cuenta, numero_fila)
        filas_validas.append(
            {
                "codigo_cuenta": codigo_cuenta,
                "fecha": fecha.date(),
                "comprobante": str(_valor(fila, mapeo, "comprobante") or "").strip(),
                "glosa": str(_valor(fila, mapeo, "glosa") or "").strip(),
                "debe": debe,
                "haber": haber,
                "fila_origen": numero_fila,
            }
        )

    # Cuentas contables: se resuelven todas de una vez (una consulta y,
    # si hace falta, un bulk_create), en vez de una consulta por fila.
    codigos_necesarios = set(nombre_por_codigo)
    cuentas_por_codigo = {
        c.codigo: c for c in CuentaContable.objects.filter(codigo__in=codigos_necesarios)
    }
    codigos_nuevos = codigos_necesarios - cuentas_por_codigo.keys()
    if codigos_nuevos:
        CuentaContable.objects.bulk_create(
            [
                CuentaContable(
                    codigo=codigo, nombre=nombre_por_codigo[codigo], tipo="activo"
                )
                for codigo in codigos_nuevos
            ]
        )
        # MySQL (a diferencia de PostgreSQL/SQLite) no devuelve los id
        # generados en bulk_create(); hay que volver a consultarlas para
        # tener las instancias con su pk real antes de usarlas como FK.
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
                        "automáticamente con tipo 'activo' por defecto; verificar su "
                        "clasificación real."
                    ),
                )
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
            fila_origen=f["fila_origen"],
        )
        for f in filas_validas
    ]
    RegistroContable.objects.bulk_create(registros, batch_size=1000)
    ErrorValidacion.objects.bulk_create(errores_a_guardar, batch_size=1000)

    validos = len(filas_validas)
    con_error = total - validos
    carga.total_registros = total
    carga.registros_validos = validos
    carga.registros_con_error = con_error
    carga.estado = "validado" if con_error == 0 else "con_errores"
    carga.save()
