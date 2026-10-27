"""Reportes (RF-05, RF-08): constancia de cada reporte emitido.

Cada vez que alguien abre un reporte queda registrado quién lo emitió, cuándo, de qué
empresa y gestión (y de qué carga, si fue de una sola), con qué filtros y con qué cifras.
Así se puede demostrar qué información tenía el auditor en el momento en que la usó,
aunque después la revisión avance y el reporte cambie.
"""
from django.conf import settings
from django.db import models

from registros.models import CargaArchivo, EmpresaAuditada, Gestion


class ReporteGenerado(models.Model):
    TIPO_CHOICES = [
        ("atipicas", "Transacciones atípicas"),
        ("observaciones", "Transacciones observadas"),
        ("hallazgos", "Hallazgos de auditoría"),
        ("comparativo", "Comparativo de gestiones"),
    ]
    FORMATO_CHOICES = [
        ("pantalla", "En pantalla"),
        ("pdf", "PDF"),
        ("excel", "Excel"),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    empresa = models.ForeignKey(
        EmpresaAuditada, on_delete=models.PROTECT, related_name="reportes", null=True)
    gestion = models.ForeignKey(Gestion, on_delete=models.PROTECT, related_name="reportes", null=True)
    # Vacío cuando el reporte consolida todas las cargas válidas de la gestión.
    carga = models.ForeignKey(
        CargaArchivo, on_delete=models.CASCADE, related_name="reportes", null=True, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reportes_generados"
    )
    fecha = models.DateTimeField(auto_now_add=True)
    formato = models.CharField(max_length=10, choices=FORMATO_CHOICES, default="pantalla")
    filtros = models.CharField(max_length=200, blank=True)
    # Cifras al momento de emitir.
    total_listado = models.PositiveIntegerField(default=0)
    total_atipicas = models.PositiveIntegerField(default=0)
    revisadas = models.PositiveIntegerField(default=0)
    relevantes = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Reporte generado"
        verbose_name_plural = "Reportes generados"
        ordering = ["-fecha"]
        indexes = [models.Index(fields=["empresa", "gestion", "tipo"], name="reporte_alcance_idx")]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.alcance} · {self.fecha:%d/%m/%Y %H:%M}"

    @property
    def alcance(self):
        base = f"{self.empresa} · gestión {self.gestion.anio}" if self.gestion_id else str(self.empresa)
        return f"{base} · carga #{self.carga_id}" if self.carga_id else f"{base} (consolidado)"
