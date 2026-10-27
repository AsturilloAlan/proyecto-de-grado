"""Resultados del modelo One-Class SVM sobre cada carga (RF-03, RF-04; actividad 14)."""
import json

from django.conf import settings
from django.db import models

from registros.models import CargaArchivo, RegistroContable


# Revisión del auditor sobre una atípica:
# - Observada ("relevante"): es un hallazgo; pasa al papel de trabajo.
# - En seguimiento: la señal fue útil pero requiere revisar otros documentos o cuentas.
# - Válida ("no_relevante"): revisada, sin observación.
# Para medir el modelo, una señal es útil si quedó observada o en seguimiento.
REVISIONES_UTILES = ("relevante", "seguimiento")


class AnalisisCarga(models.Model):
    """Ejecución del modelo sobre una carga. Una por carga; se rehace al volver a analizar."""

    ESTADO_CHOICES = [
        ("en_cola", "En cola"),
        ("procesando", "Analizando"),
        ("completado", "Completado"),
        ("sin_modelo", "Sin modelo entrenado"),
        ("error", "Error"),
    ]

    carga = models.OneToOneField(CargaArchivo, on_delete=models.CASCADE, related_name="analisis")
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default="en_cola")
    algoritmo = models.CharField(max_length=50, blank=True)
    configuracion = models.CharField("Configuración del modelo", max_length=100, blank=True)
    fecha_modelo = models.CharField("Fecha de entrenamiento del modelo", max_length=30, blank=True)
    total_registros = models.PositiveIntegerField(default=0)
    total_atipicas = models.PositiveIntegerField(default=0)
    segundos = models.FloatField(default=0)
    mensaje = models.TextField(blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="analisis_solicitados",
    )
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    inicio = models.DateTimeField(null=True, blank=True)
    fecha_fin = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Análisis de carga"
        verbose_name_plural = "Análisis de cargas"

    def __str__(self):
        return f"Análisis de la carga #{self.carga_id} ({self.get_estado_display()})"

    @property
    def en_curso(self):
        return self.estado in ("en_cola", "procesando")

    @property
    def porcentaje_atipicas(self):
        if not self.total_registros:
            return 0
        return self.total_atipicas * 100 / self.total_registros

    def metricas_validacion(self, primeras=20):
        """Validación preliminar (actividad 13): qué tan seguido el auditor confirma como
        relevante lo que el modelo señala. Se calcula sobre las transacciones atípicas.
        """
        atipicas = self.resultados.filter(atipica=True)
        revisadas = atipicas.exclude(revision="pendiente")
        relevantes = revisadas.filter(revision__in=REVISIONES_UTILES).count()
        total_revisadas = revisadas.count()
        top_ids = list(atipicas.order_by("-nivel", "registro__fila_origen")
                       .values_list("id", flat=True)[:primeras])
        top = atipicas.filter(id__in=top_ids).exclude(revision="pendiente")
        top_revisadas = top.count()
        top_relevantes = top.filter(revision__in=REVISIONES_UTILES).count()
        return {
            "atipicas": atipicas.count(),
            "revisadas": total_revisadas,
            # "relevantes": señales útiles para la auditoría (observadas + en seguimiento).
            "relevantes": relevantes,
            "observadas": revisadas.filter(revision="relevante").count(),
            "seguimiento": revisadas.filter(revision="seguimiento").count(),
            "no_relevantes": total_revisadas - relevantes,
            "pendientes": atipicas.count() - total_revisadas,
            "precision": relevantes * 100 / total_revisadas if total_revisadas else None,
            "primeras": min(primeras, len(top_ids)),
            "primeras_revisadas": top_revisadas,
            "primeras_relevantes": top_relevantes,
            "precision_primeras": (top_relevantes * 100 / top_revisadas) if top_revisadas else None,
        }


class ResultadoTransaccion(models.Model):
    """Puntaje del modelo para un registro contable de la carga."""

    analisis = models.ForeignKey(AnalisisCarga, on_delete=models.CASCADE, related_name="resultados")
    registro = models.OneToOneField(
        RegistroContable, on_delete=models.CASCADE, related_name="resultado_modelo"
    )
    puntaje = models.FloatField()
    # Nivel de anomalía de 0 a 1, comparable entre cargas.
    nivel = models.FloatField()
    atipica = models.BooleanField(default=False)
    # Por qué es atípica: JSON con hasta 3 motivos {"variable", "aporte" (%), "texto"};
    # el primero es el motivo principal.
    motivos = models.TextField(blank=True)
    # Tipo del motivo principal (clave de analisis.explicacion.ETIQUETAS), para filtrar.
    motivo_principal = models.CharField(max_length=30, blank=True, db_index=True)

    # Validación preliminar del auditor sobre lo que señaló el modelo (actividad 13).
    REVISION_CHOICES = [
        ("pendiente", "Pendiente"),
        # Claves internas conservadas para no perder las revisiones ya hechas.
        ("relevante", "Observada"),
        ("seguimiento", "En seguimiento"),
        ("no_relevante", "Válida"),
    ]
    revision = models.CharField(max_length=12, choices=REVISION_CHOICES, default="pendiente")
    comentario_revision = models.CharField("Comentario", max_length=300, blank=True)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="atipicas_revisadas",
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Resultado por transacción"
        verbose_name_plural = "Resultados por transacción"
        indexes = [
            models.Index(fields=["analisis", "atipica", "nivel"]),
            models.Index(fields=["analisis", "atipica", "revision"], name="resultado_revision_idx"),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(nivel__gte=0) & models.Q(nivel__lte=1),
                                   name="resultado_nivel_entre_0_y_1"),
        ]

    def __str__(self):
        return f"Fila {self.registro.fila_origen} de la carga #{self.analisis.carga_id}"

    @property
    def nivel_texto(self):
        if self.nivel >= 0.9:
            return "Muy alto"
        if self.nivel >= 0.7:
            return "Alto"
        return "Medio"

    @property
    def lista_motivos(self):
        try:
            return json.loads(self.motivos) if self.motivos else []
        except ValueError:
            return []
