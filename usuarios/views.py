import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

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

logger = logging.getLogger(__name__)

# Bloqueo por intentos fallidos de login (RNF-03).
MAXIMO_INTENTOS_LOGIN = 5
MINUTOS_BLOQUEO_LOGIN = 15


def _clave_intentos_login(username):
    return f"login_intentos_{username.strip().lower()}"


def _clave_bloqueo_login(username):
    return f"login_bloqueo_{username.strip().lower()}"


CLAVE_SESION_BLOQUEO = "login_bloqueo_hasta"
MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Espera a que termine el contador para volver a intentar."


class LoginSiempreInicioView(LoginView):
    """Login con bloqueo por intentos fallidos y segundo factor por correo."""

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            username = request.POST.get("username", "")
            if username:
                clave = _clave_intentos_login(username)
                intentos = cache.get(clave, 0)
                if intentos >= MAXIMO_INTENTOS_LOGIN:
                    hasta = cache.get(_clave_bloqueo_login(username)) or (
                        time.time() + MINUTOS_BLOQUEO_LOGIN * 60
                    )
                    request.session[CLAVE_SESION_BLOQUEO] = hasta
                    messages.error(request, MENSAJE_BLOQUEO, extra_tags="bloqueo")
                    return redirect("login")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        hasta = self.request.session.get(CLAVE_SESION_BLOQUEO)
        restante = int(hasta - time.time()) if hasta else 0
        if restante > 0:
            contexto["segundos_bloqueo"] = restante
            contexto["segundos_bloqueo_total"] = MINUTOS_BLOQUEO_LOGIN * 60
        elif hasta:
            self.request.session.pop(CLAVE_SESION_BLOQUEO, None)
        return contexto

    def form_invalid(self, form):
        username = form.data.get("username", "")
        if username:
            clave = _clave_intentos_login(username)
            intentos = cache.get(clave, 0) + 1
            cache.set(clave, intentos, timeout=MINUTOS_BLOQUEO_LOGIN * 60)

            if intentos >= MAXIMO_INTENTOS_LOGIN:
                hasta = time.time() + MINUTOS_BLOQUEO_LOGIN * 60
                cache.set(_clave_bloqueo_login(username), hasta, timeout=MINUTOS_BLOQUEO_LOGIN * 60)
                self.request.session[CLAVE_SESION_BLOQUEO] = hasta
                messages.error(self.request, MENSAJE_BLOQUEO, extra_tags="bloqueo")
                return redirect("login")

            # Aviso en los dos últimos intentos antes del bloqueo.
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
        # Credenciales correctas: falta el segundo factor.
        username = form.data.get("username", "")
        if username:
            cache.delete(_clave_intentos_login(username))
            cache.delete(_clave_bloqueo_login(username))
        self.request.session.pop(CLAVE_SESION_BLOQUEO, None)

        usuario = form.get_user()

        if not usuario.email:
            messages.error(
                self.request,
                "Tu cuenta no tiene un correo registrado, necesario para el "
                "código de verificación. Contacta con el administrador del sistema.",
            )
            return redirect("login")

        self.request.session[CLAVE_SESION_USUARIO_PENDIENTE] = usuario.pk
        self.request.session["sesion_2fa_backend"] = usuario.backend
        self.request.session[CLAVE_SESION_INICIO_2FA] = time.time()
        self.request.session[CLAVE_SESION_FALLOS_2FA] = 0
        self.request.session[CLAVE_SESION_REENVIOS_2FA] = 0
        if not _enviar_codigo_seguro(self.request, usuario):
            _limpiar_sesion_2fa(self.request)
            return redirect("login")
        messages.info(
            self.request,
            f"Te enviamos un código de verificación a {usuario.email}.",
        )
        return redirect("verificar_codigo")


# --- Endurecimiento del segundo paso (2FA) ---
CLAVE_SESION_INICIO_2FA = "sesion_2fa_inicio"
CLAVE_SESION_FALLOS_2FA = "sesion_2fa_fallos"
CLAVE_SESION_REENVIOS_2FA = "sesion_2fa_reenvios"
MINUTOS_VALIDEZ_PASO_2FA = 15  # tiempo máximo entre la contraseña y el código
MAXIMO_FALLOS_2FA = 10  # fallos totales, sumando todos los códigos reenviados
MAXIMO_REENVIOS_2FA = 3
SEGUNDOS_ESPERA_REENVIO = 60


