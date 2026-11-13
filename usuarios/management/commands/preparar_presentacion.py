"""Ajustes de una sola vez antes de la presentación. Solo toca usuarios y su historial;
empresas, cargas, avisos y atípicas no se tocan.

    python manage.py preparar_presentacion                       # lista usuarios e historial de usuarios
    python manage.py preparar_presentacion --usuario SOCIO_PRINCIPAL --nombre "Ana" --apellido "Pérez"
    python manage.py preparar_presentacion --borrar-ids 3,4,7    # simulación
    python manage.py preparar_presentacion --borrar-ids 3,4,7 --confirmar

Antes de cambiar o borrar algo guarda un respaldo JSON en _respaldo/.
"""
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from registros.models import HistorialCambio

User = get_user_model()


class Command(BaseCommand):
    help = "Cambia el nombre de un usuario y limpia el historial de usuarios hecho de prueba."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", help="Nombre de usuario (login) al que se le cambia el nombre.")
        parser.add_argument("--nombre")
        parser.add_argument("--apellido")
        parser.add_argument("--borrar-ids", nargs="+",
                            help="IDs del historial de usuarios a borrar, separados por espacio o coma.")
        parser.add_argument("--confirmar", action="store_true", help="Aplica los cambios; sin esto es simulación.")

    def handle(self, *args, **o):
        historial = HistorialCambio.objects.filter(modelo="User").select_related("usuario").order_by("fecha")
        if not (o["usuario"] or o["borrar_ids"]):
            self._listar(historial)
            return

        cambios_nombre = None
        if o["usuario"]:
            if not (o["nombre"] and o["apellido"]):
                raise CommandError("Indica --nombre y --apellido.")
            try:
                usuario = User.objects.get(username=o["usuario"])
            except User.DoesNotExist:
                raise CommandError(f"No existe el usuario {o['usuario']}.")
            cambios_nombre = (usuario, " ".join(o["nombre"].split()), " ".join(o["apellido"].split()))
            self.stdout.write(f"Nombre de {usuario.username}: '{usuario.get_full_name()}' -> "
                              f"'{cambios_nombre[1]} {cambios_nombre[2]}'")

        a_borrar = HistorialCambio.objects.none()
        if o["borrar_ids"]:
            texto = " ".join(o["borrar_ids"]).replace(",", " ")
            try:
                ids = sorted({int(x) for x in texto.split()})
            except ValueError:
                raise CommandError(f"--borrar-ids debe tener solo números; se recibió: {texto!r}")
            encontrados = dict(HistorialCambio.objects.filter(pk__in=ids).values_list("pk", "modelo"))
            no_existen = [i for i in ids if i not in encontrados]
            de_otro = {i: m for i, m in encontrados.items() if m.lower() != "user"}
            if no_existen or de_otro:
                raise CommandError(f"Recibidos: {ids}. No existen: {no_existen}. "
                                   f"No son de usuarios: {de_otro}. No se hizo nada.")
            a_borrar = HistorialCambio.objects.filter(pk__in=ids).select_related("usuario").order_by("fecha")
            self.stdout.write(f"Se borrarán {a_borrar.count()} registros del historial de usuarios:")
            for h in a_borrar:
                self.stdout.write("  " + self._linea(h))

        if not o["confirmar"]:
            self.stdout.write(self.style.WARNING("\nSimulación: no se cambió nada. Repite con --confirmar."))
            return

        respaldo = self._respaldar(cambios_nombre, a_borrar)
        with transaction.atomic():
            if cambios_nombre:
                usuario, nombre, apellido = cambios_nombre
                usuario.first_name, usuario.last_name = nombre, apellido
                usuario.save(update_fields=["first_name", "last_name"])
            a_borrar.delete()
        self.stdout.write(self.style.SUCCESS(f"\nListo. Respaldo en {respaldo}"))

    def _listar(self, historial):
        self.stdout.write("USUARIOS")
        for u in User.objects.order_by("id"):
            self.stdout.write(f"  {u.username:<20} nombre: '{u.get_full_name()}'  activo: {u.is_active}")
        self.stdout.write(f"\nHISTORIAL DE USUARIOS ({historial.count()})")
        for h in historial:
            self.stdout.write("  " + self._linea(h))
        self.stdout.write("\nEl historial de empresas, cargas, avisos y atípicas no se lista ni se toca.")

    @staticmethod
    def _linea(h):
        fecha = timezone.localtime(h.fecha).strftime("%d/%m/%Y %H:%M")
        return (f"id {h.pk:<5} {fecha}  {h.objeto_descripcion or h.objeto_id}  {h.campo or h.accion}: "
                f"'{h.valor_anterior[:40]}' -> '{h.valor_nuevo[:40]}'  (por {h.usuario.username})")

    @staticmethod
    def _respaldar(cambios_nombre, a_borrar):
        carpeta = Path(settings.BASE_DIR) / "_respaldo"
        carpeta.mkdir(exist_ok=True)
        archivo = carpeta / f"preparar_presentacion_{timezone.localtime():%Y%m%d_%H%M%S}.json"
        datos = {
            "nombre_anterior": None if not cambios_nombre else {
                "usuario": cambios_nombre[0].username,
                "first_name": cambios_nombre[0].first_name,
                "last_name": cambios_nombre[0].last_name,
            },
            "historial_borrado": [
                {"id": h.pk, "modelo": h.modelo, "objeto_id": h.objeto_id,
                 "objeto_descripcion": h.objeto_descripcion, "accion": h.accion, "campo": h.campo,
                 "valor_anterior": h.valor_anterior, "valor_nuevo": h.valor_nuevo,
                 "usuario": h.usuario.username, "fecha": h.fecha.isoformat()}
                for h in a_borrar
            ],
        }
        archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        return archivo
