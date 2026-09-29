"""Modelo de datos para la carga y validación de registros contables (RF-01, RF-02)."""
from datetime import date

from django.conf import settings
from django.db import models


class EmpresaAuditada(models.Model):
    """Empresa cuyos registros contables se analizan."""

    # Categoría de cierre de gestión según el SIN: el año fiscal no siempre cierra el 31
    # de diciembre.
    CATEGORIA_CIERRE_CHOICES = [
        ("general", "Comercio, servicios, bancos y seguros (cierre 31 de diciembre)"),
        ("industrial", "Industrial o petrolera (cierre 31 de marzo)"),
        ("agropecuaria", "Agropecuaria o agroindustrial (cierre 30 de junio)"),
        ("minera", "Minera (cierre 30 de septiembre)"),
    ]

    nombre = models.CharField(max_length=200)
    nit = models.CharField("NIT", max_length=30, blank=True)
    rubro = models.CharField(
        "Rubro / sector económico",
        max_length=150,
        blank=True,
        help_text="Ayuda a interpretar mejor los resultados del análisis, "
        "ya que el comportamiento contable normal varía según el rubro.",
    )
    categoria_cierre = models.CharField(
        "Categoría de cierre de gestión (SIN)",
        max_length=20,
        choices=CATEGORIA_CIERRE_CHOICES,
        default="general",
        help_text="Define en qué mes cierra el año fiscal de esta empresa. Se usa "
        "solo para sugerir las fechas al crear una nueva gestión; siempre "
        "se pueden ajustar a mano si hay una excepción real.",
    )
    contacto_nombre = models.CharField("Nombre del representante legal", max_length=150, blank=True)
    contacto_email = models.EmailField("Correo del contacto", blank=True)
    contacto_telefono = models.CharField("Teléfono del contacto", max_length=30, blank=True)
    fecha_registro = models.DateTimeField("Fecha de registro", auto_now_add=True)

    class Meta:
        verbose_name = "Empresa Auditada"
        verbose_name_plural = "Empresas Auditadas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def fechas_gestion_para(self, anio):
        """Calcula (fecha_inicio, fecha_fin) de la gestión `anio` según la categoría
        de cierre de esta empresa.
        """
        if self.categoria_cierre == "industrial":
            return date(anio - 1, 4, 1), date(anio, 3, 31)
        if self.categoria_cierre == "agropecuaria":
            return date(anio - 1, 7, 1), date(anio, 6, 30)
        if self.categoria_cierre == "minera":
            return date(anio - 1, 10, 1), date(anio, 9, 30)
        return date(anio, 1, 1), date(anio, 12, 31)


class Gestion(models.Model):
    """Periodo fiscal analizado."""

    anio = models.PositiveIntegerField("Año")
    fecha_inicio = models.DateField("Fecha de inicio de la gestión")
    fecha_fin = models.DateField("Fecha de fin de la gestión")

    class Meta:
        verbose_name = "Gestión"
        verbose_name_plural = "Gestiones"
        ordering = ["-anio"]
        constraints = [
            models.UniqueConstraint(
                fields=["anio", "fecha_inicio", "fecha_fin"],
                name="gestion_periodo_unico",
            )
        ]

    def __str__(self):
        return f"{self.anio} ({self.fecha_inicio:%d/%m/%Y} - {self.fecha_fin:%d/%m/%Y})"


class CuentaContable(models.Model):
    """Catálogo de cuentas del libro mayor."""

    TIPO_CHOICES = [
        ("activo", "Activo"),
        ("pasivo", "Pasivo"),
        ("patrimonio", "Patrimonio"),
        ("ingreso", "Ingreso"),
        ("gasto", "Gasto"),
    ]

    codigo = models.CharField(max_length=30, unique=True)
    nombre = models.CharField(max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)

    class Meta:
        verbose_name = "Cuenta Contable"
        verbose_name_plural = "Cuentas Contables"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class CargaArchivo(models.Model):
    """Representa un lote de importación de registros contables (RF-01)."""

    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("validado", "Validado"),
        # Cargado con pendientes: se guardaron filas, pero hay rechazos o avisos por
        # revisar.
        ("con_observaciones", "Cargado con pendientes"),
        ("con_errores", "Con errores"),
        # Anulada: la carga no se edita ni se borra; se anula con motivo y se vuelve a
        # subir.
        ("anulada", "Anulada"),
    ]

    # Tipo de libro detectado según la estructura del archivo.
    FORMATO_CHOICES = [
        ("diario_pdf", "Libro Diario (PDF)"),
        ("diario_plano", "Libro Diario (Excel/CSV)"),
        ("mayor", "Libro Mayor (Excel/CSV)"),
    ]

    archivo = models.FileField(upload_to="cargas/%Y/%m/")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cargas"
    )
    empresa = models.ForeignKey(
        EmpresaAuditada, on_delete=models.PROTECT, related_name="cargas"
    )
    gestion = models.ForeignKey(
        Gestion, on_delete=models.PROTECT, related_name="cargas"
    )
    fecha_carga = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default="pendiente"
    )
    formato_detectado = models.CharField(
        "Tipo de libro detectado",
        max_length=20,
        choices=FORMATO_CHOICES,
        blank=True,
    )
    total_registros = models.PositiveIntegerField(default=0)
    registros_validos = models.PositiveIntegerField(default=0)
    registros_con_error = models.PositiveIntegerField(default=0)
    # Filas que SÍ se guardaron pero generaron un ErrorValidacion tipo "aviso".
    registros_con_aviso = models.PositiveIntegerField(default=0)

    # Quién confirmó la carga y cuándo.
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cargas_revisadas",
        null=True,
        blank=True,
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)

    # Anulación: quién, cuándo y por qué se descartó esta carga.
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cargas_anuladas",
        null=True,
        blank=True,
    )
    fecha_anulacion = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.TextField("Motivo de la anulación", blank=True)

    class Meta:
        verbose_name = "Carga de Archivo"
        verbose_name_plural = "Cargas de Archivos"
        ordering = ["-fecha_carga"]

    def __str__(self):
        return f"Carga #{self.pk} - {self.empresa} ({self.gestion})"

    # Dos lecturas del estado, calculadas a partir de `estado` y los contadores:
    # si el archivo entró completo (importación) y en qué punto va el auditor (revisión).
    ETIQUETAS_IMPORTACION = {
        "procesando": "Procesando",
        "completa": "Completa",
        "parcial": "Parcial",
        "fallida": "Fallida",
    }
    ETIQUETAS_REVISION = {
        "en_revision": "En revisión",
        "por_confirmar": "Por confirmar",
        "validada": "Validada",
        "anulada": "Anulada",
        "no_aplica": "Sin datos para revisar",
    }

    @property
    def estado_importacion(self):
        if self.estado == "pendiente":
            return "procesando"
        if self.registros_validos == 0:
            return "fallida"
        if self.registros_con_error:
            return "parcial"
        return "completa"

    @property
    def estado_importacion_display(self):
        return self.ETIQUETAS_IMPORTACION[self.estado_importacion]

    @property
    def avisos_pendientes(self):
        # Las vistas de listado lo anotan en la consulta para no contar carga por carga.
        valor = getattr(self, "avisos_pendientes_anotado", None)
        if valor is None:
            valor = self.errores.filter(tipo="aviso", revisado=False).count()
        return valor

    @property
    def estado_revision_carga(self):
        if self.estado == "anulada":
            return "anulada"
        if self.estado == "validado":
            return "validada"
        if self.estado_importacion in ("procesando", "fallida"):
            return "no_aplica"
        return "en_revision" if self.avisos_pendientes else "por_confirmar"

    @property
    def estado_revision_carga_display(self):
        return self.ETIQUETAS_REVISION[self.estado_revision_carga]


