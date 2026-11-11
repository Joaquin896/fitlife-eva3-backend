"""
Roles del sistema:
  * Administrador  -> usuario superuser (Django Admin completo).
  * Recepción      -> miembro del grupo "Recepcion": control de acceso, dashboard,
                      CRUD de socios, membresías, pagos y clases.
  * Socio          -> usuario con perfil Socio: ve su membresía, su QR y reserva clases.
"""
from django.contrib.auth.mixins import UserPassesTestMixin
from rest_framework.permissions import SAFE_METHODS, BasePermission

GRUPO_RECEPCION = "Recepcion"
GRUPO_SOCIO = "Socio"


def es_personal(user) -> bool:
    return bool(
        user and user.is_authenticated
        and (user.is_superuser or user.groups.filter(name=GRUPO_RECEPCION).exists())
    )


def es_socio(user) -> bool:
    return bool(user and user.is_authenticated and hasattr(user, "socio"))


class SoloPersonalMixin(UserPassesTestMixin):
    """Vistas HTML exclusivas de recepción/administración."""

    def test_func(self):
        return es_personal(self.request.user)


class SoloSocioMixin(UserPassesTestMixin):
    def test_func(self):
        return es_socio(self.request.user)


class EsPersonal(BasePermission):
    message = "Solo personal de recepción o administración."

    def has_permission(self, request, view):
        return es_personal(request.user)


class LecturaAutenticadaEscrituraPersonal(BasePermission):
    """GET para cualquier usuario autenticado; POST/PUT/PATCH/DELETE solo personal."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return es_personal(request.user)
