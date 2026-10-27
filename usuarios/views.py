import logging
import time
import hashlib
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from . import correos
from .autenticacion_2fa import CLAVE_SESION_USUARIO_PENDIENTE, enviar_codigo, validar_codigo
from .decorators import rol_requerido
from .forms import (
    CambiarClaveForm,
    CodigoVerificacionForm,
    DatosCuentaForm,
    DatosPersonalesForm,
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
    return f"login_intentos_{_identidad_login(username)}"


def _clave_bloqueo_login(username):
    return f"login_bloqueo_{_identidad_login(username)}"


def _identidad_login(username):
    # AuthenticationForm normaliza NFKC: el límite debe usar la misma identidad.
    # El hash mantiene la clave de caché acotada incluso con entradas hostiles.
    identidad = unicodedata.normalize("NFKC", username.strip()).casefold()
    return hashlib.sha256(identidad.encode("utf-8")).hexdigest()


CLAVE_SESION_BLOQUEO = "login_bloqueo_hasta"
MENSAJE_BLOQUEO = "Demasiados intentos fallidos. Acceso bloqueado temporalmente."


def _registrar_clave_actual_si_falta(usuario, clave):
    """Cuentas creadas antes del historial de contraseñas: al iniciar sesión se guarda la
    raíz cifrada de la clave actual, para que la siguiente no pueda ser una variante."""
    from django.contrib.auth.hashers import make_password

    from .models import HistorialClave
    from .validadores import raiz

    if clave and not HistorialClave.objects.filter(usuario=usuario).exists():
        HistorialClave.objects.create(usuario=usuario, raiz_hash=make_password(raiz(clave)))


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

            intentos_restantes = MAXIMO_INTENTOS_LOGIN - intentos
            if 0 < intentos_restantes <= 2 and form.errors.get("__all__"):
                texto = "Te queda 1 intento" if intentos_restantes == 1 else f"Te quedan {intentos_restantes} intentos"
                form.errors["__all__"] = form.error_class([
                    f"{form.errors['__all__'][0]} {texto} antes de un bloqueo de {MINUTOS_BLOQUEO_LOGIN} minutos."
                ])
        return super().form_invalid(form)

    def form_valid(self, form):
        username = form.data.get("username", "")
        if username:
            cache.delete(_clave_intentos_login(username))
            cache.delete(_clave_bloqueo_login(username))
        self.request.session.pop(CLAVE_SESION_BLOQUEO, None)

        usuario = form.get_user()
        _registrar_clave_actual_si_falta(usuario, form.cleaned_data.get("password", ""))

        if not usuario.email:
            messages.error(
                self.request,
                "La cuenta no tiene correo registrado para el código de verificación. "
                "Contactar al administrador del sistema.",
            )
            return redirect("login")

        self.request.session[CLAVE_SESION_USUARIO_PENDIENTE] = usuario.pk
        self.request.session["sesion_2fa_backend"] = usuario.backend
        self.request.session[CLAVE_SESION_INICIO_2FA] = time.time()
        self.request.session[CLAVE_SESION_FALLOS_2FA] = 0
        self.request.session[CLAVE_SESION_REENVIOS_2FA] = 0
        self.request.session[CLAVE_SESION_HUELLA_2FA] = _huella_2fa(usuario)
        if not _enviar_codigo_seguro(self.request, usuario):
            _limpiar_sesion_2fa(self.request)
            return redirect("login")
        messages.info(
            self.request,
            f"Código de verificación enviado a {usuario.email}.",
        )
        return redirect("verificar_codigo")


# Endurecimiento del segundo paso (2FA)
CLAVE_SESION_INICIO_2FA = "sesion_2fa_inicio"
CLAVE_SESION_FALLOS_2FA = "sesion_2fa_fallos"
CLAVE_SESION_REENVIOS_2FA = "sesion_2fa_reenvios"
CLAVE_SESION_HUELLA_2FA = "sesion_2fa_huella"
CLAVE_SESION_CODIGO_2FA = "sesion_2fa_codigo"
MINUTOS_VALIDEZ_PASO_2FA = 15  # tiempo máximo entre la contraseña y el código
MAXIMO_FALLOS_2FA = 10  # fallos totales, sumando todos los códigos reenviados
MAXIMO_REENVIOS_2FA = 3
SEGUNDOS_ESPERA_REENVIO = 60


def _huella_2fa(usuario):
    return salted_hmac("usuarios.paso_2fa", usuario.get_session_auth_hash() + "|" + usuario.email).hexdigest()


def _limpiar_sesion_2fa(request):
    for clave in (
        CLAVE_SESION_USUARIO_PENDIENTE, "sesion_2fa_backend", CLAVE_SESION_INICIO_2FA,
        CLAVE_SESION_FALLOS_2FA, CLAVE_SESION_REENVIOS_2FA,
        CLAVE_SESION_HUELLA_2FA, CLAVE_SESION_CODIGO_2FA,
    ):
        request.session.pop(clave, None)


def _enviar_codigo_seguro(request, usuario):
    """Envía el código 2FA; si el correo falla muestra un mensaje y no inicia sesión."""
    try:
        codigo = enviar_codigo(usuario, request)
        request.session[CLAVE_SESION_CODIGO_2FA] = codigo.pk
        return True
    except Exception:
        logger.exception("No se pudo enviar el código 2FA al usuario %s", usuario.pk)
        messages.error(
            request,
            "No se pudo enviar el código de verificación. Intentar de nuevo en unos "
            "minutos o contactar al administrador del sistema.",
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
        messages.error(request, "La verificación expiró. Iniciar sesión de nuevo.")
        return redirect("login")

    usuario = User.objects.filter(pk=usuario_id).first()
    inicio = request.session.get(CLAVE_SESION_INICIO_2FA, 0)
    if (
        usuario is None
        or not usuario.is_active
        or not request.session.get(CLAVE_SESION_CODIGO_2FA)
        or not constant_time_compare(request.session.get(CLAVE_SESION_HUELLA_2FA, ""), _huella_2fa(usuario))
        or time.time() - inicio > MINUTOS_VALIDEZ_PASO_2FA * 60
    ):
        _limpiar_sesion_2fa(request)
        messages.error(request, "La verificación expiró. Iniciar sesión de nuevo.")
        return redirect("login")

    aviso_intentos = None
    if request.method == "POST":
        if "cancelar" in request.POST:
            usuario.codigos_verificacion.filter(pk=request.session.get(CLAVE_SESION_CODIGO_2FA)).update(usado=True)
            _limpiar_sesion_2fa(request)
            return redirect("login")
        if "reenviar" in request.POST:
            reenvios = request.session.get(CLAVE_SESION_REENVIOS_2FA, 0)
            ultimo = usuario.codigos_verificacion.order_by("-creado").first()
            if reenvios >= MAXIMO_REENVIOS_2FA:
                messages.error(
                    request,
                    "Máximo de reenvíos alcanzado. Iniciar sesión de nuevo.",
                )
            elif ultimo and (timezone.now() - ultimo.creado).total_seconds() < SEGUNDOS_ESPERA_REENVIO:
                messages.warning(
                    request,
                    f"Deben pasar {SEGUNDOS_ESPERA_REENVIO} segundos entre reenvíos.",
                )
            elif _enviar_codigo_seguro(request, usuario):
                request.session[CLAVE_SESION_REENVIOS_2FA] = reenvios + 1
                messages.info(request, f"Nuevo código enviado a {usuario.email}.")
            return redirect("verificar_codigo")

        form = CodigoVerificacionForm(request.POST)
        if form.is_valid():
            ok, error = validar_codigo(usuario, form.cleaned_data["codigo"], request.session[CLAVE_SESION_CODIGO_2FA])
            if ok:
                usuario.backend = request.session.get(
                    "sesion_2fa_backend", "django.contrib.auth.backends.ModelBackend"
                )
                _limpiar_sesion_2fa(request)
                auth_login(request, usuario)
                return redirect(_destino_tras_login(usuario))
            form.add_error("codigo", error)
        # También cuentan los códigos malformados; reenviar no reinicia el límite.
        fallos = request.session.get(CLAVE_SESION_FALLOS_2FA, 0) + 1
        request.session[CLAVE_SESION_FALLOS_2FA] = fallos
        if fallos >= MAXIMO_FALLOS_2FA:
            _limpiar_sesion_2fa(request)
            messages.error(request, "Demasiados intentos fallidos. Vuelve a iniciar sesión.")
            return redirect("login")
        quedan = MAXIMO_FALLOS_2FA - fallos
        if quedan <= 3:
            aviso_intentos = f"Te {'queda 1 intento' if quedan == 1 else f'quedan {quedan} intentos'}."
    else:
        form = CodigoVerificacionForm()

    ultimo = usuario.codigos_verificacion.order_by("-creado").first()
    espera = max(0, int(SEGUNDOS_ESPERA_REENVIO - (timezone.now() - ultimo.creado).total_seconds()) + 1) if ultimo else 0
    restantes = max(0, MAXIMO_REENVIOS_2FA - request.session.get(CLAVE_SESION_REENVIOS_2FA, 0))
    return render(request, "usuarios/verificar_codigo.html", {
        "form": form, "correo": usuario.email, "espera_reenvio": espera,
        "reenvios_restantes": restantes, "aviso_intentos": aviso_intentos,
    })


@login_required
def home(request):
    """Página principal tras iniciar sesión: funciona como panel/dashboard."""
    from registros.models import CargaArchivo, EmpresaAuditada  # import local: evita acoplar usuarios - registros a nivel de módulo

    total_empresas = EmpresaAuditada.objects.count()
    cargas = CargaArchivo.objects.select_related("empresa", "gestion", "usuario")
    total_cargas = cargas.count()
    from registros.estados import anotar_avisos_pendientes, filtrar_por_revision

    cargas_por_revisar = (
        filtrar_por_revision(anotar_avisos_pendientes(cargas), "en_revision").count()
        + filtrar_por_revision(anotar_avisos_pendientes(cargas), "por_confirmar").count()
    )
    ultimas_cargas = anotar_avisos_pendientes(cargas).order_by("-fecha_carga")[:5]

    from analisis.models import ResultadoTransaccion

    atipicas_por_validar = ResultadoTransaccion.objects.filter(
        atipica=True, revision="pendiente", analisis__estado="completado",
    ).exclude(analisis__carga__estado="anulada").count()
    cargas_en_cola = cargas.filter(estado="pendiente").count()

    pendientes_revision = anotar_avisos_pendientes(cargas).filter(
        estado="con_observaciones", registros_validos__gt=0,
    ).order_by("fecha_carga")
    trabajo_pendiente = list(pendientes_revision[:4])
    for carga in trabajo_pendiente:
        carga.accion_pendiente = "Revisar avisos" if carga.avisos_pendientes_anotado else "Confirmar carga"
        carga.destino_pendiente = "?pestana=avisos" if carga.avisos_pendientes_anotado else "#confirmar-carga"
    cargas_atipicas = cargas.filter(
        estado="validado", analisis__estado="completado",
        analisis__resultados__atipica=True, analisis__resultados__revision="pendiente",
    ).distinct().order_by("fecha_carga")[:4]

    return render(
        request,
        "usuarios/home.html",
        {
            "total_empresas": total_empresas,
            "total_cargas": total_cargas,
            "cargas_por_revisar": cargas_por_revisar,
            "ultimas_cargas": ultimas_cargas,
            "atipicas_por_validar": atipicas_por_validar,
            "cargas_en_cola": cargas_en_cola,
            "trabajo_pendiente": trabajo_pendiente,
            "cargas_atipicas_pendientes": cargas_atipicas,
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
        correos.enviar(
            "correo_cambiado", usuario, correo_anterior,
            asunto="Cambio de correo de la cuenta - ST&S Auditores",
            texto=lambda ref: (
                f"Cuenta: {usuario.username}\n\n"
                "El correo asociado a la cuenta del sistema de apoyo a la auditoría "
                f"se cambió a {usuario.email}. Los códigos de verificación llegarán "
                "ahora a esa dirección.\n\n"
                "Si el cambio no fue realizado por el titular de la cuenta, comunicarlo de "
                "inmediato al administrador del sistema.\n\n"
                f"Correo automático, no responder. Referencia: {ref}"
            ),
            fallar_en_silencio=True,
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
    elif request.method == "POST" and request.POST.get("accion") == "personales":
        from registros.auditoria import registrar_edicion

        anteriores = {"first_name": request.user.first_name, "last_name": request.user.last_name}
        form_personales = DatosPersonalesForm(request.POST, instance=request.user,
                                              es_administrador=_rol_de(request.user) == "Administrador")
        if form_personales.is_valid():
            usuario = form_personales.save()
            registrar_edicion(usuario, anteriores, request.user, ["first_name", "last_name"])
            nuevo = usuario.get_full_name()
            anterior = f"{anteriores['first_name']} {anteriores['last_name']}".strip()
            if usuario.email and nuevo != anterior:
                correos.enviar(
                    "nombre_cambiado", usuario, usuario.email,
                    asunto="Cambio de nombre en la cuenta - ST&S Auditores",
                    texto=lambda ref: (
                        f"Cuenta: {usuario.username}\n\n"
                        f"El nombre de la cuenta cambió de \"{anterior or 'sin nombre'}\" a \"{nuevo}\". "
                        "Este nombre aparece en las firmas de los reportes.\n\n"
                        "Si el cambio no fue realizado por el titular de la cuenta, comunicarlo de inmediato "
                        "al administrador del sistema.\n\n"
                        f"Correo automático, no responder. Referencia: {ref}"),
                    request=request, fallar_en_silencio=True)
            messages.success(request, "Nombre actualizado. Se usará en las firmas de los reportes.")
            return redirect("perfil")
        form_avatar = PerfilForm(instance=perfil_usuario)
        form_datos = DatosCuentaForm(instance=request.user)
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
    if request.POST.get("accion") != "personales":
        form_personales = DatosPersonalesForm(instance=request.user)

    return render(
        request,
        "usuarios/perfil.html",
        {
            "form": form_avatar,
            "form_datos": form_datos,
            "form_personales": form_personales,
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
        _avisar_cambio_clave(usuario, "clave_cambiada", self.request)


@login_required
def cambiar_clave_hecho(request):
    return render(request, "usuarios/cambiar_clave_hecho.html")


ETIQUETAS_CAMPO_USUARIO = {
    "first_name": "Nombre", "last_name": "Apellido", "email": "Correo", "is_active": "Cuenta activa",
    "rol": "Rol", "password": "Contraseña",
}


def _rol_de(usuario):
    return "Administrador" if usuario.groups.filter(name="Administrador").exists() else "Auditor"


@rol_requerido("Administrador")
def usuarios_lista(request):
    """Gestión de usuarios para el rol Administrador."""
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
    resumen = {
        "total": len(usuarios),
        "activos": sum(1 for u in usuarios if u["objeto"].is_active),
        "administradores": sum(1 for u in usuarios if u["rol"] == "Administrador"),
    }
    return render(request, "usuarios/usuarios_lista.html", {"usuarios": usuarios, "resumen": resumen})


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
        messages.error(request, 'La cuenta propia se edita desde "Mi perfil".')
        return redirect("usuarios_lista")

    if usuario_obj.is_superuser:
        messages.error(
            request,
            "Las cuentas de superusuario se administran desde /admin/, no desde este panel.",
        )
        return redirect("usuarios_lista")

    if request.method == "POST":
        valores_anteriores = {"first_name": usuario_obj.first_name, "last_name": usuario_obj.last_name,
                              "email": usuario_obj.email, "is_active": usuario_obj.is_active}
        form = UsuarioEditarForm(request.POST, instance=usuario_obj)
        if form.is_valid():
            form.save()
            registrar_edicion(usuario_obj, valores_anteriores, request.user, ["first_name", "last_name", "email", "is_active"])

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
            "rol_actual": rol_actual,
            "es_creacion": False,
            "cambios": [
                {"etiqueta": ETIQUETAS_CAMPO_USUARIO.get(c.campo, c.campo or c.get_accion_display()),
                 "anterior": {"True": "Sí", "False": "No"}.get(c.valor_anterior, c.valor_anterior),
                 "nuevo": {"True": "Sí", "False": "No"}.get(c.valor_nuevo, c.valor_nuevo),
                 "fecha": c.fecha, "por": c.usuario}
                for c in HistorialCambio.objects.filter(modelo="User", objeto_id=usuario_obj.pk)
                .select_related("usuario").order_by("-fecha")[:8]
            ],
        },
    )


# La respuesta es siempre la misma, exista o no el correo, para no revelar qué cuentas hay.
MAXIMO_SOLICITUDES_RECUPERACION = 3
MINUTOS_VENTANA_RECUPERACION = 15


def _avisar_cambio_clave(usuario, tipo, request=None):
    """Aviso por correo de que la contraseña cambió (por el usuario o por recuperación)."""
    if not usuario.email:
        return
    como = ("se restableció mediante el enlace de recuperación enviado al correo"
            if tipo == "clave_restablecida" else "acaba de cambiar desde el perfil")
    correos.enviar(
        tipo, usuario, usuario.email,
        asunto="Cambio de contraseña - ST&S Auditores",
        texto=lambda ref: (
            f"Cuenta: {usuario.username}\n\n"
            f"La contraseña del sistema de apoyo a la auditoría {como}. "
            "Si el cambio fue realizado por el titular de la cuenta, no se requiere "
            "ninguna acción.\n\n"
            "En caso contrario, comunicarlo de inmediato al administrador del sistema.\n\n"
            f"Correo automático, no responder. Referencia: {ref}"
        ),
        request=request,
        fallar_en_silencio=True,
    )


def _limite_recuperacion_superado(request, correo):
    """Máximo de solicitudes por correo y por IP en la ventana de tiempo (evita abuso)."""
    identidades = [
        "recuperar_correo_" + hashlib.sha256(correo.casefold().encode("utf-8")).hexdigest(),
        "recuperar_ip_" + hashlib.sha256((request.META.get("REMOTE_ADDR") or "").encode()).hexdigest(),
    ]
    superado = False
    for clave in identidades:
        cantidad = cache.get(clave, 0) + 1
        cache.set(clave, cantidad, timeout=MINUTOS_VENTANA_RECUPERACION * 60)
        superado = superado or cantidad > MAXIMO_SOLICITUDES_RECUPERACION
    return superado


def recuperar_clave(request):
    """Paso 1: pedir el correo de la cuenta."""
    from django.contrib.auth.tokens import default_token_generator
    from django.template.loader import render_to_string
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from .forms import RecuperarClaveForm

    if request.user.is_authenticated:
        return redirect("cambiar_clave")
    form = RecuperarClaveForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        correo = form.cleaned_data["email"]
        if _limite_recuperacion_superado(request, correo):
            logger.warning("Recuperación de contraseña: límite de solicitudes superado.")
            return redirect("recuperar_clave_enviado")
        cuentas = list(User.objects.filter(email__iexact=correo, is_active=True)[:2])
        if len(cuentas) == 1 and cuentas[0].has_usable_password():
            usuario = cuentas[0]
            enlace = request.build_absolute_uri(reverse_lazy("restablecer_clave", kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(usuario.pk)),
                "token": default_token_generator.make_token(usuario),
            }))
            minutos = settings.PASSWORD_RESET_TIMEOUT // 60
            contexto = {
                "nombre": usuario.first_name or usuario.username,
                "usuario": usuario.username,
                "enlace": enlace,
                "minutos": minutos,
                "vence": timezone.localtime() + timedelta(minutes=minutos),
                "fecha_solicitud": timezone.localtime(),
                "ip": request.META.get("REMOTE_ADDR") or "No disponible",
            }
            try:
                correos.enviar(
                    "recuperar_clave", usuario, usuario.email,
                    asunto="Restablecer contraseña - ST&S Auditores",
                    texto=lambda ref: render_to_string("usuarios/correo_recuperar.txt", {**contexto, "referencia": ref}),
                    html=lambda ref: render_to_string("usuarios/correo_recuperar.html", {**contexto, "referencia": ref}),
                    request=request,
                )
            except Exception:
                # Queda en RegistroCorreo y en sistema.log; al usuario se le muestra lo mismo.
                pass
        elif len(cuentas) > 1:
            logger.warning("Recuperación de contraseña: correo repetido en varias cuentas; no se envía.")
        return redirect("recuperar_clave_enviado")
    return render(request, "usuarios/recuperar_clave.html", {"form": form})


def recuperar_clave_enviado(request):
    return render(request, "usuarios/recuperar_clave_enviado.html",
                  {"minutos": settings.PASSWORD_RESET_TIMEOUT // 60})


class RestablecerClaveView(PasswordResetConfirmView):
    """Paso 2: el enlace del correo lleva aquí; se elige la contraseña nueva.

    Django valida el token (un solo uso: deja de servir al cambiar la contraseña, y
    vence en PASSWORD_RESET_TIMEOUT) y lo saca de la URL antes de mostrar el formulario.
    """

    template_name = "usuarios/restablecer_clave.html"
    success_url = reverse_lazy("restablecer_clave_hecho")
    post_reset_login = False

    def get_user(self, uidb64):
        usuario = super().get_user(uidb64)
        return usuario if usuario and usuario.is_active and usuario.has_usable_password() else None

    def get_form_class(self):
        from .forms import RestablecerClaveForm

        return RestablecerClaveForm

    def form_valid(self, form):
        from registros.models import HistorialCambio

        respuesta = super().form_valid(form)
        usuario = form.user
        HistorialCambio.objects.create(
            modelo="User", objeto_id=usuario.pk, objeto_descripcion=str(usuario),
            accion="edicion", campo="password", valor_anterior="",
            valor_nuevo="(restablecida por correo)", usuario=usuario,
        )
        # Django invalida las sesiones al cambiar el hash; también se retira el bloqueo de login.
        cache.delete(_clave_intentos_login(usuario.username))
        cache.delete(_clave_bloqueo_login(usuario.username))
        _avisar_cambio_clave(usuario, "clave_restablecida", self.request)
        return respuesta


def restablecer_clave_hecho(request):
    return render(request, "usuarios/restablecer_clave_hecho.html")
