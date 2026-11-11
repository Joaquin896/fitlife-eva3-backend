"""Django Admin personalizado para FitLife."""
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from . import services
from .models import Clase, Instalacion, Membresia, Pago, Plan, RegistroAcceso, Reserva, Sesion, Socio

COLORES = {
    "ACTIVA": "#198754", "VENCIDA": "#dc3545", "CONGELADA": "#0dcaf0",
    "PAGADO": "#198754", "PENDIENTE": "#ffc107", "ANULADO": "#6c757d",
    "CONFIRMADA": "#198754", "EN_ESPERA": "#fd7e14", "CANCELADA": "#6c757d", "ASISTIO": "#0d6efd",
    "PERMITIDO": "#198754", "DENEGADO": "#dc3545",
}


def chip(valor, texto):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">{}</span>',
        COLORES.get(valor, "#6c757d"), texto,
    )


def clp(valor):
    return f"${valor:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
@admin.register(Instalacion)
class InstalacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "capacidad_maxima", "umbral_alerta", "ocupacion_actual")

    @admin.display(description="Ocupación actual")
    def ocupacion_actual(self, obj):
        return f"{services.aforo_actual()} / {obj.capacidad_maxima}"


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("nombre", "precio_clp", "duracion_dias", "activo", "membresias_activas")
    list_filter = ("activo",)
    list_editable = ("activo",)
    search_fields = ("nombre", "descripcion")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            n_activas=Count("membresias", filter=Q(membresias__estado="ACTIVA"))
        )

    @admin.display(description="Precio", ordering="precio")
    def precio_clp(self, obj):
        return clp(obj.precio)

    @admin.display(description="Membresías activas", ordering="n_activas")
    def membresias_activas(self, obj):
        return obj.n_activas


# ---------------------------------------------------------------------------
class MembresiaInline(admin.TabularInline):
    model = Membresia
    extra = 0
    fields = ("plan", "fecha_inicio", "fecha_fin", "estado", "dias_congelados")
    readonly_fields = ("dias_congelados",)
    show_change_link = True


class EstadoMembresiaFilter(admin.SimpleListFilter):
    title = "estado de membresía actual"
    parameter_name = "estado_actual"

    def lookups(self, request, model_admin):
        return Membresia.Estado.choices + [("SIN", "Sin membresía")]

    def queryset(self, request, queryset):
        valor = self.value()
        if not valor:
            return queryset
        hoy = timezone.localdate()
        if valor == "SIN":
            return queryset.exclude(membresias__fecha_inicio__lte=hoy)
        ids = [s.pk for s in queryset if (m := s.membresia_actual()) and m.estado == valor]
        return queryset.filter(pk__in=ids)


@admin.register(Socio)
class SocioAdmin(admin.ModelAdmin):
    list_display = ("nombre_completo", "rut", "email", "telefono", "estado_membresia", "creado")
    list_filter = (EstadoMembresiaFilter, "creado")
    search_fields = ("rut", "usuario__username", "usuario__first_name", "usuario__last_name", "usuario__email")
    readonly_fields = ("codigo_qr", "vista_qr", "creado")
    autocomplete_fields = ("usuario",)
    inlines = [MembresiaInline]
    list_select_related = ("usuario",)
    fieldsets = (
        ("Datos personales", {"fields": ("usuario", "rut", "telefono", "fecha_nacimiento")}),
        ("Acceso", {"fields": ("codigo_qr", "vista_qr")}),
        ("Auditoría", {"fields": ("creado",), "classes": ("collapse",)}),
    )
    actions = ["regenerar_qr"]

    @admin.display(description="Nombre", ordering="usuario__last_name")
    def nombre_completo(self, obj):
        return obj.nombre

    @admin.display(description="Correo", ordering="usuario__email")
    def email(self, obj):
        return obj.usuario.email

    @admin.display(description="Membresía")
    def estado_membresia(self, obj):
        m = obj.membresia_actual()
        return chip(m.estado, m.get_estado_display()) if m else chip("", "Sin membresía")

    @admin.display(description="Código QR")
    def vista_qr(self, obj):
        if not obj.pk:
            return "Se genera al guardar."
        return format_html('<img src="{}" width="160" height="160" alt="QR">', reverse("qr_svg", args=[obj.pk]))

    @admin.action(description="Regenerar código QR (invalida el anterior)")
    def regenerar_qr(self, request, queryset):
        for s in queryset:
            s.regenerar_qr()
        self.message_user(request, f"QR regenerado para {queryset.count()} socio(s).")


# ---------------------------------------------------------------------------
class PagoInline(admin.TabularInline):
    model = Pago
    extra = 0
    fields = ("monto", "metodo", "estado", "fecha_pago", "referencia")


