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
    """Los datos de una carga (estado, totales, archivo) los calcula
    `procesar_carga` — no tiene sentido, y sí bastante riesgo, que alguien
    los edite a mano desde acá. Por eso el admin queda de solo lectura
    para crear/editar; sí se puede borrar (además del permiso normal de
    Django), porque esa es la vía "correcta" para revertir una carga mal
    hecha y volver a subir el archivo corregido — queda documentado solo,
    ya que la carga nueva lleva su propio usuario y fecha de todos modos.
    """

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


@admin.register(RegistroContable)
class RegistroContableAdmin(admin.ModelAdmin):
    """De solo lectura por completo: son las transacciones contables que
    ya se validaron y guardaron al cargar un archivo (RF-01/RF-02). Es
    justamente el dato que el sistema tiene que proteger de alteraciones
    silenciosas — si algo está mal, se corrige revirtiendo la carga
    completa (borrando el CargaArchivo, lo que arrastra sus registros) y
    subiendo de nuevo el archivo corregido, no editando una fila suelta
    sin dejar rastro."""

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
    list_display = ("carga", "fila", "campo", "descripcion")
    list_filter = ("carga",)