class RegistroContable(models.Model):
    """Transacción individual de libro diario / libro mayor."""

    carga = models.ForeignKey(
        CargaArchivo, on_delete=models.CASCADE, related_name="registros"
    )
    cuenta = models.ForeignKey(
        CuentaContable, on_delete=models.PROTECT, related_name="registros"
    )
    fecha = models.DateField()
    numero_comprobante = models.CharField(max_length=50, blank=True)
    glosa = models.CharField(max_length=300, blank=True)
    debe = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    haber = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text=(
            "Saldo tal como venía en el archivo original (columna 'Saldo' del "
            "Libro Mayor), guardado como referencia. No se calcula ni se "
            "valida; el sistema no conoce la naturaleza deudora/acreedora "
            "de cada cuenta, así que no intenta recalcularlo."
        ),
    )
    fila_origen = models.PositiveIntegerField(
        help_text="Número de fila en el archivo original, para trazabilidad."
    )

    class Meta:
        verbose_name = "Registro Contable"
        verbose_name_plural = "Registros Contables"
        ordering = ["fecha"]
        indexes = [
            models.Index(fields=["fecha"]),
            models.Index(fields=["cuenta"]),
        ]

    def __str__(self):
        return f"{self.fecha} - {self.cuenta} - D:{self.debe} H:{self.haber}"


class HistorialCambio(models.Model):
    """Pista de auditoría: cada creación o edición de información sensible."""

    ACCION_CHOICES = [
        ("creacion", "Creación"),
        ("edicion", "Edición"),
    ]

    modelo = models.CharField(
        max_length=100, help_text="Nombre del modelo afectado, ej. EmpresaAuditada."
    )
    objeto_id = models.PositiveIntegerField()
    objeto_descripcion = models.CharField(
        max_length=200,
        blank=True,
        help_text="Representación legible del objeto al momento del cambio.",
    )
    accion = models.CharField(max_length=20, choices=ACCION_CHOICES)
    campo = models.CharField(max_length=100, blank=True)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cambios_realizados",
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Historial de Cambio"
        verbose_name_plural = "Historial de Cambios"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.modelo} #{self.objeto_id} - {self.get_accion_display()} ({self.fecha:%d/%m/%Y %H:%M})"


class ErrorValidacion(models.Model):
    """Inconsistencia o aviso detectado durante la validación de una carga (RF-02)."""

    TIPO_CHOICES = [
        ("error", "Error"),
        ("aviso", "Aviso"),
    ]

    # Decisión del auditor sobre un aviso: válido (falso positivo) u observado
    # (hallazgo).
    ESTADO_REVISION_CHOICES = [
        ("pendiente", "Pendiente"),
        ("valido", "Válido (falso positivo)"),
        ("observado", "Observado (hallazgo confirmado)"),
    ]
    estado_revision = models.CharField(
        max_length=10, choices=ESTADO_REVISION_CHOICES, default="pendiente"
    )

    carga = models.ForeignKey(
        CargaArchivo, on_delete=models.CASCADE, related_name="errores"
    )
    fila = models.PositiveIntegerField()
    campo = models.CharField(max_length=100, blank=True)
    descripcion = models.CharField(max_length=300)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default="error")

    # Solo aplica a tipo="aviso".
    revisado = models.BooleanField(default=False)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="avisos_revisados",
        null=True,
        blank=True,
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    # Justificación opcional de por qué se dan por válidos los avisos seleccionados.
    comentario_revision = models.TextField(blank=True)

    class Meta:
        verbose_name = "Error de Validación"
        verbose_name_plural = "Errores de Validación"
        ordering = ["carga", "fila"]

    def __str__(self):
        return f"Carga #{self.carga_id} fila {self.fila}: {self.descripcion}"
