"""Vistas HTML (templates) de FitLife."""
from collections import OrderedDict
from datetime import timedelta
from io import BytesIO

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import ProtectedError, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from . import services
from .forms import (
    ClaseForm, MembresiaForm, PagoForm, PlanForm, ReservaManualForm, SesionForm, SocioForm,
)
from .models import Clase, Membresia, Pago, Plan, RegistroAcceso, Reserva, Sesion, Socio
from .permissions import SoloPersonalMixin, SoloSocioMixin, es_personal, es_socio

COLORES_ESTADO = {
    "ACTIVA": "success", "VENCIDA": "danger", "CONGELADA": "info",
    "PAGADO": "success", "PENDIENTE": "warning", "ANULADO": "secondary",
    "CONFIRMADA": "success", "EN_ESPERA": "warning", "CANCELADA": "secondary", "ASISTIO": "primary",
    "PERMITIDO": "success", "DENEGADO": "danger",
}


def badge(valor, texto=None):
    return format_html('<span class="badge text-bg-{}">{}</span>', COLORES_ESTADO.get(valor, "secondary"), texto or valor)


def clp(valor):
    return f"${valor:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Inicio y páginas por rol
# ---------------------------------------------------------------------------
@login_required
def inicio(request):
    if es_personal(request.user):
        return redirect("dashboard")
    if es_socio(request.user):
        return redirect("mi_cuenta")
    messages.warning(request, "Tu usuario no tiene un rol asignado. Contacta a administración.")
    return render(request, "gym/sin_rol.html")


class DashboardView(LoginRequiredMixin, SoloPersonalMixin, TemplateView):
    template_name = "gym/dashboard.html"


class AccesoView(LoginRequiredMixin, SoloPersonalMixin, TemplateView):
    """Pantalla del torniquete: lector QR (teclado) o simulación de escaneo."""
    template_name = "gym/acceso.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        Membresia.actualizar_vencidas()
        socios = []
        for s in Socio.objects.select_related("usuario"):
            m = s.membresia_actual()
            socios.append({
                "obj": s,
                "estado": m.estado if m else "SIN MEMBRESÍA",
                "pagada": m.esta_pagada if m else False,
            })
        ctx["socios"] = socios
        return ctx


class ClasesView(LoginRequiredMixin, TemplateView):
    template_name = "gym/clases.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ahora = timezone.now()
        sesiones = (
            Sesion.objects.filter(inicio__gte=ahora - timedelta(hours=1), inicio__lte=ahora + timedelta(days=7))
            .select_related("clase").order_by("inicio")
        )
        socio = getattr(self.request.user, "socio", None)
        por_dia = OrderedDict()
        for s in sesiones:
            mi = None
            if socio:
                mi = s.reservas.filter(socio=socio).exclude(estado=Reserva.Estado.CANCELADA).first()
            dia = timezone.localtime(s.inicio).date()
            por_dia.setdefault(dia, []).append({"s": s, "mi": mi})
        ctx["por_dia"] = por_dia
        return ctx


