"""
Procesamiento y validación de archivos de registros contables (RF-01, RF-02).

Lee el archivo asociado a una CargaArchivo con pandas, valida cada fila
(integridad, completitud y consistencia de los campos requeridos) y
guarda los registros válidos en RegistroContable. Las filas con
problemas se registran en ErrorValidacion en lugar de descartarse sin
rastro, para que el auditor pueda revisarlas.
"""
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
    "comprobante": ["comprobante", "nro comprobante", "numero comprobante", "n comprobante"],
}

CAMPOS_REQUERIDOS = ["fecha", "cuenta", "debe", "haber"]


def _normalizar(texto):
    texto = str(texto).strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto


def _mapear_columnas(columnas_archivo):
    """Empareja las columnas reales del archivo con los campos esperados."""
    normalizadas = {_normalizar(c): c for c in columnas_archivo}
    mapeo = {}
    for campo, alias in COLUMNAS_ESPERADAS.items():
        for a in alias:
            if a in normalizadas:
                mapeo[campo] = normalizadas[a]
                break
    return mapeo


def _leer_archivo(carga):
    nombre = carga.archivo.name.lower()
    if nombre.endswith(".csv"):
        return pd.read_csv(carga.archivo.path, dtype=str)
    return pd.read_excel(carga.archivo.path, dtype=str)


def procesar_carga(carga):
    """Procesa el archivo de una CargaArchivo y guarda los resultados."""
    try:
        df = _leer_archivo(carga)
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

    mapeo = _mapear_columnas(df.columns)
    faltantes = [c for c in CAMPOS_REQUERIDOS if c not in mapeo]
    if faltantes:
        ErrorValidacion.objects.create(
            carga=carga,
            fila=0,
            campo="archivo",
            descripcion=(
                "Faltan columnas requeridas en el archivo: " + ", ".join(faltantes)
            ),
        )
        carga.estado = "con_errores"
        carga.total_registros = 0
        carga.save()
        return

    total = 0
    validos = 0
    con_error = 0

    for indice, fila in df.iterrows():
        numero_fila = indice + 2  # +1 por encabezado, +1 porque iterrows empieza en 0
        total += 1
        errores_fila = []

        fecha_raw = fila.get(mapeo["fecha"])
        fecha = pd.to_datetime(fecha_raw, errors="coerce", dayfirst=True)
        if pd.isna(fecha):
            errores_fila.append(("fecha", f"Fecha inválida o vacía: '{fecha_raw}'"))

        codigo_cuenta = str(fila.get(mapeo["cuenta"], "")).strip()
        if not codigo_cuenta or codigo_cuenta.lower() == "nan":
            errores_fila.append(("cuenta", "Código de cuenta vacío"))

        debe = pd.to_numeric(fila.get(mapeo["debe"]), errors="coerce")
        haber = pd.to_numeric(fila.get(mapeo["haber"]), errors="coerce")
        if pd.isna(debe):
            debe = 0
        if pd.isna(haber):
            haber = 0
        if debe == 0 and haber == 0:
            errores_fila.append(("debe/haber", "La fila no tiene monto en Debe ni en Haber"))
        if debe < 0 or haber < 0:
            errores_fila.append(("debe/haber", "Debe y Haber no pueden ser negativos"))

        if errores_fila:
            for campo, descripcion in errores_fila:
                ErrorValidacion.objects.create(
                    carga=carga, fila=numero_fila, campo=campo, descripcion=descripcion
                )
            con_error += 1
            continue

        cuenta, creada = CuentaContable.objects.get_or_create(
            codigo=codigo_cuenta,
            defaults={
                "nombre": str(fila.get(mapeo.get("nombre_cuenta"), codigo_cuenta) or codigo_cuenta),
                "tipo": "activo",
            },
        )
        if creada:
            ErrorValidacion.objects.create(
                carga=carga,
                fila=numero_fila,
                campo="cuenta",
                descripcion=(
                    f"La cuenta '{codigo_cuenta}' no existía en el catálogo y se creó "
                    "automáticamente con tipo 'activo' por defecto; verificar su "
                    "clasificación real."
                ),
            )

        RegistroContable.objects.create(
            carga=carga,
            cuenta=cuenta,
            fecha=fecha.date(),
            numero_comprobante=str(fila.get(mapeo.get("comprobante"), "") or "").strip(),
            glosa=str(fila.get(mapeo.get("glosa"), "") or "").strip(),
            debe=debe,
            haber=haber,
            fila_origen=numero_fila,
        )
        validos += 1

    carga.total_registros = total
    carga.registros_validos = validos
    carga.registros_con_error = con_error
    carga.estado = "validado" if con_error == 0 else "con_errores"
    carga.save()
