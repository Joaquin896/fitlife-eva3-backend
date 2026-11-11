"""
Modelos del dominio FitLife.

Relaciones principales (todas con claves foráneas en PostgreSQL):

    User 1──1 Socio 1──* Membresia *──1 Plan
                              │
                              └──* Pago
    Socio 1──* Reserva *──1 Sesion *──1 Clase
    Socio 1──* RegistroAcceso
"""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from .validators import normalizar_rut, validar_rut


# ---------------------------------------------------------------------------
# Instalación (capacidad máxima para control de aforo)
# ---------------------------------------------------------------------------
class Instalacion(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    capacidad_maxima = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Número máximo de personas dentro de la instalación al mismo tiempo.",
    )
    umbral_alerta = models.PositiveSmallIntegerField(
        default=80, help_text="% de ocupación desde el cual el dashboard muestra alerta."
    )

    class Meta:
        verbose_name = "instalación"
        verbose_name_plural = "instalaciones"

    def __str__(self):
        return f"{self.nombre} (capacidad {self.capacidad_maxima})"

    @classmethod
    def principal(cls) -> "Instalacion":
        inst = cls.objects.order_by("id").first()
        if inst is None:
            inst = cls.objects.create(
                nombre="FitLife Central", capacidad_maxima=settings.FITLIFE_CAPACIDAD_DEFECTO
            )
        return inst


# ---------------------------------------------------------------------------
# Planes y socios
# ---------------------------------------------------------------------------
class Plan(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True)
    precio = models.PositiveIntegerField(help_text="Precio en pesos chilenos (CLP).")
    duracion_dias = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    activo = models.BooleanField(default=True, help_text="Solo los planes activos se pueden vender.")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["precio"]
        verbose_name_plural = "planes"

    def __str__(self):
        return f"{self.nombre} (${self.precio:,.0f})".replace(",", ".")


