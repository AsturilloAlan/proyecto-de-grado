from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .autenticacion_2fa import CLAVE_SESION_USUARIO_PENDIENTE, enviar_codigo, validar_codigo
from .decorators import rol_requerido
from .forms import (
    CambiarClaveForm,
    CodigoVerificacionForm,
    DatosCuentaForm,
    PerfilForm,
    UsuarioCrearForm,
    UsuarioEditarForm,
)
from .models import PRESETS_AVATAR, PerfilUsuario

# --- Bloqueo por intentos fallidos de login (RNF-03, protección básica
# contra fuerza bruta) ---
MAXIMO_INTENTOS_LOGIN = 5
MINUTOS_BLOQUEO_LOGIN = 15


def _clave_intentos_login(username):
    return f"login_intentos_{username.strip().lower()}"


class LoginSiempreInicioView(LoginView):
    """Login que siempre redirige al inicio tras autenticarse, con dos
    capas extra de seguridad (RNF-03):

    1. Bloqueo temporal por intentos fallidos: tras varios intentos
       seguidos con la misma cuenta, se bloquea unos minutos, para
       dificultar un ataque de fuerza bruta contra la contraseña.
    2. Verificación en dos pasos (2FA) por correo: si el usuario y la
       contraseña son correctos, no se completa el login de inmediato —
       se envía un código de 6 dígitos al correo registrado, y recién
       tras ingresarlo correctamente (vista `verificar_codigo`) se
       inicia la sesión de verdad.

    Django, por defecto, respeta el parámetro `?next=` que agrega
    `login_required` cuando un usuario sin sesión intenta entrar a una
    URL protegida (por ejemplo, editar una empresa) — al loguearse,
    lo manda directo ahí. Aquí lo ignoramos a propósito: siempre se
    aterriza en el inicio, que es más predecible para el usuario.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            username = request.POST.get("username", "")
            if username:
                clave = _clave_intentos_login(username)
                intentos = cache.get(clave, 0)
                if intentos >= MAXIMO_INTENTOS_LOGIN:
                    # No se repite el nombre de usuario en el mensaje: no
                    # aporta nada útil y, si alguien prueba usuarios al
                    # azar, es mejor no confirmarle que ese texto fue
                    # aceptado tal cual.
                    messages.error(
                        request,
                        f"Demasiados intentos fallidos. Espera {MINUTOS_BLOQUEO_LOGIN} "
                        f"minutos e intenta de nuevo.",
                    )
                    return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        username = form.data.get("username", "")
        if username:
            clave = _clave_intentos_login(username)
            intentos = cache.get(clave, 0) + 1
            cache.set(clave, intentos, timeout=MINUTOS_BLOQUEO_LOGIN * 60)

            # Aviso previo al bloqueo: recién en los últimos intentos, para
            # no generar ruido con cada simple error de tipeo.
            intentos_restantes = MAXIMO_INTENTOS_LOGIN - intentos
            if 0 < intentos_restantes <= 2:
                if intentos_restantes == 1:
                    texto = "Te queda 1 intento"
                else:
                    texto = f"Te quedan {intentos_restantes} intentos"
                messages.warning(
                    self.request,
                    f"{texto} antes de que el acceso se bloquee por "
                    f"{MINUTOS_BLOQUEO_LOGIN} minutos.",
                )
        return super().form_invalid(form)

    def form_valid(self, form):
        # Usuario y contraseña correctos: se resetea el contador de
        # intentos fallidos, pero AÚN NO se inicia sesión — falta el
        # segundo factor (código por correo).
        username = form.data.get("username", "")
        if username:
            cache.delete(_clave_intentos_login(username))

        usuario = form.get_user()

        if not usuario.email:
            messages.error(
                self.request,
                "Tu cuenta no tiene un correo registrado, necesario para el "
                "código de verificación. Contacta con el administrador del sistema.",
            )
            return redirect("login")

        # Se guarda el backend con el que se autenticó (lo fija
        # `authenticate()` dentro del form) para poder llamar a
        # `login()` directamente más adelante, sin volver a pedir
        # usuario/contraseña.
        self.request.session[CLAVE_SESION_USUARIO_PENDIENTE] = usuario.pk
        self.request.session["sesion_2fa_backend"] = usuario.backend
        enviar_codigo(usuario)
        messages.info(
            self.request,
            f"Te enviamos un código de verificación a {usuario.email}.",
        )
        return redirect("verificar_codigo")


def _destino_tras_login(usuario):
    if usuario.is_superuser:
        return reverse_lazy("admin:index")
    return reverse_lazy("home")


def verificar_codigo(request):
    """Segundo paso del login: pide el código de 6 dígitos enviado por
    correo. No usa `@login_required` porque el usuario TODAVÍA no tiene
    sesión iniciada — se identifica por el id guardado temporalmente en
    `request.session` durante `LoginSiempreInicioView.form_valid`."""
    usuario_id = request.session.get(CLAVE_SESION_USUARIO_PENDIENTE)
    if not usuario_id:
        messages.error(request, "Tu sesión de verificación expiró. Inicia sesión de nuevo.")
        return redirect("login")

    usuario = get_object_or_404(User, pk=usuario_id)

    if request.method == "POST":
        if "reenviar" in request.POST:
            enviar_codigo(usuario)
            messages.info(request, f"Te enviamos un nuevo código a {usuario.email}.")
            return redirect("verificar_codigo")

        form = CodigoVerificacionForm(request.POST)
        if form.is_valid():
            ok, error = validar_codigo(usuario, form.cleaned_data["codigo"])
            if ok:
                usuario.backend = request.session.get(
                    "sesion_2fa_backend", "django.contrib.auth.backends.ModelBackend"
                )
                del request.session[CLAVE_SESION_USUARIO_PENDIENTE]
                request.session.pop("sesion_2fa_backend", None)
                auth_login(request, usuario)
                return redirect(_destino_tras_login(usuario))
            messages.error(request, error)
    else:
        form = CodigoVerificacionForm()

    return render(
        request,
        "usuarios/verificar_codigo.html",
        {"form": form, "correo": usuario.email},
    )


@login_required
def home(request):
    """Página principal tras iniciar sesión: funciona como panel/dashboard.

    `es_administrador` ya llega por el context processor `navegacion`;
    aquí solo se agregan los datos propios del panel (conteos y últimas
    cargas), evitando repetir la app `registros` en esta app para no
    generar un import circular entre apps.
    """
    from registros.models import CargaArchivo, EmpresaAuditada  # import local: evita acoplar usuarios <-> registros a nivel de módulo

    total_empresas = EmpresaAuditada.objects.count()
    cargas = CargaArchivo.objects.select_related("empresa", "gestion", "usuario")
    total_cargas = cargas.count()
    cargas_con_errores = cargas.filter(estado="con_errores").count()
    ultimas_cargas = cargas.order_by("-fecha_carga")[:5]

    return render(
        request,
        "usuarios/home.html",
        {
            "total_empresas": total_empresas,
            "total_cargas": total_cargas,
            "cargas_con_errores": cargas_con_errores,
            "ultimas_cargas": ultimas_cargas,
        },
    )


@login_required
def perfil(request):
    """Perfil del usuario autenticado: datos de la cuenta y foto/avatar.

    Dos formularios independientes en la misma página, cada uno con su
    propio botón (`name="accion"` distingue cuál se envió) para que
    guardar el correo no exija volver a tocar el avatar, y viceversa.
    """
    perfil_usuario, _creado = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST" and request.POST.get("accion") == "avatar":
        form_avatar = PerfilForm(request.POST, request.FILES, instance=perfil_usuario)
        form_datos = DatosCuentaForm(instance=request.user)
        if form_avatar.is_valid():
            # Elegir un avatar predefinido limpia la foto subida, y
            # subir una foto limpia el preset elegido: son alternativas,
            # no se combinan.
            if form_avatar.cleaned_data.get("avatar_preset") and not request.FILES.get("avatar"):
                perfil_usuario.avatar.delete(save=False)
            elif request.FILES.get("avatar"):
                form_avatar.instance.avatar_preset = ""
            form_avatar.save()
            messages.success(request, "Avatar actualizado correctamente.")
            return redirect("perfil")
    elif request.method == "POST" and request.POST.get("accion") == "datos":
        form_datos = DatosCuentaForm(request.POST, instance=request.user)
        form_avatar = PerfilForm(instance=perfil_usuario)
        if form_datos.is_valid():
            form_datos.save()
            messages.success(request, "Correo actualizado correctamente.")
            return redirect("perfil")
    else:
        form_avatar = PerfilForm(instance=perfil_usuario)
        form_datos = DatosCuentaForm(instance=request.user)

    return render(
        request,
        "usuarios/perfil.html",
        {
            "form": form_avatar,
            "form_datos": form_datos,
            "presets_avatar": PRESETS_AVATAR,
        },
    )


class CambiarClaveView(PasswordChangeView):
    """Cambio de contraseña del usuario autenticado, con el mismo estilo
    Bootstrap del resto del sistema."""

    form_class = CambiarClaveForm
    template_name = "usuarios/cambiar_clave.html"
    success_url = reverse_lazy("cambiar_clave_hecho")


@login_required
def cambiar_clave_hecho(request):
    return render(request, "usuarios/cambiar_clave_hecho.html")


def _rol_de(usuario):
    return "Administrador" if usuario.groups.filter(name="Administrador").exists() else "Auditor"


@rol_requerido("Administrador")
def usuarios_lista(request):
    """Panel de gestión de usuarios, dentro de la propia app (no en
    /admin/ de Django), para que la Socia Principal pueda dar de alta
    o editar cuentas sin depender del panel técnico."""
    # Se excluye la propia cuenta y a cualquier superusuario: el
    # superusuario es la cuenta técnica de administración de Django
    # (por encima de los roles de negocio), no un "Auditor" ni
    # "Administrador" que la Socia Principal deba gestionar aquí.
    usuarios_qs = (
        User.objects.exclude(pk=request.user.pk)
        .exclude(is_superuser=True)
        .prefetch_related("groups")
        .order_by("username")
    )
    usuarios = [
        {"objeto": u, "rol": _rol_de(u)}
        for u in usuarios_qs
    ]
    return render(request, "usuarios/usuarios_lista.html", {"usuarios": usuarios})


@rol_requerido("Administrador")
def usuario_crear(request):
    from registros.auditoria import registrar_creacion

    if request.method == "POST":
        form = UsuarioCrearForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            registrar_creacion(usuario, request.user)
            messages.success(
                request, f"Cuenta '{usuario.username}' creada correctamente como {form.cleaned_data['rol']}."
            )
            return redirect("usuarios_lista")
    else:
        form = UsuarioCrearForm()
    return render(
        request,
        "usuarios/usuario_form.html",
        {"form": form, "titulo": "Crear nuevo usuario", "es_creacion": True},
    )


@rol_requerido("Administrador")
def usuario_editar(request, usuario_id):
    from registros.auditoria import registrar_edicion
    from registros.models import HistorialCambio

    usuario_obj = get_object_or_404(User, pk=usuario_id)
    rol_actual = _rol_de(usuario_obj)

    if usuario_obj.pk == request.user.pk:
        messages.error(request, "No puedes editar tu propia cuenta desde este panel.")
        return redirect("usuarios_lista")

    if usuario_obj.is_superuser:
        messages.error(
            request,
            "Las cuentas de superusuario se administran desde /admin/, no desde este panel.",
        )
        return redirect("usuarios_lista")

    if request.method == "POST":
        valores_anteriores = {"email": usuario_obj.email, "is_active": usuario_obj.is_active}
        form = UsuarioEditarForm(request.POST, instance=usuario_obj)
        if form.is_valid():
            form.save()
            registrar_edicion(usuario_obj, valores_anteriores, request.user, ["email", "is_active"])

            nuevo_rol = form.cleaned_data["rol"]
            if nuevo_rol != rol_actual:
                usuario_obj.groups.clear()
                usuario_obj.groups.add(Group.objects.get(name=nuevo_rol))
                HistorialCambio.objects.create(
                    modelo="User",
                    objeto_id=usuario_obj.pk,
                    objeto_descripcion=str(usuario_obj),
                    accion="edicion",
                    campo="rol",
                    valor_anterior=rol_actual,
                    valor_nuevo=nuevo_rol,
                    usuario=request.user,
                )

            messages.success(request, "Usuario actualizado correctamente.")
            return redirect("usuarios_lista")
    else:
        form = UsuarioEditarForm(instance=usuario_obj, initial={"rol": rol_actual})

    return render(
        request,
        "usuarios/usuario_form.html",
        {
            "form": form,
            "titulo": f"Editar usuario: {usuario_obj.username}",
            "usuario_obj": usuario_obj,
            "es_creacion": False,
        },
    )
