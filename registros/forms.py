import os
import re
import unicodedata
import zipfile
from datetime import date

from django import forms

from .models import CargaArchivo, EmpresaAuditada, Gestion

PATRON_TELEFONO = re.compile(r"\+?[0-9() -]{6,20}")
# NIT: se aceptan dígitos con guion o puntos como separadores.
PATRON_NIT_CARACTERES = re.compile(r"[0-9]+(?:[. -][0-9]+)*")
NIT_MINIMO_DIGITOS = 5
NIT_MAXIMO_DIGITOS = 15
PATRON_TIENE_LETRA = re.compile(r"[^\W\d_]", re.UNICODE)

EXTENSIONES_PERMITIDAS = [".xlsx", ".xls", ".csv", ".pdf"]
TAMANO_MAXIMO_MB = 20
TAMANO_MAXIMO_BYTES = TAMANO_MAXIMO_MB * 1024 * 1024
TAMANO_MAXIMO_EXCEL_EXPANDIDO = 100 * 1024 * 1024
MAXIMO_ENTRADAS_EXCEL = 5000


def _texto_visible(valor, multilinea=False):
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} and not (multilinea and c in "\r\n\t") for c in valor):
        raise forms.ValidationError("No se admiten caracteres invisibles o de control en este campo.")
    return unicodedata.normalize("NFC", valor)


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
            "contador_nombre",
            "contador_email",
            "contador_telefono",
        ]
        labels = {
            "nombre": "Nombre de la empresa",
            "nit": "NIT",
            "rubro": "Rubro / sector económico",
            "categoria_cierre": "Categoría de cierre de gestión (SIN)",
            "contacto_nombre": "Nombre del representante legal",
            "contador_nombre": "Nombre del contador",
            "contacto_email": "Correo",
            "contacto_telefono": "Teléfono",
            "contador_email": "Correo",
            "contador_telefono": "Teléfono",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"class": "form-control"}),
            "nit": forms.TextInput(attrs={"class": "form-control"}),
            "rubro": forms.TextInput(attrs={"class": "form-control"}),
            "categoria_cierre": forms.Select(attrs={"class": "form-select"}),
            "contacto_nombre": forms.TextInput(attrs={"class": "form-control"}),
            "contador_nombre": forms.TextInput(attrs={"class": "form-control"}),
            "contacto_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contacto_telefono": forms.TextInput(attrs={"class": "form-control"}),
            "contador_email": forms.EmailInput(attrs={"class": "form-control"}),
            "contador_telefono": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_nombre(self):
        nombre = _texto_visible(self.cleaned_data["nombre"].strip())
        if not nombre:
            raise forms.ValidationError("El nombre de la empresa es obligatorio.")
        if not PATRON_TIENE_LETRA.search(nombre):
            raise forms.ValidationError(
                "El nombre de la empresa debe incluir al menos una letra; no "
                "puede ser solo números o símbolos."
            )
        return nombre

    def clean_rubro(self):
        return _texto_visible(self.cleaned_data.get("rubro", ""))

    def clean_anio_gestion_inicial(self):
        anio = self.cleaned_data.get("anio_gestion_inicial")
        if anio and anio > date.today().year + 1:
            raise forms.ValidationError(f"Año demasiado lejano a futuro (máximo permitido: {date.today().year + 1}).")
        return anio

    def clean_contacto_nombre(self):
        contacto_nombre = unicodedata.normalize("NFC", self.cleaned_data.get("contacto_nombre", "").strip())
        if contacto_nombre and (
            not PATRON_TIENE_LETRA.search(contacto_nombre)
            or any(not (c.isalpha() or c in " .'-’") for c in contacto_nombre)
        ):
            raise forms.ValidationError(
                "El nombre del representante legal debe contener letras; se permiten espacios, puntos, apóstrofos y guiones."
            )
        return contacto_nombre

    def clean_contador_nombre(self):
        nombre = unicodedata.normalize("NFC", " ".join((self.cleaned_data.get("contador_nombre") or "").split()))
        if nombre and (not PATRON_TIENE_LETRA.search(nombre)
                       or any(not (c.isalpha() or c in " .'-’") for c in nombre)):
            raise forms.ValidationError(
                "El nombre del contador debe contener letras; se permiten espacios, puntos, apóstrofos y guiones.")
        return nombre

    def clean_nit(self):
        nit = self.cleaned_data.get("nit", "").strip()
        if not nit:
            return nit
        digitos = sum(1 for caracter in nit if caracter.isdigit())
        if not PATRON_NIT_CARACTERES.fullmatch(nit) or not (
            NIT_MINIMO_DIGITOS <= digitos <= NIT_MAXIMO_DIGITOS
        ):
            raise forms.ValidationError(
                f"NIT no válido: de {NIT_MINIMO_DIGITOS} a {NIT_MAXIMO_DIGITOS} dígitos, "
                "con puntos o guiones opcionales."
            )
        return nit

    def clean_contacto_telefono(self):
        return self._telefono_valido("contacto_telefono")

    def clean_contador_telefono(self):
        return self._telefono_valido("contador_telefono")

    def _telefono_valido(self, campo):
        telefono = self.cleaned_data.get(campo, "").strip()
        if telefono and (
            not PATRON_TELEFONO.fullmatch(telefono)
            or len(telefono) > 20
            or not 6 <= sum(c in "0123456789" for c in telefono) <= 15
            or telefono.count("(") != telefono.count(")")
            or telefono.count("(") > 1
            or ("(" in telefono and not re.search(r"\([0-9]+\)", telefono))
            or (")" in telefono and telefono.index(")") < telefono.index("("))
        ):
            raise forms.ValidationError(
                "Teléfono no válido: entre 6 y 15 dígitos, con prefijo + opcional y separadores equilibrados (máximo 20 caracteres)."
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
            "fecha_inicio": forms.DateInput(attrs={"class": "form-control", "type": "date", "min": "1999-01-01"}, format="%Y-%m-%d"),
            "fecha_fin": forms.DateInput(attrs={"class": "form-control", "type": "date", "min": "2000-01-01"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_inicio"].required = False
        self.fields["fecha_fin"].required = False

    def clean_anio(self):
        anio = self.cleaned_data["anio"]
        if anio < 2000 or anio > 2100:
            raise forms.ValidationError("Año no válido (entre 2000 y 2100).")
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
        # Sin fechas explícitas se usa el cierre fiscal de la empresa o el año calendario.
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
        # El periodo debe incluir el año indicado y durar como máximo un año.
        if anio and inicio and fin and anio not in (inicio.year, fin.year):
            self.add_error("fecha_fin", f"Las fechas no corresponden a la gestión {anio}.")
        if inicio and fin and (fin - inicio).days > 365:
            self.add_error("fecha_inicio", "Una gestión dura como máximo un año (12 meses).")
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

    def clean_detalle(self):
        detalle = _texto_visible(self.cleaned_data.get("detalle", ""), multilinea=True)
        if detalle and not any(c.isalpha() for c in detalle):
            raise forms.ValidationError("El detalle debe explicar el motivo con palabras, no solo números o símbolos.")
        return detalle

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
            raise forms.ValidationError("El archivo está vacío.")
        if not _contenido_coincide(archivo, extension):
            raise forms.ValidationError(
                f"El contenido no corresponde a un archivo {extension} válido. "
                "Verificar que no esté dañado ni renombrado."
            )
        if extension in (".xlsx", ".xls"):
            _validar_excel_comprimido(archivo)
        return archivo


# Firmas de inicio de archivo: la extensión sola se puede cambiar a mano.
FIRMA_ZIP = b"PK\x03\x04"  # .xlsx (Excel moderno)
FIRMA_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # .xls (Excel 97-2003)


def _validar_excel_comprimido(archivo):
    """Inspecciona el ZIP sin descomprimirlo antes de entregarlo al lector Excel."""
    try:
        if archivo.read(4) != FIRMA_ZIP:
            return  # El formato OLE antiguo se valida al procesarlo.
        archivo.seek(0)
        with zipfile.ZipFile(archivo) as paquete:
            entradas = paquete.infolist()
            if len(entradas) > MAXIMO_ENTRADAS_EXCEL or sum(e.file_size for e in entradas) > TAMANO_MAXIMO_EXCEL_EXPANDIDO:
                raise forms.ValidationError("El Excel descomprimido supera el límite de 100 MB o contiene demasiados componentes.")
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(paquete.namelist()):
                raise forms.ValidationError("El archivo comprimido no contiene un libro Excel válido.")
            if any(e.flag_bits & 1 for e in entradas):
                raise forms.ValidationError("No se admiten libros Excel cifrados.")
    except zipfile.BadZipFile:
        raise forms.ValidationError("El archivo Excel está dañado o no es un ZIP válido.")
    finally:
        archivo.seek(0)


def _contenido_coincide(archivo, extension):
    """Revisa los primeros bytes según la extensión. CSV no tiene firma: debe ser texto
    (sin bytes nulos, típicos de un archivo binario renombrado a .csv)."""
    inicio = archivo.read(1024)
    archivo.seek(0)
    if extension == ".csv":
        return b"\x00" not in inicio and not inicio.startswith((FIRMA_ZIP, FIRMA_OLE, b"%PDF"))
    if extension in (".xlsx", ".xls"):
        # Algunos sistemas guardan un .xlsx con extensión .xls: pandas lo lee igual.
        return inicio.startswith(FIRMA_ZIP) or inicio.startswith(FIRMA_OLE)
    if extension == ".pdf":
        return b"%PDF" in inicio
    return False