def _limpiar_sesion_2fa(request):
    for clave in (
        CLAVE_SESION_USUARIO_PENDIENTE, "sesion_2fa_backend", CLAVE_SESION_INICIO_2FA,
        CLAVE_SESION_FALLOS_2FA, CLAVE_SESION_REENVIOS_2FA,
    ):
        request.session.pop(clave, None)


def _enviar_codigo_seguro(request, usuario):
    """Envía el código 2FA; si el correo falla muestra un mensaje y no inicia sesión."""
    try:
        enviar_codigo(usuario)
        return True
    except Exception:
        logger.exception("No se pudo enviar el código 2FA al usuario %s", usuario.pk)
        messages.error(
            request,
            "No se pudo enviar el código de verificación por correo. Intenta de nuevo "
            "en unos minutos; si el problema sigue, avisa al administrador del sistema.",
        )
        return False


def _destino_tras_login(usuario):
    if usuario.is_superuser:
        return reverse_lazy("admin:index")
    return reverse_lazy("home")


def verificar_codigo(request):
    """Segundo paso del login: pide el código de 6 dígitos enviado por correo."""
    usuario_id = request.session.get(CLAVE_SESION_USUARIO_PENDIENTE)
    if not usuario_id:
        messages.error(request, "Tu sesión de verificación expiró. Inicia sesión de nuevo.")
        return redirect("login")

    usuario = User.objects.filter(pk=usuario_id).first()
    inicio = request.session.get(CLAVE_SESION_INICIO_2FA, 0)
    if (
        usuario is None
        or not usuario.is_active
        or time.time() - inicio > MINUTOS_VALIDEZ_PASO_2FA * 60
    ):
        # Paso pendiente vencido, o la cuenta se desactivó mientras tanto.
        _limpiar_sesion_2fa(request)
        messages.error(request, "Tu sesión de verificación expiró. Inicia sesión de nuevo.")
        return redirect("login")

    if request.method == "POST":
        if "reenviar" in request.POST:
            reenvios = request.session.get(CLAVE_SESION_REENVIOS_2FA, 0)
            ultimo = usuario.codigos_verificacion.order_by("-creado").first()
            if reenvios >= MAXIMO_REENVIOS_2FA:
                messages.error(
                    request,
                    "Alcanzaste el máximo de reenvíos. Inicia sesión de nuevo para recibir otro código.",
                )
            elif ultimo and (timezone.now() - ultimo.creado).total_seconds() < SEGUNDOS_ESPERA_REENVIO:
                messages.warning(
                    request,
                    f"Espera {SEGUNDOS_ESPERA_REENVIO} segundos entre reenvíos del código.",
                )
            elif _enviar_codigo_seguro(request, usuario):
                request.session[CLAVE_SESION_REENVIOS_2FA] = reenvios + 1
                messages.info(request, f"Te enviamos un nuevo código a {usuario.email}.")
            return redirect("verificar_codigo")

        form = CodigoVerificacionForm(request.POST)
        if form.is_valid():
            ok, error = validar_codigo(usuario, form.cleaned_data["codigo"])
            if ok:
                usuario.backend = request.session.get(
                    "sesion_2fa_backend", "django.contrib.auth.backends.ModelBackend"
                )
                _limpiar_sesion_2fa(request)
                auth_login(request, usuario)
                return redirect(_destino_tras_login(usuario))
            # El límite de fallos es para todo el paso; reenviar el código no lo
            # reinicia.
            fallos = request.session.get(CLAVE_SESION_FALLOS_2FA, 0) + 1
            request.session[CLAVE_SESION_FALLOS_2FA] = fallos
            if fallos >= MAXIMO_FALLOS_2FA:
                _limpiar_sesion_2fa(request)
                messages.error(
                    request,
                    "Demasiados códigos incorrectos. Inicia sesión de nuevo.",
                )
                return redirect("login")
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
    """Página principal tras iniciar sesión: funciona como panel/dashboard."""
    from registros.models import CargaArchivo, EmpresaAuditada  # import local: evita acoplar usuarios <-> registros a nivel de módulo

    total_empresas = EmpresaAuditada.objects.count()
    cargas = CargaArchivo.objects.select_related("empresa", "gestion", "usuario")
    total_cargas = cargas.count()
    from registros.estados import anotar_avisos_pendientes, filtrar_por_revision

    # Cargas que esperan trabajo del auditor (avisos pendientes o confirmación).
    cargas_por_revisar = (
        filtrar_por_revision(anotar_avisos_pendientes(cargas), "en_revision").count()
        + filtrar_por_revision(anotar_avisos_pendientes(cargas), "por_confirmar").count()
    )
    ultimas_cargas = anotar_avisos_pendientes(cargas).order_by("-fecha_carga")[:5]

    return render(
        request,
        "usuarios/home.html",
        {
            "total_empresas": total_empresas,
            "total_cargas": total_cargas,
            "cargas_por_revisar": cargas_por_revisar,
            "ultimas_cargas": ultimas_cargas,
        },
    )


