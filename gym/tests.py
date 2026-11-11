"""
Pruebas automáticas de las reglas de negocio.

    python manage.py test gym
"""
from datetime import time, timedelta

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import services
from .models import Clase, Instalacion, Membresia, Pago, Plan, RegistroAcceso, Reserva, Sesion, Socio
from .permissions import GRUPO_RECEPCION
from .validators import calcular_dv, validar_rut


def crear_socio(username, cuerpo_rut, plan, dias_inicio=0, pagado=True):
    u = User.objects.create_user(username=username, password="ClaveSegura123")
    s = Socio.objects.create(usuario=u, rut=f"{cuerpo_rut}-{calcular_dv(str(cuerpo_rut))}")
    m = Membresia.objects.create(socio=s, plan=plan, fecha_inicio=timezone.localdate() + timedelta(days=dias_inicio))
    Pago.objects.create(membresia=m, monto=plan.precio, estado="PAGADO" if pagado else "PENDIENTE")
    return s


class BaseFitLife(TestCase):
    def setUp(self):
        self.plan = Plan.objects.create(nombre="Mensual", precio=29990, duracion_dias=30)
        self.clase = Clase.objects.create(
            nombre="Spinning", instructor="Prof", cupo_maximo=2, hora_inicio=time(19, 0), dias_semana="0,1,2,3,4,5,6"
        )
        self.sesion = Sesion.objects.create(clase=self.clase, inicio=timezone.now() + timedelta(hours=3))
        self.ana = crear_socio("ana", 18456789, self.plan)
        self.bruno = crear_socio("bruno", 17234567, self.plan)
        self.felipe = crear_socio("felipe", 15987654, self.plan)


class ReglasReservaTests(BaseFitLife):
    def test_cupo_y_lista_de_espera(self):
        r1 = services.reservar(self.ana, self.sesion)
        r2 = services.reservar(self.bruno, self.sesion)
        r3 = services.reservar(self.felipe, self.sesion)
        self.assertEqual(r1.estado, Reserva.Estado.CONFIRMADA)
        self.assertEqual(r2.estado, Reserva.Estado.CONFIRMADA)
        self.assertEqual(r3.estado, Reserva.Estado.EN_ESPERA)
        self.assertEqual(r3.posicion_espera, 1)

    def test_cancelar_promueve_lista_de_espera(self):
        r1 = services.reservar(self.ana, self.sesion)
        services.reservar(self.bruno, self.sesion)
        r3 = services.reservar(self.felipe, self.sesion)
        promovida = services.cancelar_reserva(r1)
        self.assertEqual(promovida.pk, r3.pk)
        r3.refresh_from_db()
        self.assertEqual(r3.estado, Reserva.Estado.CONFIRMADA)

    def test_aumentar_cupo_promueve(self):
        services.reservar(self.ana, self.sesion)
        services.reservar(self.bruno, self.sesion)
        r3 = services.reservar(self.felipe, self.sesion)
        self.sesion.cupo_maximo = 3
        self.sesion.save()
        services.promover_lista_espera(self.sesion)
        r3.refresh_from_db()
        self.assertEqual(r3.estado, Reserva.Estado.CONFIRMADA)

    def test_no_permite_reserva_duplicada(self):
        services.reservar(self.ana, self.sesion)
        with self.assertRaises(ValidationError):
            services.reservar(self.ana, self.sesion)

    def test_membresia_vencida_no_reserva(self):
        carla = crear_socio("carla", 19876543, self.plan, dias_inicio=-45)
        self.assertEqual(carla.membresia_actual().estado, Membresia.Estado.VENCIDA)
        with self.assertRaises(ValidationError):
            services.reservar(carla, self.sesion)


class ReglasMembresiaTests(BaseFitLife):
    def test_fecha_fin_se_calcula_con_el_plan(self):
        m = self.ana.membresia_actual()
        self.assertEqual((m.fecha_fin - m.fecha_inicio).days, 29)

    def test_congelar_y_descongelar_extiende_vigencia(self):
        m = self.ana.membresia_actual()
        fin_original = m.fecha_fin
        m.congelar()
        m.congelada_desde = timezone.localdate() - timedelta(days=5)
        m.save()
        dias = m.descongelar()
        self.assertEqual(dias, 5)
        self.assertEqual(m.fecha_fin, fin_original + timedelta(days=5))
        self.assertEqual(m.estado, Membresia.Estado.ACTIVA)

    def test_no_permite_membresias_superpuestas(self):
        m = Membresia(socio=self.ana, plan=self.plan, fecha_inicio=timezone.localdate())
        with self.assertRaises(ValidationError):
            m.full_clean()


