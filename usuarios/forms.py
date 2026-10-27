"""Formularios de la app usuarios."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.forms import PasswordInput, TextInput

from .models import PerfilUsuario
from .validadores import muy_parecidas

ROLES_DISPONIBLES = [("Auditor", "Auditor"), ("Administrador", "Administrador")]


def _validar_email_unico(email, instancia):
    """El correo recibe el código 2FA, por eso no puede repetirse entre cuentas."""
    email = email.strip()
    if email and User.objects.filter(email__iexact=email).exclude(pk=instancia.pk).exists():
        raise forms.ValidationError("Ya existe una cuenta registrada con ese correo.")
    return email


def _limpiar_nombre(valor, etiqueta):
    """Nombre o apellido de persona: letras (con tildes), espacios, apóstrofo, punto o guion."""
    import unicodedata

    valor = " ".join(unicodedata.normalize("NFC", valor or "").split())
    if valor and (not any(c.isalpha() for c in valor) or any(not (c.isalpha() or c in " .'-’") for c in valor)):
        raise forms.ValidationError(f"El {etiqueta} solo admite letras, espacios, puntos, apóstrofos y guiones.")
    return valor


class NombresMixin:
    """Nombre y apellido (opcionales): se usan en las firmas de los reportes."""

    def clean_first_name(self):
        return _limpiar_nombre(self.cleaned_data.get("first_name"), "nombre")

    def clean_last_name(self):
        return _limpiar_nombre(self.cleaned_data.get("last_name"), "apellido")


ATRIBUTOS_NOMBRE = {"class": "form-control", "maxlength": 60, "autocomplete": "given-name"}
ATRIBUTOS_APELLIDO = {"class": "form-control", "maxlength": 60, "autocomplete": "family-name"}


DIAS_ENTRE_CAMBIOS_DE_NOMBRE = 30


class DatosPersonalesForm(NombresMixin, forms.ModelForm):
    """Nombre y apellido propios. Como firman los reportes, cambiarlos pide la contraseña
    actual, queda en el historial, se avisa por correo y cada usuario (también el
    Administrador, que firma como socio) puede hacerlo como máximo una vez cada 30 días.
    Las correcciones del nombre de otro usuario las hace el Administrador desde la
    gestión de usuarios, también con rastro en el historial."""

    clave_actual = forms.CharField(
        label="Contraseña actual", strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
        help_text="Necesaria para confirmar el cambio.",
    )

    def __init__(self, *args, es_administrador=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.es_administrador = es_administrador
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True

    def clean_clave_actual(self):
        clave = self.cleaned_data.get("clave_actual", "")
        if not self.instance.check_password(clave):
            raise forms.ValidationError("La contraseña no es correcta.")
        return clave

    def clean(self):
        limpio = super().clean()
        if self.errors:
            return limpio
        sin_cambio = (limpio.get("first_name") == self.initial.get("first_name")
                      and limpio.get("last_name") == self.initial.get("last_name"))
        if sin_cambio:
            return limpio
        from datetime import timedelta

        from django.utils import timezone

        from registros.models import HistorialCambio

        ultimo = (HistorialCambio.objects
                  .filter(modelo="User", objeto_id=self.instance.pk, campo__in=("first_name", "last_name"),
                          usuario=self.instance)
                  .order_by("-fecha").values_list("fecha", flat=True).first())
        if ultimo and timezone.now() - ultimo < timedelta(days=DIAS_ENTRE_CAMBIOS_DE_NOMBRE):
            disponible = timezone.localtime(ultimo + timedelta(days=DIAS_ENTRE_CAMBIOS_DE_NOMBRE))
            raise forms.ValidationError(
                f"El nombre se cambió hace poco. Se podrá cambiar de nuevo desde el "
                f"{disponible:%d/%m/%Y}.")
        return limpio

    class Meta:
        model = User
        fields = ["first_name", "last_name"]
        labels = {"first_name": "Nombre", "last_name": "Apellido"}
        widgets = {"first_name": forms.TextInput(attrs=ATRIBUTOS_NOMBRE),
                   "last_name": forms.TextInput(attrs=ATRIBUTOS_APELLIDO)}


class LoginForm(AuthenticationForm):
    # Mensaje corto; no dice cuál de los dos datos falló (no revela si el usuario existe).
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Usuario o contraseña incorrectos.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = TextInput(
            attrs={"class": "form-control", "autofocus": True, "autocomplete": "username", "placeholder": " ", "autocapitalize": "none", "spellcheck": "false"}
        )
        self.fields["password"].widget = PasswordInput(
            attrs={"class": "form-control", "autocomplete": "current-password", "placeholder": " "}
        )


class CambiarClaveForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.widget.attrs["class"] = "form-control"

    def clean(self):
        limpio = super().clean()
        # Aquí sí se conoce la contraseña actual: se compara directamente con la nueva.
        anterior, nueva = limpio.get("old_password"), limpio.get("new_password1")
        if anterior and nueva and muy_parecidas(anterior, nueva):
            self.add_error("new_password1", "La contraseña nueva es muy parecida a la actual. Elegir una distinta.")
        return limpio


TAMANO_MAXIMO_AVATAR_MB = 5
TAMANO_MAXIMO_AVATAR_BYTES = TAMANO_MAXIMO_AVATAR_MB * 1024 * 1024


class PerfilForm(forms.ModelForm):
    """Foto de perfil: subir una imagen propia, o elegir uno de los avatares
    predefinidos.
    """

    class Meta:
        model = PerfilUsuario
        fields = ["avatar", "avatar_preset"]
        labels = {"avatar": "Foto de perfil"}
        widgets = {
            # Sin "Actualmente / Limpiar": elegir un avatar predefinido ya reemplaza la foto.
            "avatar": forms.FileInput(attrs={"class": "d-none", "accept": "image/*"}),
            "avatar_preset": forms.HiddenInput(),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and hasattr(avatar, "size") and avatar.size > TAMANO_MAXIMO_AVATAR_BYTES:
            raise forms.ValidationError(
                f"La imagen pesa demasiado (máximo {TAMANO_MAXIMO_AVATAR_MB} MB)."
            )
        return avatar


class DatosCuentaForm(forms.ModelForm):
    """Datos básicos de la cuenta (correo) editables desde el perfil."""

    clave_actual = forms.CharField(
        label="Contraseña actual",
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "current-password"}),
        help_text="Necesaria para confirmar el cambio de correo.",
    )

    class Meta:
        model = User
        fields = ["email"]
        labels = {"email": "Correo electrónico"}
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True

    def clean_email(self):
        return _validar_email_unico(self.cleaned_data["email"], self.instance)

    def clean_clave_actual(self):
        clave = self.cleaned_data.get("clave_actual", "")
        if not self.instance.check_password(clave):
            raise forms.ValidationError("La contraseña no es correcta.")
        return clave


class CodigoVerificacionForm(forms.Form):
    """Ingreso del código de 6 dígitos enviado por correo (2FA)."""

    codigo = forms.RegexField(
        regex=r"\A[0-9]{6}\Z",
        strip=True,
        error_messages={"invalid": "El código debe contener exactamente 6 dígitos del 0 al 9."},
        label="Código de verificación",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg text-center",
                "autofocus": True,
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
                "autocomplete": "one-time-code",
                "placeholder": "000000",
                "aria-describedby": "codigo-ayuda codigo-errores",
            }
        ),
    )


class UsuarioCrearForm(UserCreationForm):
    """Crear una cuenta nueva desde el panel de usuarios (solo Administrador),
    asignándole un rol de una vez.
    """

    email = forms.EmailField(
        label="Correo electrónico",
        required=True,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    rol = forms.ChoiceField(
        label="Rol",
        choices=ROLES_DISPONIBLES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre_campo in ("username", "password1", "password2"):
            self.fields[nombre_campo].widget.attrs["class"] = "form-control"

    def clean_email(self):
        return _validar_email_unico(self.cleaned_data["email"], self.instance)

    def save(self, commit=True):
        usuario = super().save(commit=commit)
        if commit:
            grupo = Group.objects.get(name=self.cleaned_data["rol"])
            usuario.groups.add(grupo)
        return usuario


class UsuarioEditarForm(NombresMixin, forms.ModelForm):
    """Editar una cuenta existente desde el panel de usuarios: nombre, correo, si está
    activa, y su rol.
    """

    rol = forms.ChoiceField(
        label="Rol",
        choices=ROLES_DISPONIBLES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "is_active"]
        labels = {"first_name": "Nombre", "last_name": "Apellido",
                  "email": "Correo electrónico", "is_active": "Cuenta activa"}
        widgets = {
            "first_name": forms.TextInput(attrs=ATRIBUTOS_NOMBRE),
            "last_name": forms.TextInput(attrs=ATRIBUTOS_APELLIDO),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sin correo la cuenta no puede completar el 2FA al iniciar sesión.
        self.fields["email"].required = True

    def clean_email(self):
        return _validar_email_unico(self.cleaned_data["email"], self.instance)


class RecuperarClaveForm(forms.Form):
    """Correo de la cuenta para recibir el enlace de recuperación."""

    email = forms.EmailField(
        label="Correo electrónico",
        max_length=254,
        widget=forms.EmailInput(attrs={
            "class": "form-control", "autocomplete": "email", "autofocus": True,
            "placeholder": " ", "autocapitalize": "none", "spellcheck": "false",
            "aria-describedby": "correo-ayuda correo-errores",
        }),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip()


class RestablecerClaveForm(SetPasswordForm):
    """Contraseña nueva desde el enlace del correo (mismas reglas que el cambio normal)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.widget.attrs["class"] = "form-control"
