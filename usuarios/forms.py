"""Formularios de la app usuarios."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.forms import PasswordInput, TextInput

from .models import PerfilUsuario

ROLES_DISPONIBLES = [("Auditor", "Auditor"), ("Administrador", "Administrador")]


def _validar_email_unico(email, instancia):
    """El correo recibe el código 2FA, por eso no puede repetirse entre cuentas."""
    email = email.strip()
    if email and User.objects.filter(email__iexact=email).exclude(pk=instancia.pk).exists():
        raise forms.ValidationError("Ya existe una cuenta registrada con ese correo.")
    return email


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
            "avatar": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "avatar_preset": forms.HiddenInput(),
        }

    def clean_avatar(self):
        # Límite de tamaño de la imagen.
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


class UsuarioEditarForm(forms.ModelForm):
    """Editar una cuenta existente desde el panel de usuarios: correo, si está
    activa, y su rol.
    """

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sin correo la cuenta no puede completar el 2FA al iniciar sesión.
        self.fields["email"].required = True

    def clean_email(self):
        return _validar_email_unico(self.cleaned_data["email"], self.instance)
