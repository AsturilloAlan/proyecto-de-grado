"""
Modelo de datos para la carga y validación de registros contables
(RF-01, RF-02). Diseñado en tercera forma normal: cada tabla depende
únicamente de su propia clave, sin datos repetidos ni dependencias
transitivas entre atributos.
"""
from datetime import date

from django.conf import settings
from django.db import models


class EmpresaAuditada(models.Model):
    """Empresa cuyos registros contables se analizan.

    Se modela como entidad propia (en vez de un campo de texto repetido
    en cada carga) para permitir trazabilidad y, en el futuro, extender
    el sistema a más de una empresa auditada.
    """

    # Categoría de cierre de gestión según el Servicio de Impuestos
    # Nacionales (SIN, Bolivia): el cierre del año fiscal no es siempre
    # el 31 de diciembre — depende del rubro de la empresa. Se usa para
    # sugerir automáticamente las fechas al crear una gestión (ver
    # `fechas_gestion_para`), sin obligar a escribirlas a mano cada vez.
    # Es independiente del campo "rubro" (texto libre, descriptivo) de
    # abajo: este campo es estructurado a propósito, justamente para
    # poder calcular fechas con él.
    CATEGORIA_CIERRE_CHOICES = [
        ("general", "Comercio, servicios, bancos y seguros — cierra 31 de diciembre"),
        ("industrial", "Industrial o petrolera — cierra 31 de marzo"),
        ("agropecuaria", "Agropecuaria o agroindustrial — cierra 30 de junio"),
        ("minera", "Minera — cierra 30 de septiembre"),
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
        "solo para sugerir las fechas al crear una nueva gestión — siempre "
        "se pueden ajustar a mano si hay una excepción real.",
    )
    contacto_nombre = models.CharField("Nombre del contacto", max_length=150, blank=True)
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
        """Calcula (fecha_inicio, fecha_fin) de la gestión `anio` según la
        categoría de cierre de esta empresa. Para las categorías con cierre
        distinto al 31/12, la gestión "anio" es la que TERMINA en ese año
        (ej. industrial, gestión 2024: 01/04/2023 - 31/03/2024) — así se
        nombra habitualmente en Bolivia, igual que la gestión "general" ya
        se nombra por el año en que transcurre."""
        if self.categoria_cierre == "industrial":
            return date(anio - 1, 4, 1), date(anio, 3, 31)
        if self.categoria_cierre == "agropecuaria":
            return date(anio - 1, 7, 1), date(anio, 6, 30)
        if self.categoria_cierre == "minera":
            return date(anio - 1, 10, 1), date(anio, 9, 30)
        return date(anio, 1, 1), date(anio, 12, 31)


