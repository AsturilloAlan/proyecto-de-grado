from django.contrib import admin

from .models import RegistroCorreo


@admin.register(RegistroCorreo)
class RegistroCorreoAdmin(admin.ModelAdmin):
    """Bitácora de correos: solo lectura (es una pista de auditoría)."""

    list_display = ("fecha", "tipo", "destinatario", "usuario", "enviado", "identificador")
    list_filter = ("tipo", "enviado")
    search_fields = ("destinatario", "usuario__username", "identificador")
    readonly_fields = [f.name for f in RegistroCorreo._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
