from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register("planes", api.PlanViewSet, basename="plan")
router.register("socios", api.SocioViewSet, basename="socio")
router.register("membresias", api.MembresiaViewSet, basename="membresia")
router.register("pagos", api.PagoViewSet, basename="pago")
router.register("clases", api.ClaseViewSet, basename="clase")
router.register("sesiones", api.SesionViewSet, basename="sesion")
router.register("reservas", api.ReservaViewSet, basename="reserva")
router.register("accesos", api.RegistroAccesoViewSet, basename="acceso")

urlpatterns = [
    path("acceso/validar/", api.validar_acceso, name="api-validar-acceso"),
    path("aforo/", api.aforo, name="api-aforo"),
    path("me/", api.yo, name="api-me"),
    path("salud/", api.salud, name="api-salud"),
    path("", include(router.urls)),
]
