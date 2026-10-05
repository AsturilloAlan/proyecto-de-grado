import os
import re
from datetime import date

from django import forms

from .models import CargaArchivo, EmpresaAuditada, Gestion

PATRON_TELEFONO = re.compile(r"^[0-9+()\s-]{6,20}$")
# NIT: se aceptan dígitos con guion o puntos como separadores.
PATRON_NIT_CARACTERES = re.compile(r"^[0-9.\s-]+$")
NIT_MINIMO_DIGITOS = 5
NIT_MAXIMO_DIGITOS = 15
# Al menos una letra: rechaza nombres puramente numéricos.
PATRON_TIENE_LETRA = re.compile(r"[^\W\d_]", re.UNICODE)

EXTENSIONES_PERMITIDAS = [".xlsx", ".xls", ".csv", ".pdf"]
TAMANO_MAXIMO_MB = 20
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024


class EmpresaAuditadaForm(forms.ModelForm):
    # Atajo para crear la primera gestión de la empresa desde este formulario.
    anio_gestion_inicial = forms.IntegerField(
        label="Año de la primera gestión a auditar (opcional)",
        required=False,
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 2000, "max": 2100}),
        help_text=(
            "Las fechas se calculan según la categoría de cierre."
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
            "contacto_nombre": "Nombre del representante legal",
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
        # Rechaza nombres solo numéricos o de símbolos.
        if not PATRON_TIENE_LETRA.search(nombre):
            raise forms.ValidationError(
                "El nombre de la empresa debe incluir al menos una letra; no "
                "puede ser solo números o símbolos."
            )
        return nombre

    def clean_contacto_nombre(self):
        contacto_nombre = self.cleaned_data.get("contacto_nombre", "").strip()
        # El nombre del representante legal no lleva dígitos.
        if contacto_nombre and any(caracter.isdigit() for caracter in contacto_nombre):
            raise forms.ValidationError(
                "El nombre del representante legal no debe contener números."
            )
        return contacto_nombre

    def clean_nit(self):
        nit = self.cleaned_data.get("nit", "").strip()
        if not nit:
            return nit
        digitos = sum(1 for caracter in nit if caracter.isdigit())
        if not PATRON_NIT_CARACTERES.match(nit) or not (
            NIT_MINIMO_DIGITOS <= digitos <= NIT_MAXIMO_DIGITOS
        ):
            raise forms.ValidationError(
                f"NIT no válido: de {NIT_MINIMO_DIGITOS} a {NIT_MAXIMO_DIGITOS} dígitos, "
                "con puntos o guiones opcionales."
            )
        return nit

    def clean_contacto_telefono(self):
        telefono = self.cleaned_data.get("contacto_telefono", "").strip()
        if telefono and not PATRON_TELEFONO.match(telefono):
            raise forms.ValidationError(
                "Teléfono no válido: de 6 a 20 caracteres entre números, espacios, +, - o paréntesis."
            )
        return telefono


class GestionForm(forms.ModelForm):
    """Alta de una gestión (año fiscal)."""

    empresa = forms.ModelChoiceField(
        queryset=EmpresaAuditada.objects.all(),
        required=False,
        label="Empresa (opcional)",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Sugiere las fechas según su categoría de cierre. Sin empresa se usa el año calendario."
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
            raise forms.ValidationError("Año no válido (entre 2000 y 2100).")
        # No se admite una gestión que todavía no empezó.
        limite_superior = date.today().year + 1
        if anio > limite_superior:
            raise forms.ValidationError(
                f"El año {anio} es demasiado lejano a futuro para una gestión a "
                f"auditar (máximo permitido: {limite_superior})."
            )
        return anio

    def clean(self):
        limpio = super().clean()
        anio = limpio.get("anio")
        inicio = limpio.get("fecha_inicio")
        fin = limpio.get("fecha_fin")
        empresa = limpio.get("empresa")
        # Sin fechas manuales se calculan según la categoría de cierre de la empresa, o
        # por año calendario.
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
        # save(commit=False) no toma las fechas calculadas en clean(); se asignan aquí.
        instancia.fecha_inicio = self.cleaned_data.get("fecha_inicio", instancia.fecha_inicio)
        instancia.fecha_fin = self.cleaned_data.get("fecha_fin", instancia.fecha_fin)
        if commit:
            instancia.save()
        return instancia


class AnulacionForm(forms.Form):
    """Motivo de anulación: una causa de la lista y, si hace falta, un detalle."""

    MOTIVOS = [
        ("gestion", "Gestión equivocada"),
        ("empresa", "Empresa equivocada"),
        ("archivo", "Archivo incorrecto o incompleto"),
        ("duplicada", "Carga repetida"),
        ("otro", "Otro motivo"),
    ]

    tipo_motivo = forms.ChoiceField(
        label="Motivo",
        choices=MOTIVOS,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    detalle = forms.CharField(
        label="Detalle",
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean(self):
        datos = super().clean()
        tipo = datos.get("tipo_motivo")
        detalle = (datos.get("detalle") or "").strip()
        if tipo == "otro" and len(detalle) < 10:
            self.add_error("detalle", "Para \"Otro motivo\" se requiere un detalle de al menos 10 caracteres.")
        if tipo:
            texto = dict(self.MOTIVOS)[tipo]
            datos["motivo"] = f"{texto}. {detalle}" if detalle else texto
        return datos


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
                "Formato no soportado. Se aceptan archivos .xlsx, .xls, .csv o .pdf."
            )
        if archivo.size > TAMANO_MAXIMO_BYTES:
            raise forms.ValidationError(
                f"El archivo pesa demasiado (máximo {TAMANO_MAXIMO_MB} MB)."
            )
        if archivo.size == 0:
            # Rechaza archivos vacíos al subirlos.
            raise forms.ValidationError("El archivo está vacío.")
        return archivo