class Socio(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="socio"
    )
    rut = models.CharField("RUT", max_length=12, unique=True, validators=[validar_rut])
    telefono = models.CharField(max_length=20, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    codigo_qr = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
        help_text="Identificador único codificado en el QR de acceso.",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["usuario__last_name", "usuario__first_name"]

    def __str__(self):
        return f"{self.nombre} ({self.rut})"

    def save(self, *args, **kwargs):
        self.rut = normalizar_rut(self.rut)
        super().save(*args, **kwargs)

    @property
    def nombre(self) -> str:
        return self.usuario.get_full_name() or self.usuario.username

    def membresia_actual(self):
        """Membresía que rige hoy (la de fecha de término más lejana ya iniciada)."""
        hoy = timezone.localdate()
        return (
            self.membresias.select_related("plan")
            .filter(fecha_inicio__lte=hoy)
            .order_by("-fecha_fin", "-id")
            .first()
        )

    def regenerar_qr(self):
        self.codigo_qr = uuid.uuid4()
        self.save(update_fields=["codigo_qr"])


# ---------------------------------------------------------------------------
# Membresías y pagos
# ---------------------------------------------------------------------------
class Membresia(models.Model):
    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        VENCIDA = "VENCIDA", "Vencida"
        CONGELADA = "CONGELADA", "Congelada"

    socio = models.ForeignKey(Socio, on_delete=models.CASCADE, related_name="membresias")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="membresias")
    fecha_inicio = models.DateField(default=timezone.localdate)
    fecha_fin = models.DateField(blank=True, help_text="Si se deja vacía se calcula con la duración del plan.")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ACTIVA)
    congelada_desde = models.DateField(null=True, blank=True, editable=False)
    dias_congelados = models.PositiveIntegerField(default=0, editable=False)
    observaciones = models.CharField(max_length=200, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_inicio"]
        verbose_name = "membresía"
        verbose_name_plural = "membresías"
        constraints = [
            models.CheckConstraint(
                condition=Q(fecha_fin__gte=models.F("fecha_inicio")),
                name="membresia_fin_mayor_inicio",
            )
        ]
        indexes = [models.Index(fields=["estado", "fecha_fin"])]

    def __str__(self):
        return f"{self.socio.nombre} · {self.plan.nombre} · {self.get_estado_display()}"

    # --- validaciones -------------------------------------------------------
    def clean(self):
        if self.fecha_fin is None and self.plan_id and self.fecha_inicio:
            self.fecha_fin = self.fecha_inicio + timedelta(days=self.plan.duracion_dias - 1)
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin < self.fecha_inicio:
            raise ValidationError({"fecha_fin": "La fecha de término no puede ser anterior al inicio."})
        if self.socio_id and self.fecha_inicio and self.fecha_fin:
            solapadas = Membresia.objects.filter(
                socio_id=self.socio_id,
                fecha_inicio__lte=self.fecha_fin,
                fecha_fin__gte=self.fecha_inicio,
            ).exclude(pk=self.pk)
            if solapadas.exists():
                raise ValidationError("El socio ya tiene una membresía que se superpone con esas fechas.")
        if self.plan_id and not self.pk and not self.plan.activo:
            raise ValidationError({"plan": "El plan seleccionado no está activo."})

    def save(self, *args, **kwargs):
        if self.fecha_fin is None:
            self.fecha_fin = self.fecha_inicio + timedelta(days=self.plan.duracion_dias - 1)
        if self.estado == self.Estado.ACTIVA and self.fecha_fin < timezone.localdate():
            self.estado = self.Estado.VENCIDA
        elif self.estado == self.Estado.VENCIDA and self.fecha_fin >= timezone.localdate():
            self.estado = self.Estado.ACTIVA
        super().save(*args, **kwargs)

    # --- consultas ----------------------------------------------------------
    @property
    def dias_restantes(self) -> int:
        return max((self.fecha_fin - timezone.localdate()).days + 1, 0)

    @property
    def total_pagado(self) -> int:
        return self.pagos.filter(estado=Pago.Estado.PAGADO).aggregate(t=Sum("monto"))["t"] or 0

    @property
    def esta_pagada(self) -> bool:
        return self.total_pagado >= self.plan.precio

    @property
    def habilita_acceso(self) -> bool:
        hoy = timezone.localdate()
        return (
            self.estado == self.Estado.ACTIVA
            and self.fecha_inicio <= hoy <= self.fecha_fin
            and self.esta_pagada
        )

    # --- transiciones de estado --------------------------------------------
    def congelar(self):
        if self.estado != self.Estado.ACTIVA:
            raise ValidationError("Solo se puede congelar una membresía activa.")
        self.estado = self.Estado.CONGELADA
        self.congelada_desde = timezone.localdate()
        self.save(update_fields=["estado", "congelada_desde"])

    def descongelar(self):
        """Reactiva la membresía y extiende la fecha de término en los días congelados."""
        if self.estado != self.Estado.CONGELADA:
            raise ValidationError("La membresía no está congelada.")
        dias = (timezone.localdate() - (self.congelada_desde or timezone.localdate())).days
        self.fecha_fin = self.fecha_fin + timedelta(days=dias)
        self.dias_congelados += dias
        self.congelada_desde = None
        self.estado = self.Estado.ACTIVA
        self.save()
        return dias

    @classmethod
    def actualizar_vencidas(cls) -> int:
        """Marca como VENCIDA toda membresía ACTIVA cuya fecha de término ya pasó."""
        return cls.objects.filter(
            estado=cls.Estado.ACTIVA, fecha_fin__lt=timezone.localdate()
        ).update(estado=cls.Estado.VENCIDA)


class Pago(models.Model):
    class Metodo(models.TextChoices):
        EFECTIVO = "EFECTIVO", "Efectivo"
        DEBITO = "DEBITO", "Tarjeta de débito"
        CREDITO = "CREDITO", "Tarjeta de crédito"
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"

    class Estado(models.TextChoices):
        PAGADO = "PAGADO", "Pagado"
        PENDIENTE = "PENDIENTE", "Pendiente"
        ANULADO = "ANULADO", "Anulado"

    membresia = models.ForeignKey(Membresia, on_delete=models.CASCADE, related_name="pagos")
    monto = models.PositiveIntegerField(validators=[MinValueValidator(1)], help_text="Monto en CLP.")
    metodo = models.CharField(max_length=15, choices=Metodo.choices, default=Metodo.DEBITO)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PAGADO)
    fecha_pago = models.DateTimeField(default=timezone.now)
    referencia = models.CharField(max_length=60, blank=True, help_text="N° de boleta o comprobante.")
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-fecha_pago"]

    def __str__(self):
        return f"Pago #{self.pk} · {self.membresia.socio.nombre} · ${self.monto:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Clases grupales, sesiones y reservas
# ---------------------------------------------------------------------------
class Clase(models.Model):
    DIAS = [(0, "Lun"), (1, "Mar"), (2, "Mié"), (3, "Jue"), (4, "Vie"), (5, "Sáb"), (6, "Dom")]

    nombre = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True)
    instructor = models.CharField(max_length=100)
    sala = models.CharField(max_length=50, default="Sala 1")
    cupo_maximo = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    duracion_minutos = models.PositiveIntegerField(default=60, validators=[MinValueValidator(15)])
    dias_semana = models.CharField(
        max_length=20, default="0,2,4",
        help_text="Días en que se dicta, separados por coma (0=lunes … 6=domingo).",
    )
    hora_inicio = models.TimeField(help_text="Hora de inicio para la programación semanal.")
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        try:
            dias = self.lista_dias()
        except ValueError:
            raise ValidationError({"dias_semana": "Use números del 0 al 6 separados por coma."})
        if not dias or any(d < 0 or d > 6 for d in dias):
            raise ValidationError({"dias_semana": "Use números del 0 al 6 separados por coma."})

    def lista_dias(self) -> list[int]:
        return [int(d) for d in self.dias_semana.split(",") if d.strip() != ""]

    def dias_legibles(self) -> str:
        nombres = dict(self.DIAS)
        try:
            return ", ".join(nombres[d] for d in self.lista_dias())
        except (KeyError, ValueError):
            return self.dias_semana


