"""API REST (Django REST Framework)."""
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import Clase, Membresia, Pago, Plan, RegistroAcceso, Reserva, Sesion, Socio
from .permissions import EsPersonal, LecturaAutenticadaEscrituraPersonal, es_personal
from .serializers import (
    ClaseSerializer, MembresiaSerializer, PagoSerializer, PlanSerializer,
    RegistroAccesoSerializer, ReservaSerializer, SesionSerializer, SocioSerializer,
    ValidarAccesoSerializer,
)


def _error_negocio(e: DjangoValidationError):
    return Response({"detail": " ".join(e.messages)}, status=status.HTTP_400_BAD_REQUEST)


class PlanViewSet(viewsets.ModelViewSet):
    serializer_class = PlanSerializer
    permission_classes = [LecturaAutenticadaEscrituraPersonal]

    def get_queryset(self):
        qs = Plan.objects.all()
        if not es_personal(self.request.user):
            qs = qs.filter(activo=True)
        return qs

    def perform_destroy(self, instance):
        if instance.membresias.exists():
            raise ValidationError("No se puede eliminar un plan con membresías asociadas; desactívelo.")
        instance.delete()


class SocioViewSet(viewsets.ModelViewSet):
    serializer_class = SocioSerializer
    permission_classes = [EsPersonal]

    def get_queryset(self):
        qs = Socio.objects.select_related("usuario")
        q = self.request.query_params.get("q")
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(rut__icontains=q) | Q(usuario__first_name__icontains=q)
                | Q(usuario__last_name__icontains=q) | Q(usuario__username__icontains=q)
            )
        return qs

    def perform_destroy(self, instance):
        instance.usuario.delete()  # elimina también el perfil (CASCADE)


class MembresiaViewSet(viewsets.ModelViewSet):
    serializer_class = MembresiaSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [LecturaAutenticadaEscrituraPersonal()]
        return [EsPersonal()]

    def get_queryset(self):
        Membresia.actualizar_vencidas()
        qs = Membresia.objects.select_related("socio__usuario", "plan")
        if not es_personal(self.request.user):
            qs = qs.filter(socio__usuario=self.request.user)
        estado = self.request.query_params.get("estado")
        if estado:
            qs = qs.filter(estado=estado.upper())
        return qs

    @action(detail=True, methods=["post"])
    def congelar(self, request, pk=None):
        m = self.get_object()
        try:
            m.congelar()
        except DjangoValidationError as e:
            return _error_negocio(e)
        return Response(MembresiaSerializer(m).data)

    @action(detail=True, methods=["post"])
    def descongelar(self, request, pk=None):
        m = self.get_object()
        try:
            dias = m.descongelar()
        except DjangoValidationError as e:
            return _error_negocio(e)
        data = MembresiaSerializer(m).data
        data["dias_extendidos"] = dias
        return Response(data)


class PagoViewSet(viewsets.ModelViewSet):
    serializer_class = PagoSerializer
    permission_classes = [EsPersonal]

    def get_queryset(self):
        qs = Pago.objects.select_related("membresia__socio__usuario")
        estado = self.request.query_params.get("estado")
        if estado:
            qs = qs.filter(estado=estado.upper())
        return qs

    def perform_create(self, serializer):
        serializer.save(registrado_por=self.request.user)


class ClaseViewSet(viewsets.ModelViewSet):
    queryset = Clase.objects.all()
    serializer_class = ClaseSerializer
    permission_classes = [LecturaAutenticadaEscrituraPersonal]


