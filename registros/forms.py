import os
import re
from datetime import date

from django import forms

from .models import CargaArchivo, EmpresaAuditada, Gestion

PATRON_TELEFONO = re.compile(r"^[0-9+()\s-]{6,20}$")

EXTENSIONES_PERMITIDAS = [".xlsx", ".xls", ".csv", ".pdf"]
TAMANO_MAXIMO_MB = 20
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024


class EmpresaAuditadaForm(forms.ModelForm):
    # No es un campo del modelo EmpresaAuditada: es un atajo para no tener
    # que ir a otra pantalla a crear la primera gestión de esta empresa.
    # Si se deja vacío, la empresa queda registrada igual, sin gestión
    # (se puede agregar después desde "Cargar registros").
    anio_gestion_inicial = forms.IntegerField(
        label="Año de la primera gestión a auditar (opcional)",
        required=False,
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 2000, "max": 2100}),
        help_text=(
            "Las fechas se calculan solas según la categoría de cierre elegida "
            "abajo (por defecto, año calendario 01/01 - 31/12). Si hace falta "
            "ajustarlas a mano, se puede después desde \"Cargar registros\" → "
            "\"Agregar gestión\"."
        ),
    )

    class Meta:
        model = EmpresaAuditada
        fields = [
            "nombre",
            "nit",
            "rubro",
            "categoria_cierre",
            "contacto_nombre",
            "contacto_email",
            "contacto_telefono",
        ]
        labels = {
            "nombre": "Nombre de la empresa",
            "nit": "NIT",
            "rubro": "Rubro / sector económico",
            "categoria_cierre": "Categoría de cierre de gestión (SIN)",
            "contacto_nombre": "Nombre del contacto",
            "contacto_email": "Correo del contacto",
            "contacto_telefono": "Teléfono del contacto",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "form-control"}),
            "nit": forms.TextInput(attrs={"class": "form-control"}),
            "rubro": forms.TextInput(attrs={"class": "form-control"}),
            "categoria_cierre": forms.Select(attrs={"class": "form-select"}),
            "contacto_nombre": forms.TextInput(attrs={"class": "form-control"}),
            "contacto_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contacto_telefono": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_nombre(self):
        nombre = self.cleaned_data["nombre"].strip()
        if not nombre:
            raise forms.ValidationError("El nombre de la empresa es obligatorio.")
        return nombre

    def clean_contacto_telefono(self):
        telefono = self.cleaned_data.get("contacto_telefono", "").strip()
        if telefono and not PATRON_TELEFONO.match(telefono):
            raise forms.ValidationError(
                "Ingresa un teléfono válido (solo números, espacios, +, - o "
                "paréntesis, entre 6 y 20 caracteres)."
            )
        return telefono


class GestionForm(forms.ModelForm):
    """Alta de una gestión (año fiscal).

    Uso simple (la mayoría de los casos): se elige la empresa y se escribe
    el año — las fechas se calculan solas según la categoría de cierre
    SIN de esa empresa (ver `EmpresaAuditada.fechas_gestion_para`), en vez
    de asumir siempre el año calendario. Es la misma automatización que
    ya existía al crear una empresa nueva, pero antes NO se aplicaba acá
    (esta pantalla no sabía para qué empresa era la gestión), que era
    justamente el hueco: cualquier gestión agregada después de la inicial
    quedaba sin ninguna sugerencia de fechas.

    "Empresa" es un campo del FORMULARIO, no del modelo Gestion: Gestion
    sigue siendo independiente de cualquier empresa en particular (varias
    empresas con el mismo rubro pueden compartir una gestión con las
    mismas fechas) — acá solo se usa para calcular la sugerencia, no se
    guarda esa asociación.

    Uso avanzado (opcional): igual se puede escribir o corregir las
    fechas de inicio/fin a mano (ej. una excepción real de esa empresa),
    tanto si se eligió empresa como si no.
    """

    empresa = forms.ModelChoiceField(
        queryset=EmpresaAuditada.objects.all(),
        required=False,
        label="Empresa (para sugerir las fechas según su categoría de cierre SIN)",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Autocompleta fecha de inicio/fin según la categoría de cierre de "
            "esa empresa. El resultado se puede corregir a mano igual. Si se "
            "deja sin elegir, se usa el año calendario de siempre."
        ),
    )

    class Meta:
        model = Gestion
        fields = ["anio", "fecha_inicio", "fecha_fin"]
        labels = {
            "anio": "Año de la gestión",
            "fecha_inicio": "Fecha de inicio (opcional)",
            "fecha_fin": "Fecha de fin (opcional)",
        }
        widgets = {
            "anio": forms.NumberInput(attrs={"class": "form-control", "min": 2000, "max": 2100}),
            "fecha_inicio": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_inicio"].required = False
        self.fields["fecha_fin"].required = False

    def clean_anio(self):
        anio = self.cleaned_data["anio"]
        if anio < 2000 or anio > 2100:
            raise forms.ValidationError("Ingresa un año válido (entre 2000 y 2100).")
        return anio

    def clean(self):
        limpio = super().clean()
        anio = limpio.get("anio")
        inicio = limpio.get("fecha_inicio")
        fin = limpio.get("fecha_fin")
        empresa = limpio.get("empresa")
        # Si no se especificaron fechas a mano: con empresa elegida, se
        # calculan según su categoría de cierre SIN; sin empresa, se cae
        # al año calendario de siempre (comportamiento previo, sin cambios).
        if anio and not inicio and not fin:
            if empresa is not None:
                inicio, fin = empresa.fechas_gestion_para(anio)
            else:
                inicio, fin = date(anio, 1, 1), date(anio, 12, 31)
            limpio["fecha_inicio"] = inicio
            limpio["fecha_fin"] = fin
        else:
            if anio and not inicio:
                inicio = date(anio, 1, 1)
                limpio["fecha_inicio"] = inicio
            if anio and not fin:
                fin = date(anio, 12, 31)
                limpio["fecha_fin"] = fin
        if inicio and fin and fin <= inicio:
            raise forms.ValidationError(
                "La fecha de fin debe ser posterior a la fecha de inicio."
            )
        return limpio

    def save(self, commit=True):
        instancia = super().save(commit=False)
        # clean() ya rellenó fecha_inicio/fecha_fin si vinieron vacías,
        # pero save(commit=False) no vuelve a pasar por cleaned_data para
        # asignarlas al objeto — se hace explícito acá.
        instancia.fecha_inicio = self.cleaned_data.get("fecha_inicio", instancia.fecha_inicio)
        instancia.fecha_fin = self.cleaned_data.get("fecha_fin", instancia.fecha_fin)
        if commit:
            instancia.save()
        return instancia


class CargaArchivoForm(forms.ModelForm):
    class Meta:
        model = CargaArchivo
        fields = ["empresa", "gestion", "archivo"]
        labels = {
            "empresa": "Empresa auditada",
            "gestion": "Gestión",
            "archivo": "Archivo de registros contables (libro mayor / diario, Excel o PDF)",
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
                "Formato no soportado. Sube un archivo .xlsx, .xls, .csv o .pdf "
                "(Libro Diario exportado en PDF)."
            )
        if archivo.size > TAMANO_MAXIMO_BYTES:
            raise forms.ValidationError(
                f"El archivo pesa demasiado (máximo {TAMANO_MAXIMO_MB} MB)."
            )
        return archivo