class Gestion(models.Model):
    """Periodo fiscal analizado (ej. 2022, 2023).

    En Bolivia el cierre de la gestión fiscal no siempre coincide con el
    año calendario: varía según la actividad económica de la empresa,
    conforme la Resolución Normativa de Directorio vigente del Servicio
    de Impuestos Nacionales (SIN): 31 de marzo para industriales y
    petroleras, 30 de junio para agropecuarias/agroindustriales, 30 de
    septiembre para mineras, y 31 de diciembre para bancos, seguros,
    comercio y servicios. Por eso se guardan las fechas reales de inicio
    y fin de cada gestión (decididas por quien la registra, según el
    rubro del cliente) en vez de asumir siempre el año calendario.

    El año NO es único a propósito: como distintas empresas clientes
    pueden tener distinto rubro (y por lo tanto distinto cierre), puede
    existir más de una "gestión 2024" con rangos de fechas distintos
    (ej. una para clientes industriales, otra para clientes de
    servicios). Lo que sí no puede repetirse es la combinación exacta de
    año + mismas fechas, para evitar duplicados sin sentido.
    """

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
        # "Con observaciones": la carga sí se guardó (la mayoría de las filas
        # quedaron bien), pero algunas puntuales se rechazaron y conviene
        # revisarlas — distinto de "Con errores", reservado para cuando no
        # se pudo guardar nada en absoluto (ver `procesar_carga`). Antes se
        # usaba "Con errores" para ambos casos por igual, lo que hacía ver
        # como un fracaso total una carga de miles de filas con solo una
        # rechazada.
        ("con_observaciones", "Cargado con pendientes"),
        ("con_errores", "Con errores"),
        # Vía sancionada para corregir un error humano al elegir la empresa
        # o la gestión al cargar (ej. gestión 2025 en vez de 2022): en un
        # sistema de auditoría no tiene sentido "editar" en silencio una
        # carga ya procesada (los avisos de fecha ya se calcularon contra
        # la gestión original, y quedarían desactualizados). En cambio, se
        # anula (con motivo obligatorio, quién y cuándo — ver
        # `carga_anular` en views.py) y se sube de nuevo el archivo con los
        # datos correctos: la carga anulada NUNCA se borra ni se oculta,
        # queda como constancia de que existió y por qué se descartó.
        ("anulada", "Anulada"),
    ]

    # A qué tipo de libro contable corresponde el archivo, detectado
    # automáticamente según cómo vino estructurado (no lo elige quien
    # sube el archivo): un PDF siempre es Libro Diario (es el único
    # formato de PDF que se soporta); en Excel/CSV depende de si trae
    # una columna "cuenta" por fila (tabla plana, estilo Libro Diario)
    # o las transacciones agrupadas en bloques por cuenta (estilo Libro
    # Mayor) — ver `_procesar_pdf`/`_procesar_hoja_calculo` en services.py.
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
    # Filas que SÍ se guardaron pero generaron un ErrorValidacion tipo
    # "aviso" (no rechaza la fila, solo la señala para revisión — ver
    # ErrorValidacion). Se guarda aparte de registros_con_error porque son
    # cosas distintas: error = fila rechazada, aviso = fila guardada con
    # algo puntual a revisar. Antes solo se mostraba el conteo de errores;
    # esto permite mostrar los tres números (válidos/avisos/errores) sin
    # tener que contar los ErrorValidacion en cada request.
    registros_con_aviso = models.PositiveIntegerField(default=0)

    # Cuando una carga queda "con_observaciones", el auditor o
    # administrador puede revisarla y darla por válida definitivamente
    # (ver vista `carga_confirmar_validacion`) en vez de que quede
    # marcada como pendiente para siempre. Se deja constancia de quién y
    # cuándo, porque es información sensible (RF de auditoría): no basta
    # con que el estado cambie solo, sin rastro de quién lo aprobó.
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cargas_revisadas",
        null=True,
        blank=True,
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)

    # Anulación (ver ESTADO_CHOICES["anulada"] arriba): quién, cuándo y por
    # qué se descartó esta carga. `motivo_anulacion` es obligatorio a nivel
    # de formulario (no de base de datos, para no romper cargas viejas sin
    # anular) — es lo que reemplaza a un simple "borrar y listo": deja
    # constancia legible del motivo, en vez de solo un registro genérico
    # de que "algo cambió".
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


class HistorialCambio(models.Model):
    """Pista de auditoría: deja constancia de cada creación o edición sobre
    información sensible del sistema (por ahora, empresas auditadas).

    No se sobrescribe nunca — cada cambio agrega una fila nueva, campo por
    campo, para poder reconstruir después quién modificó qué, cuándo y con
    qué valor anterior. Es un control interno básico esperable en un
    sistema de apoyo a auditoría.
    """

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
    """Inconsistencia o aviso detectado durante la validación de una carga
    (RF-02).

    Permite dejar constancia de qué filas del archivo original fallaron
    y por qué, en vez de descartarlas sin rastro. También se usa para
    avisos informativos que no rechazan la fila (ej. una cuenta contable
    que no existía y se creó automáticamente) — el campo `tipo` distingue
    ambos casos para no mostrarlos igual en la interfaz.
    """

    TIPO_CHOICES = [
        ("error", "Error"),
        ("aviso", "Aviso"),
    ]

    carga = models.ForeignKey(
        CargaArchivo, on_delete=models.CASCADE, related_name="errores"
    )
    fila = models.PositiveIntegerField()
    campo = models.CharField(max_length=100, blank=True)
    descripcion = models.CharField(max_length=300)
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default="error")

    class Meta:
        verbose_name = "Error de Validación"
        verbose_name_plural = "Errores de Validación"
        ordering = ["carga", "fila"]

    def __str__(self):
        return f"Carga #{self.carga_id} fila {self.fila}: {self.descripcion}"
