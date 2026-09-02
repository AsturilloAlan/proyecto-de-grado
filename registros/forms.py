import os

from django import forms

from .models import CargaArchivo, EmpresaAuditada

EXTENSIONES_PERMITIDAS = [".xlsx", ".xls", ".csv"]
TAMANO_MAXIMO_MB = 20
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024


class EmpresaAuditadaForm(forms.ModelForm):
    class Meta:
        model = EmpresaAuditada
        fields = [
            "nombre",
            "nit",
            "rubro",
            "contacto_nombre",
            "contacto_email",
            "contacto_telefono",
        ]
        labels = {
            "nombre": "Nombre de la empresa",
            "nit": "NIT",
            "rubro": "Rubro / sector económico",
            "contacto_nombre": "Nombre del contacto",
            "contacto_email": "Correo del contacto",
            "contacto_telefono": "Teléfono del contacto",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "form-control"}),
            "nit": forms.TextInput(attrs={"class": "form-control"}),
            "rubro": forms.TextInput(attrs={"class": "form-control"}),
            "contacto_nombre": forms.TextInput(attrs={"class": "form-control"}),
            "contacto_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contacto_telefono": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        if not nombre:
            raise forms.ValidationError("El nombre de la empresa es obligatorio.")
        return nombre


class CargaArchivoForm(forms.ModelForm):
    class Meta:
        model = CargaArchivo
        fields = ["empresa", "gestion", "archivo"]
        labels = {
            "empresa": "Empresa auditada",
            "gestion": "Gestión",
            "archivo": "Archivo de registros contables (libro mayor / diario)",
        }
        widgets = {
            "empresa": forms.Select(attrs={"class": "form-select"}),
            "gestion": forms.Select(attrs={"class": "form-select"}),
            "archivo": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        extension = os.path.splitext(archivo.name)[1].lower()
        if extension not in EXTENSIONES_PERMITIDAS:
            raise forms.ValidationError(
                "Formato no soportado. Sube un archivo .xlsx, .xls o .csv."
            )
        if archivo.size > TAMANO_MAXIMO_BYTES:
            raise forms.ValidationError(
                f"El archivo pesa demasiado (máximo {TAMANO_MAXIMO_MB} MB)."
            )
        return archivo