def _registrar_y_avisar_cambio_correo(usuario, correo_anterior, autor):
    """Registra el cambio de correo y avisa a la dirección anterior."""
    from registros.models import HistorialCambio  # import local, ver `home`

    HistorialCambio.objects.create(
        modelo="User",
        objeto_id=usuario.pk,
        objeto_descripcion=str(usuario),
        accion="edicion",
        campo="email",
        valor_anterior=correo_anterior or "",
        valor_nuevo=usuario.email,
        usuario=autor,
    )
    if correo_anterior:
        send_mail(
            subject="Se cambió el correo de tu cuenta - ST&S Auditores",
            message=(
                f"Hola {usuario.first_name or usuario.username},\n\n"
                "El correo asociado a tu cuenta del sistema de apoyo a la auditoría "
                f"se cambió a {usuario.email}. Los códigos de verificación llegarán "
                "ahora a esa dirección.\n\n"
                "Si no hiciste este cambio, contacta de inmediato al administrador del sistema."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[correo_anterior],
            fail_silently=True,
        )


@login_required
def perfil(request):
    """Perfil del usuario autenticado: datos de la cuenta y foto/avatar."""
    perfil_usuario, _creado = PerfilUsuario.objects.get_or_create(usuario=request.user)

    if request.method == "POST" and request.POST.get("accion") == "avatar":
        form_avatar = PerfilForm(request.POST, request.FILES, instance=perfil_usuario)
        form_datos = DatosCuentaForm(instance=request.user)
        if form_avatar.is_valid():
            # Foto subida y avatar predefinido son alternativos.
            if form_avatar.cleaned_data.get("avatar_preset") and not request.FILES.get("avatar"):
                perfil_usuario.avatar.delete(save=False)
            elif request.FILES.get("avatar"):
                form_avatar.instance.avatar_preset = ""
            form_avatar.save()
            messages.success(request, "Avatar actualizado correctamente.")
            return redirect("perfil")
    elif request.method == "POST" and request.POST.get("accion") == "datos":
        correo_anterior = request.user.email
        form_datos = DatosCuentaForm(request.POST, instance=request.user)
        form_avatar = PerfilForm(instance=perfil_usuario)
        if form_datos.is_valid():
            usuario = form_datos.save()
            if usuario.email.lower() != (correo_anterior or "").lower():
                _registrar_y_avisar_cambio_correo(usuario, correo_anterior, request.user)
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
    """Cambio de contraseña; avisa por correo y queda en el historial."""

    form_class = CambiarClaveForm
    template_name = "usuarios/cambiar_clave.html"
    success_url = reverse_lazy("cambiar_clave_hecho")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        self._registrar_y_notificar_cambio()
        return respuesta

    def _registrar_y_notificar_cambio(self):
        from registros.models import HistorialCambio  # import local: evita acoplar usuarios <-> registros a nivel de módulo

        usuario = self.request.user
        HistorialCambio.objects.create(
            modelo="User",
            objeto_id=usuario.pk,
            objeto_descripcion=str(usuario),
            accion="edicion",
            campo="password",
            valor_anterior="",
            valor_nuevo="(cambiada por el usuario)",
            usuario=usuario,
        )
        if usuario.email:
            send_mail(
                subject="Tu contraseña fue cambiada - ST&S Auditores",
                message=(
                    f"Hola {usuario.first_name or usuario.username},\n\n"
                    "Tu contraseña del sistema de apoyo a la auditoría acaba de "
                    "cambiar. Si fuiste tú, no hace falta que hagas nada más.\n\n"
                    "Si NO fuiste tú quien la cambió, contacta de inmediato al "
                    "administrador del sistema."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[usuario.email],
                fail_silently=True,
            )


@login_required
def cambiar_clave_hecho(request):
    return render(request, "usuarios/cambiar_clave_hecho.html")


def _rol_de(usuario):
    return "Administrador" if usuario.groups.filter(name="Administrador").exists() else "Auditor"


@rol_requerido("Administrador")
def usuarios_lista(request):
    """Gestión de usuarios para el rol Administrador."""
    # Se excluyen la propia cuenta y los superusuarios.
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
