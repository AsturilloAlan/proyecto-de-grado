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
    list_display = (
        "id",
        "empresa",
        "gestion",
        "usuario",
        "fecha_carga",
        "estado",
        "total_registros",
        "registros_validos",
        "registros_con_error",
    )
    list_filter = ("estado", "gestion", "empresa")


@admin.register(RegistroContable)
class RegistroContableAdmin(admin.ModelAdmin):
    list_display = ("fecha", "cuenta", "debe", "haber", "carga")
    list_filter = ("cuenta__tipo", "carga__gestion")
    search_fields = ("glosa", "numero_comprobante")


@admin.register(ErrorValidacion)
class ErrorValidacionAdmin(admin.ModelAdmin):
    list_display = ("carga", "fila", "campo", "descripcion")
    list_filter = ("carga",)