@admin.register(Membresia)
class MembresiaAdmin(admin.ModelAdmin):
    list_display = ("socio", "plan", "fecha_inicio", "fecha_fin", "estado_color", "dias_restantes_col", "pagada")
    list_filter = ("estado", "plan", "fecha_fin")
    search_fields = ("socio__rut", "socio__usuario__first_name", "socio__usuario__last_name")
    date_hierarchy = "fecha_inicio"
    autocomplete_fields = ("socio",)
    readonly_fields = ("congelada_desde", "dias_congelados", "creado")
    inlines = [PagoInline]
    list_select_related = ("socio__usuario", "plan")
    actions = ["congelar", "descongelar", "marcar_vencidas"]

    def get_queryset(self, request):
        Membresia.actualizar_vencidas()
        return super().get_queryset(request)

    @admin.display(description="Estado", ordering="estado")
    def estado_color(self, obj):
        return chip(obj.estado, obj.get_estado_display())

    @admin.display(description="Días restantes")
    def dias_restantes_col(self, obj):
        return obj.dias_restantes

    @admin.display(description="Pagada", boolean=True)
    def pagada(self, obj):
        return obj.esta_pagada

    @admin.action(description="Congelar membresías seleccionadas")
    def congelar(self, request, queryset):
        ok = 0
        for m in queryset:
            try:
                m.congelar()
                ok += 1
            except ValidationError as e:
                self.message_user(request, f"{m}: {' '.join(e.messages)}", messages.WARNING)
        self.message_user(request, f"{ok} membresía(s) congelada(s).")

    @admin.action(description="Descongelar (extiende la fecha de término)")
    def descongelar(self, request, queryset):
        for m in queryset:
            try:
                dias = m.descongelar()
                self.message_user(request, f"{m.socio.nombre}: +{dias} día(s), vence {m.fecha_fin:%d-%m-%Y}.")
            except ValidationError as e:
                self.message_user(request, f"{m}: {' '.join(e.messages)}", messages.WARNING)

    @admin.action(description="Actualizar estados vencidos")
    def marcar_vencidas(self, request, queryset):
        n = Membresia.actualizar_vencidas()
        self.message_user(request, f"{n} membresía(s) marcadas como vencidas.")


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = ("id", "socio", "plan", "monto_clp", "metodo", "estado_color", "fecha_pago", "registrado_por")
    list_filter = ("estado", "metodo", "fecha_pago")
    search_fields = ("referencia", "membresia__socio__rut", "membresia__socio__usuario__last_name")
    date_hierarchy = "fecha_pago"
    autocomplete_fields = ("membresia",)
    readonly_fields = ("registrado_por",)
    list_select_related = ("membresia__socio__usuario", "membresia__plan", "registrado_por")
    actions = ["anular"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.registrado_por = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Socio", ordering="membresia__socio__usuario__last_name")
    def socio(self, obj):
        return obj.membresia.socio.nombre

    @admin.display(description="Plan")
    def plan(self, obj):
        return obj.membresia.plan.nombre

    @admin.display(description="Monto", ordering="monto")
    def monto_clp(self, obj):
        return clp(obj.monto)

    @admin.display(description="Estado", ordering="estado")
    def estado_color(self, obj):
        return chip(obj.estado, obj.get_estado_display())

    @admin.action(description="Anular pagos seleccionados")
    def anular(self, request, queryset):
        n = queryset.update(estado=Pago.Estado.ANULADO)
        self.message_user(request, f"{n} pago(s) anulados.")


# ---------------------------------------------------------------------------
@admin.register(Clase)
class ClaseAdmin(admin.ModelAdmin):
    list_display = ("nombre", "instructor", "sala", "cupo_maximo", "horario", "duracion_minutos", "activa")
    list_filter = ("activa", "sala", "instructor")
    list_editable = ("cupo_maximo", "activa")
    search_fields = ("nombre", "instructor")
    actions = ["generar_sesiones"]

    @admin.display(description="Horario")
    def horario(self, obj):
        return f"{obj.dias_legibles()} · {obj.hora_inicio:%H:%M}"

    @admin.action(description="Generar sesiones de los próximos 7 días")
    def generar_sesiones(self, request, queryset):
        n = services.generar_sesiones(7)
        self.message_user(request, f"{n} sesión(es) nuevas creadas.")


class ReservaInline(admin.TabularInline):
    model = Reserva
    extra = 0
    fields = ("socio", "estado", "creada")
    readonly_fields = ("socio", "estado", "creada")
    can_delete = False
    ordering = ("estado", "creada")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Sesion)
