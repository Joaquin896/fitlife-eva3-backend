from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import Clase, Membresia, Pago, Plan, RegistroAcceso, Reserva, Sesion, Socio

User = get_user_model()


class ValidacionModeloMixin:
    """Ejecuta Model.clean() para reutilizar las reglas del modelo en la API."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instancia = self.instance or self.Meta.model()
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        try:
            instancia.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, "error_dict") else e.messages)
        # clean() puede completar campos calculados (ej. fecha_fin)
        for campo in attrs:
            attrs[campo] = getattr(instancia, campo)
        if isinstance(instancia, Membresia) and "fecha_fin" not in attrs:
            attrs["fecha_fin"] = instancia.fecha_fin
        return attrs


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ["id", "nombre", "descripcion", "precio", "duracion_dias", "activo"]


class SocioSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="usuario.username")
    first_name = serializers.CharField(source="usuario.first_name")
    last_name = serializers.CharField(source="usuario.last_name")
    email = serializers.EmailField(source="usuario.email", required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    estado_membresia = serializers.SerializerMethodField()

    class Meta:
        model = Socio
        fields = [
            "id", "username", "first_name", "last_name", "email", "password",
            "rut", "telefono", "fecha_nacimiento", "codigo_qr", "estado_membresia",
        ]
        read_only_fields = ["codigo_qr"]

    def get_estado_membresia(self, obj):
        m = obj.membresia_actual()
        return m.estado if m else None

    def validate_username(self, value):
        qs = User.objects.filter(username=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.usuario_id)
        if qs.exists():
            raise serializers.ValidationError("Ese nombre de usuario ya existe.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value

    @transaction.atomic
    def create(self, validated_data):
        datos_usuario = validated_data.pop("usuario")
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Requerido al crear un socio."})
        usuario = User.objects.create_user(password=password, **datos_usuario)
        from django.contrib.auth.models import Group
        from .permissions import GRUPO_SOCIO
        usuario.groups.add(Group.objects.get_or_create(name=GRUPO_SOCIO)[0])
        return Socio.objects.create(usuario=usuario, **validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        datos_usuario = validated_data.pop("usuario", {})
        password = validated_data.pop("password", None)
        for campo, valor in datos_usuario.items():
            setattr(instance.usuario, campo, valor)
        if password:
            instance.usuario.set_password(password)
        instance.usuario.save()
        return super().update(instance, validated_data)


class MembresiaSerializer(ValidacionModeloMixin, serializers.ModelSerializer):
    socio_nombre = serializers.CharField(source="socio.nombre", read_only=True)
    plan_nombre = serializers.CharField(source="plan.nombre", read_only=True)
    dias_restantes = serializers.IntegerField(read_only=True)
    total_pagado = serializers.IntegerField(read_only=True)
    esta_pagada = serializers.BooleanField(read_only=True)
    fecha_fin = serializers.DateField(required=False)

    class Meta:
        model = Membresia
        fields = [
            "id", "socio", "socio_nombre", "plan", "plan_nombre", "fecha_inicio", "fecha_fin",
            "estado", "dias_congelados", "dias_restantes", "total_pagado", "esta_pagada", "observaciones",
        ]
        read_only_fields = ["dias_congelados"]


class PagoSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.CharField(source="membresia.socio.nombre", read_only=True)

    class Meta:
        model = Pago
        fields = ["id", "membresia", "socio_nombre", "monto", "metodo", "estado", "fecha_pago", "referencia"]


class ClaseSerializer(ValidacionModeloMixin, serializers.ModelSerializer):
    class Meta:
        model = Clase
        fields = [
            "id", "nombre", "descripcion", "instructor", "sala", "cupo_maximo",
            "duracion_minutos", "dias_semana", "hora_inicio", "activa",
        ]


class SesionSerializer(serializers.ModelSerializer):
    clase_nombre = serializers.CharField(source="clase.nombre", read_only=True)
    instructor = serializers.CharField(source="clase.instructor", read_only=True)
    sala = serializers.CharField(source="clase.sala", read_only=True)
    confirmadas = serializers.IntegerField(read_only=True)
    en_espera = serializers.IntegerField(read_only=True)
    cupos_disponibles = serializers.IntegerField(read_only=True)
    cupo_maximo = serializers.IntegerField(required=False, min_value=1)
    mi_reserva = serializers.SerializerMethodField()

    class Meta:
        model = Sesion
        fields = [
            "id", "clase", "clase_nombre", "instructor", "sala", "inicio", "cupo_maximo",
            "cancelada", "confirmadas", "en_espera", "cupos_disponibles", "mi_reserva",
        ]

    def get_mi_reserva(self, obj):
        request = self.context.get("request")
        if not request or not hasattr(request.user, "socio"):
            return None
        r = obj.reservas.filter(socio=request.user.socio).exclude(estado=Reserva.Estado.CANCELADA).first()
        if not r:
            return None
        return {"id": r.id, "estado": r.estado, "posicion_espera": r.posicion_espera}


class ReservaSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.CharField(source="socio.nombre", read_only=True)
    sesion_detalle = serializers.CharField(source="sesion.__str__", read_only=True)
    posicion_espera = serializers.IntegerField(read_only=True)
    socio = serializers.PrimaryKeyRelatedField(queryset=Socio.objects.all(), required=False)

    class Meta:
        model = Reserva
        fields = ["id", "sesion", "sesion_detalle", "socio", "socio_nombre", "estado", "posicion_espera", "creada"]
        read_only_fields = ["estado", "creada"]


class RegistroAccesoSerializer(serializers.ModelSerializer):
    socio_nombre = serializers.SerializerMethodField()

    class Meta:
        model = RegistroAcceso
        fields = ["id", "socio", "socio_nombre", "codigo_leido", "tipo", "resultado", "motivo", "latencia_ms", "fecha_hora"]

    def get_socio_nombre(self, obj):
        return obj.socio.nombre if obj.socio else None


class ValidarAccesoSerializer(serializers.Serializer):
    codigo = serializers.CharField(max_length=100)
    tipo = serializers.ChoiceField(choices=["AUTO", "ENTRADA", "SALIDA"], default="AUTO")