class SesionDetalleView(LoginRequiredMixin, SoloPersonalMixin, View):
    template_name = "gym/sesion_detalle.html"

    def get(self, request, pk):
        sesion = get_object_or_404(Sesion.objects.select_related("clase"), pk=pk)
        return render(request, self.template_name, {
            "sesion": sesion,
            "confirmadas": sesion.reservas.filter(estado__in=Reserva.ESTADOS_CON_CUPO).select_related("socio__usuario"),
            "espera": sesion.reservas.filter(estado=Reserva.Estado.EN_ESPERA).select_related("socio__usuario"),
            "canceladas": sesion.reservas.filter(estado=Reserva.Estado.CANCELADA).select_related("socio__usuario"),
            "form": ReservaManualForm(),
        })

    def post(self, request, pk):
        sesion = get_object_or_404(Sesion, pk=pk)
        form = ReservaManualForm(request.POST)
        if form.is_valid():
            try:
                r = services.reservar(form.cleaned_data["socio"], sesion)
                messages.success(request, f"Reserva creada: {r.get_estado_display()}.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
        return redirect("sesion_detalle", pk=pk)


@login_required
@require_POST
def reserva_cancelar(request, pk):
    reserva = get_object_or_404(Reserva, pk=pk)
    if not (es_personal(request.user) or reserva.socio.usuario_id == request.user.id):
        raise PermissionDenied
    try:
        promovida = services.cancelar_reserva(reserva)
        msg = "Reserva cancelada."
        if promovida:
            msg += f" {promovida.socio.nombre} pasó de la lista de espera a confirmado."
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect(request.POST.get("next") or "clases")


@login_required
@require_POST
def reserva_asistio(request, pk):
    if not es_personal(request.user):
        raise PermissionDenied
    reserva = get_object_or_404(Reserva, pk=pk, estado=Reserva.Estado.CONFIRMADA)
    reserva.estado = Reserva.Estado.ASISTIO
    reserva.save(update_fields=["estado", "actualizada"])
    messages.success(request, f"Asistencia registrada para {reserva.socio.nombre}.")
    return redirect("sesion_detalle", pk=reserva.sesion_id)


class MiCuentaView(LoginRequiredMixin, SoloSocioMixin, TemplateView):
    template_name = "gym/mi_cuenta.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        Membresia.actualizar_vencidas()
        socio = self.request.user.socio
        ctx["socio"] = socio
        ctx["membresia"] = socio.membresia_actual()
        ctx["historial"] = socio.membresias.select_related("plan")
        ctx["pagos"] = Pago.objects.filter(membresia__socio=socio).select_related("membresia__plan")[:10]
        ctx["reservas"] = (
            socio.reservas.select_related("sesion__clase")
            .filter(sesion__inicio__gte=timezone.now() - timedelta(hours=1))
            .exclude(estado=Reserva.Estado.CANCELADA)
            .order_by("sesion__inicio")
        )
        ctx["accesos"] = socio.accesos.all()[:5]
        return ctx


@login_required
def qr_svg(request, pk):
    """Devuelve el QR del socio en SVG (solo el propio socio o el personal)."""
    socio = get_object_or_404(Socio, pk=pk)
    if not (es_personal(request.user) or socio.usuario_id == request.user.id):
        raise PermissionDenied
    img = qrcode.make(str(socio.codigo_qr), image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buf = BytesIO()
    img.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="image/svg+xml")
    resp["Cache-Control"] = "private, max-age=60"
    return resp


@login_required
@require_POST
def qr_regenerar(request, pk):
    socio = get_object_or_404(Socio, pk=pk)
    if not (es_personal(request.user) or socio.usuario_id == request.user.id):
        raise PermissionDenied
    socio.regenerar_qr()
    messages.success(request, "Se generó un nuevo código QR. El anterior ya no es válido.")
    return redirect(request.POST.get("next") or "inicio")


@login_required
@require_POST
def membresia_accion(request, pk, accion):
    if not es_personal(request.user):
        raise PermissionDenied
    m = get_object_or_404(Membresia, pk=pk)
    try:
        if accion == "congelar":
            m.congelar()
            messages.success(request, f"Membresía de {m.socio.nombre} congelada.")
        elif accion == "descongelar":
            dias = m.descongelar()
            messages.success(request, f"Membresía reactivada; se extendió {dias} día(s) hasta {m.fecha_fin:%d-%m-%Y}.")
        else:
            raise Http404
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    return redirect("crud_list", entidad="membresias")


# ---------------------------------------------------------------------------
# CRUD genérico (HTML) para recepción
# ---------------------------------------------------------------------------
def _estado_membresia_socio(s):
    m = s.membresia_actual()
    return badge(m.estado, m.get_estado_display()) if m else badge("", "Sin membresía")


ENTIDADES = {
    "planes": {
        "modelo": Plan, "form": PlanForm, "titulo": "Planes",
        "buscar": ["nombre"],
        "columnas": [
            ("Nombre", lambda o: o.nombre),
            ("Precio", lambda o: clp(o.precio)),
            ("Duración", lambda o: f"{o.duracion_dias} días"),
            ("Activo", lambda o: badge("ACTIVA" if o.activo else "ANULADO", "Sí" if o.activo else "No")),
        ],
    },
    "socios": {
        "modelo": Socio, "form": SocioForm, "titulo": "Socios",
        "qs": lambda: Socio.objects.select_related("usuario"),
        "buscar": ["rut", "usuario__first_name", "usuario__last_name", "usuario__username"],
        "columnas": [
            ("Nombre", lambda o: o.nombre),
            ("RUT", lambda o: o.rut),
            ("Usuario", lambda o: o.usuario.username),
            ("Teléfono", lambda o: o.telefono or "—"),
            ("Membresía", _estado_membresia_socio),
            ("QR", lambda o: format_html('<a href="{}" target="_blank">ver QR</a>', reverse("qr_svg", args=[o.pk]))),
        ],
    },
    "membresias": {
        "modelo": Membresia, "form": MembresiaForm, "titulo": "Membresías",
        "qs": lambda: Membresia.objects.select_related("socio__usuario", "plan"),
        "buscar": ["socio__rut", "socio__usuario__first_name", "socio__usuario__last_name", "plan__nombre"],
        "filtro_estado": Membresia.Estado.choices,
        "acciones_membresia": True,
        "columnas": [
            ("Socio", lambda o: o.socio.nombre),
            ("Plan", lambda o: o.plan.nombre),
            ("Inicio", lambda o: f"{o.fecha_inicio:%d-%m-%Y}"),
            ("Término", lambda o: f"{o.fecha_fin:%d-%m-%Y}"),
            ("Estado", lambda o: badge(o.estado, o.get_estado_display())),
            ("Pagada", lambda o: badge("PAGADO" if o.esta_pagada else "PENDIENTE", "Sí" if o.esta_pagada else "Pendiente")),
        ],
    },
    "pagos": {
        "modelo": Pago, "form": PagoForm, "titulo": "Pagos",
        "qs": lambda: Pago.objects.select_related("membresia__socio__usuario", "membresia__plan"),
        "buscar": ["referencia", "membresia__socio__rut", "membresia__socio__usuario__last_name"],
        "filtro_estado": Pago.Estado.choices,
        "columnas": [
            ("Fecha", lambda o: f"{timezone.localtime(o.fecha_pago):%d-%m-%Y %H:%M}"),
            ("Socio", lambda o: o.membresia.socio.nombre),
            ("Plan", lambda o: o.membresia.plan.nombre),
            ("Monto", lambda o: clp(o.monto)),
            ("Método", lambda o: o.get_metodo_display()),
            ("Estado", lambda o: badge(o.estado, o.get_estado_display())),
        ],
    },
    "clases": {
        "modelo": Clase, "form": ClaseForm, "titulo": "Clases grupales",
        "buscar": ["nombre", "instructor"],
        "columnas": [
            ("Clase", lambda o: o.nombre),
            ("Instructor", lambda o: o.instructor),
            ("Sala", lambda o: o.sala),
            ("Cupo", lambda o: o.cupo_maximo),
            ("Horario", lambda o: f"{o.dias_legibles()} {o.hora_inicio:%H:%M}"),
            ("Activa", lambda o: badge("ACTIVA" if o.activa else "ANULADO", "Sí" if o.activa else "No")),
        ],
    },
    "sesiones": {
        "modelo": Sesion, "form": SesionForm, "titulo": "Sesiones programadas",
        "qs": lambda: Sesion.objects.select_related("clase").filter(inicio__gte=timezone.now() - timedelta(days=1)),
        "buscar": ["clase__nombre"],
        "columnas": [
            ("Clase", lambda o: o.clase.nombre),
            ("Inicio", lambda o: f"{timezone.localtime(o.inicio):%a %d-%m %H:%M}"),
            ("Confirmadas", lambda o: f"{o.confirmadas}/{o.cupo_maximo}"),
            ("En espera", lambda o: o.en_espera),
            ("Lista", lambda o: format_html('<a href="{}">ver lista</a>', reverse("sesion_detalle", args=[o.pk]))),
        ],
    },
    "accesos": {
        "modelo": RegistroAcceso, "form": None, "titulo": "Registro de accesos",
        "solo_lectura": True,
        "qs": lambda: RegistroAcceso.objects.select_related("socio__usuario"),
        "buscar": ["codigo_leido", "socio__rut", "socio__usuario__last_name", "motivo"],
        "filtro_estado": RegistroAcceso.Resultado.choices,
        "campo_estado": "resultado",
        "columnas": [
            ("Fecha/hora", lambda o: f"{timezone.localtime(o.fecha_hora):%d-%m %H:%M:%S}"),
            ("Socio", lambda o: o.socio.nombre if o.socio else "Desconocido"),
            ("Tipo", lambda o: o.get_tipo_display()),
            ("Resultado", lambda o: badge(o.resultado, o.get_resultado_display())),
            ("Motivo", lambda o: o.motivo),
            ("Latencia", lambda o: f"{o.latencia_ms:.1f} ms"),
        ],
    },
}


class EntidadMixin(LoginRequiredMixin, SoloPersonalMixin):
    def dispatch(self, request, *args, **kwargs):
        self.cfg = ENTIDADES.get(kwargs["entidad"])
        if self.cfg is None:
            raise Http404
        self.model = self.cfg["modelo"]
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.cfg.get("qs", lambda: self.model.objects.all())()

    def get_form_class(self):
        if self.cfg.get("solo_lectura"):
            raise Http404
        return self.cfg["form"]

    def get_success_url(self):
        return reverse("crud_list", kwargs={"entidad": self.kwargs["entidad"]})

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cfg"] = self.cfg
        ctx["entidad"] = self.kwargs["entidad"]
        ctx["nombre_modelo"] = self.model._meta.verbose_name
        return ctx


class CrudListView(EntidadMixin, ListView):
    template_name = "gym/crud_list.html"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q:
            filtro = Q()
            for campo in self.cfg["buscar"]:
                filtro |= Q(**{f"{campo}__icontains": q})
            qs = qs.filter(filtro)
        estado = self.request.GET.get("estado")
        if estado and self.cfg.get("filtro_estado"):
            qs = qs.filter(**{self.cfg.get("campo_estado", "estado"): estado})
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filas"] = [(o, [fn(o) for _, fn in self.cfg["columnas"]]) for o in ctx["object_list"]]
        ctx["encabezados"] = [c for c, _ in self.cfg["columnas"]]
        ctx["q"] = self.request.GET.get("q", "")
        ctx["estado_sel"] = self.request.GET.get("estado", "")
        return ctx


class CrudCreateView(EntidadMixin, CreateView):
    template_name = "gym/crud_form.html"

    def get_initial(self):
        inicial = super().get_initial()
        if self.kwargs["entidad"] == "pagos" and self.request.GET.get("membresia"):
            m = Membresia.objects.filter(pk=self.request.GET["membresia"]).select_related("plan").first()
            if m:
                inicial.update(membresia=m, monto=m.plan.precio - m.total_pagado or m.plan.precio)
        if self.kwargs["entidad"] == "membresias" and self.request.GET.get("socio"):
            inicial["socio"] = self.request.GET["socio"]
        return inicial

    def form_valid(self, form):
        if self.kwargs["entidad"] == "pagos":
            form.instance.registrado_por = self.request.user
        resp = super().form_valid(form)
        messages.success(self.request, f"{self.model._meta.verbose_name.capitalize()} creado/a correctamente.")
        return resp


class CrudUpdateView(EntidadMixin, UpdateView):
    template_name = "gym/crud_form.html"

    def form_valid(self, form):
        resp = super().form_valid(form)
        if isinstance(self.object, Sesion):
            promovidas = services.promover_lista_espera(self.object)
            if promovidas:
                messages.info(self.request, f"{len(promovidas)} reserva(s) promovidas desde la lista de espera.")
        messages.success(self.request, "Cambios guardados.")
        return resp


class CrudDeleteView(EntidadMixin, DeleteView):
    template_name = "gym/crud_confirm_delete.html"

    def form_valid(self, form):
        if self.cfg.get("solo_lectura"):
            raise Http404
        try:
            obj = self.get_object()
            if isinstance(obj, Socio):
                obj.usuario.delete()
            else:
                obj.delete()
            messages.success(self.request, "Registro eliminado.")
        except ProtectedError:
            messages.error(self.request, "No se puede eliminar: tiene registros asociados (por ejemplo, membresías). Puede desactivarlo.")
        return redirect(self.get_success_url())