class SesionViewSet(viewsets.ModelViewSet):
    serializer_class = SesionSerializer
    permission_classes = [LecturaAutenticadaEscrituraPersonal]

    def get_queryset(self):
        qs = Sesion.objects.select_related("clase")
        if self.action == "list" and self.request.query_params.get("todas") != "1":
            qs = qs.filter(inicio__gte=timezone.now() - timedelta(hours=2))
        return qs

    def perform_update(self, serializer):
        sesion = serializer.save()
        services.promover_lista_espera(sesion)  # si aumentó el cupo

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def reservar(self, request, pk=None):
        sesion = self.get_object()
        if hasattr(request.user, "socio") and not request.data.get("socio"):
            socio = request.user.socio
        elif es_personal(request.user):
            socio = Socio.objects.filter(pk=request.data.get("socio")).first()
            if socio is None:
                raise ValidationError({"socio": "Indique un socio válido."})
        else:
            raise PermissionDenied("Solo socios o recepción pueden reservar.")
        try:
            reserva = services.reservar(socio, sesion)
        except DjangoValidationError as e:
            return _error_negocio(e)
        return Response(ReservaSerializer(reserva).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], permission_classes=[EsPersonal])
    def lista(self, request, pk=None):
        sesion = self.get_object()
        reservas = sesion.reservas.select_related("socio__usuario").exclude(estado=Reserva.Estado.CANCELADA)
        return Response(ReservaSerializer(reservas, many=True).data)


class ReservaViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                     mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Las reservas no se editan ni borran: se crean (con regla de cupos) y se cancelan."""
    serializer_class = ReservaSerializer

    def get_queryset(self):
        qs = Reserva.objects.select_related("sesion__clase", "socio__usuario")
        if not es_personal(self.request.user):
            qs = qs.filter(socio__usuario=self.request.user)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        socio = serializer.validated_data.get("socio")
        if not es_personal(request.user):
            if not hasattr(request.user, "socio"):
                raise PermissionDenied("Solo socios o recepción pueden reservar.")
            socio = request.user.socio
        if socio is None:
            raise ValidationError({"socio": "Indique el socio."})
        try:
            reserva = services.reservar(socio, serializer.validated_data["sesion"])
        except DjangoValidationError as e:
            return _error_negocio(e)
        return Response(self.get_serializer(reserva).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancelar(self, request, pk=None):
        reserva = self.get_object()
        try:
            promovida = services.cancelar_reserva(reserva)
        except DjangoValidationError as e:
            return _error_negocio(e)
        return Response({
            "detail": "Reserva cancelada.",
            "promovida": ReservaSerializer(promovida).data if promovida else None,
        })


class RegistroAccesoViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RegistroAccesoSerializer
    permission_classes = [EsPersonal]

    def get_queryset(self):
        qs = RegistroAcceso.objects.select_related("socio__usuario")
        resultado = self.request.query_params.get("resultado")
        if resultado:
            qs = qs.filter(resultado=resultado.upper())
        return qs


@api_view(["POST"])
@permission_classes([EsPersonal])
def validar_acceso(request):
    """Recibe el contenido del QR leído y responde PERMITIDO / DENEGADO."""
    s = ValidarAccesoSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    registro = services.validar_acceso(s.validated_data["codigo"], s.validated_data["tipo"], request.user)
    data = RegistroAccesoSerializer(registro).data
    if registro.socio:
        m = registro.socio.membresia_actual()
        data["membresia"] = (
            {"plan": m.plan.nombre, "estado": m.estado, "vence": m.fecha_fin.strftime("%d-%m-%Y")} if m else None
        )
    data["aforo"] = services.aforo_actual()
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([EsPersonal])
def aforo(request):
    return Response(services.resumen_aforo())


@api_view(["GET"])
def yo(request):
    u = request.user
    data = {"username": u.username, "nombre": u.get_full_name(), "es_personal": es_personal(u)}
    if hasattr(u, "socio"):
        m = u.socio.membresia_actual()
        data["socio"] = SocioSerializer(u.socio).data
        data["membresia"] = MembresiaSerializer(m).data if m else None
    return Response(data)


@api_view(["GET"])
@permission_classes([])
def salud(request):
    """Health-check público (sin datos sensibles) para verificar el despliegue."""
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT version()")
        version = c.fetchone()[0].split(",")[0]
    return Response({"estado": "ok", "base_de_datos": version, "hora_servidor": timezone.localtime().isoformat()})
