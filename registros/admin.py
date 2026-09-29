from django.contrib import admin

from .models import (
    CargaArchivo,
    CuentaContable,
    EmpresaAuditada,
    ErrorValidacion,
    Gestion,
    RegistroContable,
)


@admin.register(EmpresaAuditada)
class EmpresaAuditadaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "nit")


@admin.register(Gestion)
class GestionAdmin(admin.ModelAdmin):
    list_display = ("anio", "fecha_inicio", "fecha_fin")


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "tipo")
    search_fields = ("codigo", "nombre")
    list_filter = ("tipo",)


@admin.register(CargaArchivo)
class CargaArchivoAdmin(admin.ModelAdmin):
    """Solo lectura y sin borrado: una carga es evidencia de auditoría."""

    list_display = (
        "id",
        "empresa",
        "gestion",
        "usuario",
        "fecha_carga",
        "estado",
        "formato_detectado",
        "total_registros",
        "registros_validos",
        "registros_con_error",
    )
    list_filter = ("estado", "gestion", "empresa")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RegistroContable)
class RegistroContableAdmin(admin.ModelAdmin):
    """Solo lectura: las transacciones importadas no se editan a mano."""

    list_display = ("fecha", "cuenta", "debe", "haber", "carga")
    list_filter = ("cuenta__tipo", "carga__gestion")
    search_fields = ("glosa", "numero_comprobante")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ErrorValidacion)
class ErrorValidacionAdmin(admin.ModelAdmin):
    """Solo lectura: las decisiones se gestionan desde el detalle de la carga."""

    list_display = ("carga", "fila", "campo", "tipo", "estado_revision", "descripcion")
    list_filter = ("tipo", "estado_revision")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
