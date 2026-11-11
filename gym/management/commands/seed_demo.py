"""
Carga datos de demostración para la defensa.

    python manage.py seed_demo                 # crea datos si no existen
    python manage.py seed_demo --password X    # contraseña para usuarios demo

Usuarios creados (misma contraseña para todos, por defecto la variable DEMO_PASSWORD):
    recepcion  -> grupo Recepcion
    ana, bruno, carla, diego, elena, felipe -> socios con distintos estados
"""
import os
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from gym import services
from gym.models import Clase, Instalacion, Membresia, Pago, Plan, Socio
from gym.permissions import GRUPO_RECEPCION, GRUPO_SOCIO
from gym.validators import calcular_dv

User = get_user_model()


def rut(cuerpo: int) -> str:
    return f"{cuerpo}-{calcular_dv(str(cuerpo))}"


class Command(BaseCommand):
    help = "Crea grupos, planes, socios, membresías, pagos y clases de demostración."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "FitLife2026!"))

    @transaction.atomic
    def handle(self, *args, **opts):
        pwd = opts["password"]
        hoy = timezone.localdate()

        # --- Grupos y permisos ------------------------------------------------
        recepcion, _ = Group.objects.get_or_create(name=GRUPO_RECEPCION)
        Group.objects.get_or_create(name=GRUPO_SOCIO)
        codenames = [
            f"{accion}_{modelo}"
            for modelo in ("socio", "membresia", "pago", "reserva", "sesion", "clase")
            for accion in ("view", "add", "change")
        ] + ["view_plan", "view_registroacceso", "view_instalacion"]
        recepcion.permissions.set(Permission.objects.filter(content_type__app_label="gym", codename__in=codenames))

        # --- Instalación y planes --------------------------------------------
        Instalacion.objects.get_or_create(nombre="FitLife Central", defaults={"capacidad_maxima": 30, "umbral_alerta": 80})
        planes = {}
        for nombre, precio, dias, desc in [
            ("Estudiante", 19990, 30, "Acceso de lunes a viernes, presentando credencial vigente."),
            ("Mensual", 29990, 30, "Acceso libre a sala de máquinas y clases grupales."),
            ("Trimestral", 79990, 90, "Tres meses con 11% de descuento."),
            ("Anual", 279990, 365, "Doce meses con 22% de descuento y 2 congelamientos."),
        ]:
            planes[nombre], _ = Plan.objects.get_or_create(
                nombre=nombre, defaults={"precio": precio, "duracion_dias": dias, "descripcion": desc}
            )

        # --- Usuario de recepción ---------------------------------------------
        u, creado = User.objects.get_or_create(
            username="recepcion", defaults={"first_name": "Rocío", "last_name": "Recepción", "is_staff": True}
        )
        if creado:
            u.set_password(pwd)
            u.save()
        u.groups.add(recepcion)

        # --- Socios con distintos escenarios ----------------------------------
        escenarios = [
            # username, nombre, apellido, rut, plan, inicio(días), estado, pagado
            ("ana", "Ana", "Torres", 18456789, "Mensual", -5, "ACTIVA", True),
            ("bruno", "Bruno", "Díaz", 17234567, "Trimestral", -20, "ACTIVA", True),
            ("carla", "Carla", "Muñoz", 19876543, "Mensual", -45, "ACTIVA", True),      # quedará VENCIDA
            ("diego", "Diego", "Rojas", 16543210, "Anual", -100, "CONGELADA", True),
            ("elena", "Elena", "Soto", 20123456, "Estudiante", -2, "ACTIVA", False),    # pago pendiente
            ("felipe", "Felipe", "Vargas", 15987654, "Mensual", -1, "ACTIVA", True),
        ]
        grupo_socio = Group.objects.get(name=GRUPO_SOCIO)
        for username, nombre, apellido, cuerpo, plan, inicio, estado, pagado in escenarios:
            usuario, creado = User.objects.get_or_create(
                username=username,
                defaults={"first_name": nombre, "last_name": apellido, "email": f"{username}@fitlife.cl"},
            )
            if creado:
                usuario.set_password(pwd)
                usuario.save()
            usuario.groups.add(grupo_socio)
            socio, _ = Socio.objects.get_or_create(usuario=usuario, defaults={"rut": rut(cuerpo), "telefono": "+56 9 5555 0000"})
            if socio.membresias.exists():
                continue
            p = planes[plan]
            m = Membresia(socio=socio, plan=p, fecha_inicio=hoy + timedelta(days=inicio))
            m.save()
            if estado == "CONGELADA":
                m.estado = Membresia.Estado.CONGELADA
                m.congelada_desde = hoy - timedelta(days=10)
                m.save()
            Pago.objects.create(
                membresia=m, monto=p.precio,
                metodo=Pago.Metodo.DEBITO if pagado else Pago.Metodo.TRANSFERENCIA,
                estado=Pago.Estado.PAGADO if pagado else Pago.Estado.PENDIENTE,
                referencia=f"B-{1000 + socio.pk}", registrado_por=u,
            )

        # --- Clases grupales (programación semanal) ---------------------------
        for nombre, instructor, sala, cupo, dias, hora, dur, desc in [
            ("Spinning", "Camila Pérez", "Sala Bici", 2, "0,1,2,3,4,5,6", time(19, 0), 45,
             "Cupo reducido (2) para demostrar la lista de espera."),
            ("Yoga", "Javier Fuentes", "Sala Zen", 15, "0,2,4", time(8, 0), 60, "Yoga vinyasa todos los niveles."),
            ("Funcional", "Paula Reyes", "Sala 1", 12, "1,3,5", time(18, 0), 50, "Entrenamiento funcional HIIT."),
            ("Pilates", "Andrea Lagos", "Sala Zen", 10, "1,3", time(10, 0), 55, "Pilates mat."),
            ("Zumba", "Matías Herrera", "Sala 1", 20, "0,2,4,6", time(20, 0), 60, "Baile fitness."),
        ]:
            Clase.objects.get_or_create(
                nombre=nombre,
                defaults={"instructor": instructor, "sala": sala, "cupo_maximo": cupo, "dias_semana": dias,
                          "hora_inicio": hora, "duracion_minutos": dur, "descripcion": desc},
            )
        n = services.generar_sesiones(7)
        Membresia.actualizar_vencidas()
        self.stdout.write(self.style.SUCCESS(f"Datos demo listos. Sesiones nuevas: {n}."))