class Sesion(models.Model):
    clase = models.ForeignKey(Clase, on_delete=models.CASCADE, related_name="sesiones")
    inicio = models.DateTimeField()
    cupo_maximo = models.PositiveIntegerField(
        validators=[MinValueValidator(1)], blank=True,
        help_text="Si se deja vacío se usa el cupo de la clase.",
    )
    cancelada = models.BooleanField(default=False)

    class Meta:
        ordering = ["inicio"]
        verbose_name = "sesión"
        verbose_name_plural = "sesiones"
        constraints = [models.UniqueConstraint(fields=["clase", "inicio"], name="sesion_unica_por_horario")]

    def __str__(self):
        return f"{self.clase.nombre} · {timezone.localtime(self.inicio):%d-%m %H:%M}"

    def save(self, *args, **kwargs):
        if not self.cupo_maximo:
            self.cupo_maximo = self.clase.cupo_maximo
        super().save(*args, **kwargs)

    @property
    def fin(self):
        return self.inicio + timedelta(minutes=self.clase.duracion_minutos)

    @property
    def confirmadas(self) -> int:
        return self.reservas.filter(estado__in=Reserva.ESTADOS_CON_CUPO).count()

    @property
    def en_espera(self) -> int:
        return self.reservas.filter(estado=Reserva.Estado.EN_ESPERA).count()

    @property
    def cupos_disponibles(self) -> int:
        return max(self.cupo_maximo - self.confirmadas, 0)

    @property
    def ya_paso(self) -> bool:
        return self.inicio <= timezone.now()


class Reserva(models.Model):
    class Estado(models.TextChoices):
        CONFIRMADA = "CONFIRMADA", "Confirmada"
        EN_ESPERA = "EN_ESPERA", "En lista de espera"
        CANCELADA = "CANCELADA", "Cancelada"
        ASISTIO = "ASISTIO", "Asistió"

    ESTADOS_CON_CUPO = [Estado.CONFIRMADA, Estado.ASISTIO]

    sesion = models.ForeignKey(Sesion, on_delete=models.CASCADE, related_name="reservas")
    socio = models.ForeignKey(Socio, on_delete=models.CASCADE, related_name="reservas")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.CONFIRMADA)
    creada = models.DateTimeField(default=timezone.now)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["creada"]
        constraints = [
            # Un socio no puede tener dos reservas vigentes para la misma sesión.
            models.UniqueConstraint(
                fields=["sesion", "socio"],
                condition=~Q(estado="CANCELADA"),
                name="reserva_unica_vigente",
            )
        ]

    def __str__(self):
        return f"{self.socio.nombre} → {self.sesion} ({self.get_estado_display()})"

    @property
    def posicion_espera(self):
        if self.estado != self.Estado.EN_ESPERA:
            return None
        return (
            Reserva.objects.filter(
                sesion_id=self.sesion_id, estado=self.Estado.EN_ESPERA, creada__lt=self.creada
            ).count()
            + 1
        )


# ---------------------------------------------------------------------------
# Control de acceso
# ---------------------------------------------------------------------------
class RegistroAcceso(models.Model):
    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        SALIDA = "SALIDA", "Salida"

    class Resultado(models.TextChoices):
        PERMITIDO = "PERMITIDO", "Permitido"
        DENEGADO = "DENEGADO", "Denegado"

    socio = models.ForeignKey(
        Socio, null=True, blank=True, on_delete=models.SET_NULL, related_name="accesos"
    )
    codigo_leido = models.CharField(max_length=100)
    tipo = models.CharField(max_length=8, choices=Tipo.choices)
    resultado = models.CharField(max_length=9, choices=Resultado.choices)
    motivo = models.CharField(max_length=150)
    latencia_ms = models.FloatField(help_text="Tiempo de validación en milisegundos.")
    fecha_hora = models.DateTimeField(default=timezone.now, db_index=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-fecha_hora"]
        verbose_name = "registro de acceso"
        verbose_name_plural = "registros de acceso"

    def __str__(self):
        quien = self.socio.nombre if self.socio else "Desconocido"
        return f"{timezone.localtime(self.fecha_hora):%d-%m %H:%M:%S} · {quien} · {self.tipo} · {self.resultado}"
