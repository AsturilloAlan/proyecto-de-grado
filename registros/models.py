"""
Modelo de datos para la carga y validación de registros contables
(RF-01, RF-02). Diseñado en tercera forma normal: cada tabla depende
únicamente de su propia clave, sin datos repetidos ni dependencias
transitivas entre atributos.
"""
from django.conf import settings
from django.db import models


class EmpresaAuditada(models.Model):
    """Empresa cuyos registros contables se analizan.

    Se modela como entidad propia (en vez de un campo de texto repetido
    en cada carga) para permitir trazabilidad y, en el futuro, extender
    el sistema a más de una empresa auditada.
    """

    nombre = models.CharField(max_length=200)
    nit = models.CharField("NIT", max_length=30, blank=True)

    class Meta:
        verbose_name = "Empresa Auditada"
        verbose_name_plural = "Empresas Auditadas"

    def __str__(self):
        return self.nombre


class Gestion(models.Model):
    """Periodo fiscal analizado (ej. 2022, 2023)."""

    anio = models.PositiveIntegerField("Año", unique=True)

    class Meta:
        verbose_name = "Gestión"
        verbose_name_plural = "Gestiones"
        ordering = ["-anio"]

    def __str__(self):
        return str(self.anio)


class CuentaContable(models.Model):
    """Catálogo de cuentas del libro mayor.

    Se separa de RegistroContable para no repetir el nombre y tipo de
    cuenta en cada transacción (evita anomalías de actualización).
    """

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
    """Representa un lote de importación de registros contables (RF-01).

    Agrupa los registros de un mismo archivo cargado, para trazabilidad
    (quién lo subió, cuándo, de qué empresa/gestión) y para poder
    reportar el resultado de la validación (RF-02) a nivel de lote.
    """

    ESTADO_CHOICES = [
        ("pendiente", "Pendiente"),
        ("validado", "Validado"),
        ("con_errores", "Con errores"),
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
    total_registros = models.PositiveIntegerField(default=0)
    registros_validos = models.PositiveIntegerField(default=0)
    registros_con_error = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Carga de Archivo"
        verbose_name_plural = "Cargas de Archivos"
        ordering = ["-fecha_carga"]

    def __str__(self):
        return f"Carga #{self.pk} - {self.empresa} ({self.gestion})"


class RegistroContable(models.Model):
    """Transacción individual de libro diario / libro mayor.

    Cada registro referencia su cuenta y su carga de origen en lugar de
    duplicar esa información, y guarda la fila original del archivo
    para poder rastrear cualquier registro hasta su fuente.
    """

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


class ErrorValidacion(models.Model):
    """Inconsistencia detectada durante la validación de una carga (RF-02).

    Permite dejar constancia de qué filas del archivo original fallaron
    y por qué, en vez de descartarlas sin rastro.
    """

    carga = models.ForeignKey(
        CargaArchivo, on_delete=models.CASCADE, related_name="errores"
    )
    fila = models.PositiveIntegerField()
    campo = models.CharField(max_length=100, blank=True)
    descripcion = models.CharField(max_length=300)

    class Meta:
        verbose_name = "Error de Validación"
        verbose_name_plural = "Errores de Validación"
        ordering = ["carga", "fila"]

    def __str__(self):
        return f"Carga #{self.carga_id} fila {self.fila}: {self.descripcion}"
