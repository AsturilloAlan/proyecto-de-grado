"""Formularios de la app usuarios.

`LoginForm` extiende el `AuthenticationForm` de Django únicamente para
aplicar las clases de Bootstrap a los campos (diseño), sin tocar la
lógica de validación de credenciales que ya provee Django. Lo mismo hace
`CambiarClaveForm` con `PasswordChangeForm`.
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.forms import PasswordInput, TextInput

from .models import PerfilUsuario

ROLES_DISPONIBLES = [("Auditor", "Auditor"), ("Administrador", "Administrador")]


class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = TextInput(
            attrs={"class": "form-control", "autofocus": True}
        )
        self.fields["password"].widget = PasswordInput(
            attrs={"class": "form-control"}
        )


class CambiarClaveForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.widget.attrs["class"] = "form-control"


class PerfilForm(forms.ModelForm):
    """Foto de perfil: subir una imagen propia, o elegir uno de los
    avatares predefinidos (campo `avatar_preset`, seteado por JS al
    hacer clic en una de las opciones de la galería)."""

    class Meta:
        model = PerfilUsuario
        fields = ["avatar", "avatar_preset"]
        labels = {"avatar": "Foto de perfil"}
        widgets = {
            "avatar": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "avatar_preset": forms.HiddenInput(),
        }


class DatosCuentaForm(forms.ModelForm):
    """Datos básicos de la cuenta (correo) editables desde el perfil.
    El nombre de usuario y la contraseña no se tocan aquí."""

    class Meta:
        model = User
        fields = ["email"]
        labels = {"email": "Correo electrónico"}
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }


class CodigoVerificacionForm(forms.Form):
    """Ingreso del código de 6 dígitos enviado por correo (2FA)."""

    codigo = forms.CharField(
        label="Código de verificación",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg text-center",
                "autofocus": True,
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "placeholder": "000000",
            }
        ),
    )


class UsuarioCrearForm(UserCreationForm):
    """Crear una cuenta nueva desde el panel de usuarios (solo
    Administrador), asignándole un rol de una vez. Reutiliza
    `UserCreationForm` de Django para no reinventar la validación de
    contraseñas (mismos validadores que ya aplican en todo el sistema)."""

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

    def save(self, commit=True):
        usuario = super().save(commit=commit)
        if commit:
            grupo = Group.objects.get(name=self.cleaned_data["rol"])
            usuario.groups.add(grupo)
        return usuario


class UsuarioEditarForm(forms.ModelForm):
    """Editar una cuenta existente desde el panel de usuarios: correo,
    si está activa, y su rol. El nombre de usuario y la contraseña se
    manejan aparte (username en /admin/, contraseña la cambia cada
    quien desde su propio perfil)."""

    rol = forms.ChoiceField(
        label="Rol",
        choices=ROLES_DISPONIBLES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = User
        fields = ["email", "is_active"]
        labels = {"email": "Correo electrónico", "is_active": "Cuenta activa"}
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
