class SinCacheMiddleware:
    """Evita que el navegador guarde una copia en caché de las páginas.

    Sin esto, después de cerrar sesión, el botón "atrás" del navegador
    puede mostrar una versión guardada en caché de una página que se
    vio estando logueado, aunque la sesión en el servidor ya esté
    cerrada -- da la impresión de que la sesión sigue activa. No es un
    bypass real: cualquier acción de verdad (un clic a un link, enviar
    un formulario) vuelve a pedir login, porque la sesión del servidor
    sí terminó. Pero para que no confunda visualmente, se le pide al
    navegador que jamás guarde estas páginas en caché.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        return response