class SesionAdmin(admin.ModelAdmin):
    list_display = ("clase", "inicio", "cupo_maximo", "confirmadas_col", "espera_col", "ocupacion", "cancelada")
    list_filter = ("clase", "cancelada", "inicio")
    date_hierarchy = "inicio"
    search_fields = ("clase__nombre",)
    inlines = [ReservaInline]
    list_select_related = ("clase",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            n_conf=Count("reservas", filter=Q(reservas__estado__in=Reserva.ESTADOS_CON_CUPO)),
            n_esp=Count("reservas", filter=Q(reservas__estado="EN_ESPERA")),
        )

    @admin.display(description="Confirmadas", ordering="n_conf")
    def confirmadas_col(self, obj):
        return obj.n_conf

    @admin.display(description="En espera", ordering="n_esp")
    def espera_col(self, obj):
        return obj.n_esp

    @admin.display(description="Ocupación")
    def ocupacion(self, obj):
        pct = min(int(obj.n_conf * 100 / obj.cupo_maximo), 100) if obj.cupo_maximo else 0
        color = "#dc3545" if pct >= 100 else "#fd7e14" if pct >= 80 else "#198754"
        return format_html(
            '<div style="width:100px;background:#e9ecef;border-radius:4px">'
            '<div style="width:{}px;background:{};height:10px;border-radius:4px"></div></div>',
            pct, color,
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        promovidas = services.promover_lista_espera(obj)
        if promovidas:
            self.message_user(request, f"{len(promovidas)} reserva(s) promovidas desde la lista de espera.")


class ReservaAdminForm(forms.ModelForm):
    class Meta:
        model = Reserva
        fields = ("sesion", "socio", "estado")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # Al crear, el estado lo decide la regla de cupos.
            self.fields["estado"].disabled = True
            self.fields["estado"].help_text = "Se asigna automáticamente (Confirmada o En lista de espera)."

    def clean(self):
        datos = super().clean()
        if not self.instance.pk and datos.get("sesion") and datos.get("socio"):
            sesion, socio = datos["sesion"], datos["socio"]
            if Reserva.objects.filter(sesion=sesion, socio=socio).exclude(estado="CANCELADA").exists():
                raise ValidationError("El socio ya tiene una reserva para esta sesión.")
            if sesion.cancelada or sesion.ya_paso:
                raise ValidationError("La sesión está cancelada o ya comenzó.")
            services.verificar_membresia_para_reserva(socio, sesion)
        return datos


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    form = ReservaAdminForm
    list_display = ("socio", "sesion", "estado_color", "posicion", "creada")
    list_filter = ("estado", "sesion__clase", "sesion__inicio")
    search_fields = ("socio__rut", "socio__usuario__last_name", "sesion__clase__nombre")
    autocomplete_fields = ("socio",)
    list_select_related = ("socio__usuario", "sesion__clase")
    actions = ["cancelar"]

    @admin.display(description="Estado", ordering="estado")
    def estado_color(self, obj):
        return chip(obj.estado, obj.get_estado_display())

    @admin.display(description="Posición espera")
    def posicion(self, obj):
        return obj.posicion_espera or "—"

    def save_model(self, request, obj, form, change):
        if not change:
            sesion = obj.sesion
            obj.estado = (
                Reserva.Estado.CONFIRMADA if sesion.confirmadas < sesion.cupo_maximo else Reserva.Estado.EN_ESPERA
            )
            super().save_model(request, obj, form, change)
            return
        anterior = Reserva.objects.get(pk=obj.pk).estado
        super().save_model(request, obj, form, change)
        if anterior in Reserva.ESTADOS_CON_CUPO and obj.estado not in Reserva.ESTADOS_CON_CUPO:
            services.promover_lista_espera(obj.sesion)

    @admin.action(description="Cancelar reservas (promueve lista de espera)")
    def cancelar(self, request, queryset):
        for r in queryset.exclude(estado="CANCELADA"):
            try:
                promovida = services.cancelar_reserva(r)
                if promovida:
                    self.message_user(request, f"{promovida.socio.nombre} pasó a confirmado en {r.sesion}.")
            except ValidationError as e:
                self.message_user(request, " ".join(e.messages), messages.WARNING)
        self.message_user(request, "Reservas canceladas.")


@admin.register(RegistroAcceso)
class RegistroAccesoAdmin(admin.ModelAdmin):
    list_display = ("fecha_hora", "socio", "tipo", "resultado_color", "motivo", "latencia_ms", "registrado_por")
    list_filter = ("resultado", "tipo", "fecha_hora")
    search_fields = ("socio__rut", "socio__usuario__last_name", "codigo_leido", "motivo")
    date_hierarchy = "fecha_hora"
    list_select_related = ("socio__usuario", "registrado_por")

    @admin.display(description="Resultado", ordering="resultado")
    def resultado_color(self, obj):
        return chip(obj.resultado, obj.get_resultado_display())

    # La bitácora de accesos es de solo lectura (auditoría).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