class ControlAccesoTests(BaseFitLife):
    def test_acceso_permitido_y_salida_automatica(self):
        r = services.validar_acceso(str(self.ana.codigo_qr))
        self.assertEqual((r.tipo, r.resultado), ("ENTRADA", "PERMITIDO"))
        self.assertEqual(services.aforo_actual(), 1)
        r = services.validar_acceso(str(self.ana.codigo_qr))
        self.assertEqual((r.tipo, r.resultado), ("SALIDA", "PERMITIDO"))
        self.assertEqual(services.aforo_actual(), 0)

    def test_qr_desconocido_denegado(self):
        r = services.validar_acceso("00000000-0000-0000-0000-000000000000")
        self.assertEqual(r.resultado, "DENEGADO")
        r = services.validar_acceso("texto-cualquiera")
        self.assertEqual(r.resultado, "DENEGADO")

    def test_membresia_congelada_o_impaga_denegada(self):
        self.ana.membresia_actual().congelar()
        self.assertEqual(services.validar_acceso(str(self.ana.codigo_qr)).resultado, "DENEGADO")
        elena = crear_socio("elena", 20123456, self.plan, pagado=False)
        r = services.validar_acceso(str(elena.codigo_qr))
        self.assertEqual(r.resultado, "DENEGADO")
        self.assertIn("pago pendiente", r.motivo)

    def test_aforo_maximo(self):
        Instalacion.objects.create(nombre="Mini", capacidad_maxima=1)
        services.validar_acceso(str(self.ana.codigo_qr))
        r = services.validar_acceso(str(self.bruno.codigo_qr))
        self.assertEqual(r.resultado, "DENEGADO")
        self.assertIn("Aforo", r.motivo)

    def test_latencia_registrada(self):
        r = services.validar_acceso(str(self.ana.codigo_qr))
        self.assertLess(r.latencia_ms, 500)
        self.assertEqual(RegistroAcceso.objects.count(), 1)


class SeguridadApiTests(BaseFitLife):
    def setUp(self):
        super().setUp()
        self.client_api = APIClient()
        self.recepcion = User.objects.create_user("recepcion", password="ClaveSegura123")
        self.recepcion.groups.add(Group.objects.create(name=GRUPO_RECEPCION))

    def test_anonimo_no_accede(self):
        self.assertEqual(self.client_api.get("/api/socios/").status_code, 403)
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)

    def test_socio_no_ve_pagos_ni_valida_accesos(self):
        self.client_api.force_authenticate(self.ana.usuario)
        self.assertEqual(self.client_api.get("/api/pagos/").status_code, 403)
        self.assertEqual(self.client_api.post("/api/acceso/validar/", {"codigo": "x"}).status_code, 403)

    def test_socio_reserva_por_api(self):
        self.client_api.force_authenticate(self.ana.usuario)
        resp = self.client_api.post(f"/api/sesiones/{self.sesion.pk}/reservar/")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["estado"], "CONFIRMADA")

    def test_socio_solo_ve_sus_membresias(self):
        self.client_api.force_authenticate(self.ana.usuario)
        resp = self.client_api.get("/api/membresias/")
        self.assertEqual(resp.data["count"], 1)

    def test_recepcion_valida_acceso_y_ve_aforo(self):
        self.client_api.force_authenticate(self.recepcion)
        resp = self.client_api.post("/api/acceso/validar/", {"codigo": str(self.ana.codigo_qr)})
        self.assertEqual(resp.data["resultado"], "PERMITIDO")
        self.assertEqual(self.client_api.get("/api/aforo/").data["ocupacion"], 1)

    def test_crud_planes_api(self):
        self.client_api.force_authenticate(self.recepcion)
        resp = self.client_api.post("/api/planes/", {"nombre": "Diario", "precio": 5000, "duracion_dias": 1})
        self.assertEqual(resp.status_code, 201)
        pk = resp.data["id"]
        self.assertEqual(self.client_api.patch(f"/api/planes/{pk}/", {"precio": 6000}).data["precio"], 6000)
        self.assertEqual(self.client_api.delete(f"/api/planes/{pk}/").status_code, 204)


class ValidadorRutTests(TestCase):
    def test_rut(self):
        validar_rut("11.111.111-1")
        with self.assertRaises(ValidationError):
            validar_rut("11.111.111-2")
