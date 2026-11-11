"""
Lógica de negocio de FitLife.

Se mantiene separada de las vistas para reutilizarla desde la API (DRF),
las vistas HTML, el Django Admin y los tests.
"""
import time
import uuid
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from .models import Clase, Instalacion, Membresia, RegistroAcceso, Reserva, Sesion, Socio


# ---------------------------------------------------------------------------
# Reservas con cupos y lista de espera
# ---------------------------------------------------------------------------
def verificar_membresia_para_reserva(socio: Socio, sesion: Sesion) -> None:
    Membresia.actualizar_vencidas()
    m = socio.membresia_actual()
    if m is None:
        raise ValidationError("El socio no tiene una membresía registrada.")
    if m.estado == Membresia.Estado.CONGELADA:
        raise ValidationError("La membresía está congelada.")
    if m.estado == Membresia.Estado.VENCIDA:
        raise ValidationError("La membresía está vencida.")
    if not m.esta_pagada:
        raise ValidationError("La membresía tiene pagos pendientes.")
    if timezone.localtime(sesion.inicio).date() > m.fecha_fin:
        raise ValidationError("La membresía vence antes de la fecha de la clase.")


@transaction.atomic
def reservar(socio: Socio, sesion: Sesion) -> Reserva:
    """
    Crea una reserva. Si hay cupo queda CONFIRMADA; si no, queda EN_ESPERA.
    Se bloquea la fila de la sesión (SELECT ... FOR UPDATE) para que dos
    reservas simultáneas no puedan sobrepasar el cupo.
    """
    sesion = Sesion.objects.select_for_update().select_related("clase").get(pk=sesion.pk)
    if sesion.cancelada:
        raise ValidationError("La sesión fue cancelada.")
    if sesion.ya_paso:
        raise ValidationError("La sesión ya comenzó o terminó.")
    if Reserva.objects.filter(sesion=sesion, socio=socio).exclude(estado=Reserva.Estado.CANCELADA).exists():
        raise ValidationError("El socio ya tiene una reserva para esta sesión.")
    verificar_membresia_para_reserva(socio, sesion)

    hay_cupo = sesion.confirmadas < sesion.cupo_maximo
    estado = Reserva.Estado.CONFIRMADA if hay_cupo else Reserva.Estado.EN_ESPERA
    return Reserva.objects.create(sesion=sesion, socio=socio, estado=estado)


@transaction.atomic
def promover_lista_espera(sesion: Sesion) -> list[Reserva]:
    """Mueve reservas EN_ESPERA a CONFIRMADA (orden de llegada) mientras haya cupo."""
    sesion = Sesion.objects.select_for_update().get(pk=sesion.pk)
    promovidas = []
    libres = sesion.cupo_maximo - sesion.confirmadas
    if libres <= 0 or sesion.cancelada:
        return promovidas
    espera = list(
        Reserva.objects.filter(sesion=sesion, estado=Reserva.Estado.EN_ESPERA).order_by("creada")[:libres]
    )
    for r in espera:
        r.estado = Reserva.Estado.CONFIRMADA
        r.save(update_fields=["estado", "actualizada"])
        promovidas.append(r)
    return promovidas


@transaction.atomic
def cancelar_reserva(reserva: Reserva) -> Reserva | None:
    """Cancela la reserva y, si liberó un cupo, promueve al primero de la lista de espera."""
    if reserva.estado == Reserva.Estado.CANCELADA:
        raise ValidationError("La reserva ya estaba cancelada.")
    if reserva.estado == Reserva.Estado.ASISTIO:
        raise ValidationError("No se puede cancelar una reserva con asistencia registrada.")
    liberaba_cupo = reserva.estado == Reserva.Estado.CONFIRMADA
    reserva.estado = Reserva.Estado.CANCELADA
    reserva.save(update_fields=["estado", "actualizada"])
    if liberaba_cupo:
        promovidas = promover_lista_espera(reserva.sesion)
        return promovidas[0] if promovidas else None
    return None


def generar_sesiones(dias: int = 7) -> int:
    """Crea las sesiones de los próximos `dias` días según la programación semanal de cada clase."""
    creadas = 0
    hoy = timezone.localdate()
    tz = timezone.get_current_timezone()
    for clase in Clase.objects.filter(activa=True):
        try:
            dias_clase = clase.lista_dias()
        except ValueError:
            continue
        for i in range(dias):
            fecha = hoy + timedelta(days=i)
            if fecha.weekday() not in dias_clase:
                continue
            inicio = timezone.make_aware(datetime.combine(fecha, clase.hora_inicio), tz)
            _, nueva = Sesion.objects.get_or_create(
                clase=clase, inicio=inicio, defaults={"cupo_maximo": clase.cupo_maximo}
            )
            creadas += int(nueva)
    return creadas


# ---------------------------------------------------------------------------
# Control de acceso por QR y aforo
# ---------------------------------------------------------------------------
def _socios_dentro_qs():
    """Socios cuyo último acceso PERMITIDO de hoy fue una ENTRADA."""
    hoy = timezone.localdate()
    ultimo = (
        RegistroAcceso.objects.filter(
            socio=OuterRef("pk"),
            resultado=RegistroAcceso.Resultado.PERMITIDO,
            fecha_hora__date=hoy,
        )
        .order_by("-fecha_hora", "-id")
    )
    return (
        Socio.objects.annotate(
            ultimo_tipo=Subquery(ultimo.values("tipo")[:1]),
            hora_ingreso=Subquery(ultimo.values("fecha_hora")[:1]),
        )
        .filter(ultimo_tipo=RegistroAcceso.Tipo.ENTRADA)
        .select_related("usuario")
    )


def aforo_actual() -> int:
    return _socios_dentro_qs().count()


def _extraer_uuid(codigo: str):
    texto = (codigo or "").strip()
    if texto.upper().startswith("FITLIFE:"):
        texto = texto.split(":", 1)[1]
    try:
        return uuid.UUID(texto)
    except ValueError:
        return None


@transaction.atomic
def validar_acceso(codigo: str, tipo: str = "AUTO", usuario=None) -> RegistroAcceso:
    """
    Valida un código QR leído en el torniquete y deja registro.

    tipo = "ENTRADA", "SALIDA" o "AUTO" (si el socio está dentro registra salida,
    si está fuera registra entrada).
    Reglas de ENTRADA: socio existe, membresía activa, vigente y pagada, sin
    ingreso duplicado y con aforo disponible.
    """
    t0 = time.perf_counter()
    Membresia.actualizar_vencidas()
    socio = None
    resultado = RegistroAcceso.Resultado.DENEGADO
    motivo = ""

    codigo_uuid = _extraer_uuid(codigo)
    if codigo_uuid:
        socio = Socio.objects.select_related("usuario").filter(codigo_qr=codigo_uuid).first()

    dentro = socio is not None and _socios_dentro_qs().filter(pk=socio.pk).exists()
    if tipo == "AUTO":
        tipo = RegistroAcceso.Tipo.SALIDA if dentro else RegistroAcceso.Tipo.ENTRADA

    if codigo_uuid is None:
        motivo = "Código QR con formato inválido."
    elif socio is None:
        motivo = "Código QR no corresponde a ningún socio."
    elif tipo == RegistroAcceso.Tipo.SALIDA:
        if dentro:
            resultado, motivo = RegistroAcceso.Resultado.PERMITIDO, "Salida registrada."
        else:
            motivo = "El socio no registra ingreso hoy."
    else:
        m = socio.membresia_actual()
        if dentro:
            motivo = "El socio ya se encuentra dentro (anti-passback)."
        elif m is None:
            motivo = "Sin membresía registrada."
        elif m.estado == Membresia.Estado.VENCIDA:
            motivo = f"Membresía vencida el {m.fecha_fin:%d-%m-%Y}."
        elif m.estado == Membresia.Estado.CONGELADA:
            motivo = "Membresía congelada."
        elif not m.esta_pagada:
            motivo = "Membresía con pago pendiente."
        elif aforo_actual() >= Instalacion.principal().capacidad_maxima:
            motivo = "Aforo máximo alcanzado. Espere a que salga una persona."
        else:
            resultado = RegistroAcceso.Resultado.PERMITIDO
            motivo = f"Bienvenido/a. Membresía {m.plan.nombre} vigente hasta {m.fecha_fin:%d-%m-%Y}."

    latencia = (time.perf_counter() - t0) * 1000
    return RegistroAcceso.objects.create(
        socio=socio,
        codigo_leido=(codigo or "")[:100],
        tipo=tipo,
        resultado=resultado,
        motivo=motivo,
        latencia_ms=round(latencia, 2),
        registrado_por=usuario if (usuario and usuario.is_authenticated) else None,
    )


def resumen_aforo() -> dict:
    """Datos para el dashboard en tiempo real."""
    Membresia.actualizar_vencidas()
    inst = Instalacion.principal()
    hoy = timezone.localdate()
    ahora = timezone.now()
    dentro = list(_socios_dentro_qs().order_by("-hora_ingreso"))
    ocupacion = len(dentro)
    porcentaje = round(ocupacion * 100 / inst.capacidad_maxima, 1) if inst.capacidad_maxima else 0
    if porcentaje >= 100:
        nivel = "lleno"
    elif porcentaje >= inst.umbral_alerta:
        nivel = "alerta"
    else:
        nivel = "normal"

    accesos_hoy = RegistroAcceso.objects.filter(fecha_hora__date=hoy)
    stats = accesos_hoy.aggregate(
        ingresos=Count("id", filter=Q(tipo="ENTRADA", resultado="PERMITIDO")),
        salidas=Count("id", filter=Q(tipo="SALIDA", resultado="PERMITIDO")),
        denegados=Count("id", filter=Q(resultado="DENEGADO")),
    )
    latencias = list(accesos_hoy.values_list("latencia_ms", flat=True)[:200])
    ultimos = accesos_hoy.select_related("socio__usuario")[:10]

    sesiones_hoy = (
        Sesion.objects.filter(inicio__date=hoy, cancelada=False)
        .select_related("clase")
        .annotate(
            n_conf=Count("reservas", filter=Q(reservas__estado__in=Reserva.ESTADOS_CON_CUPO)),
            n_esp=Count("reservas", filter=Q(reservas__estado=Reserva.Estado.EN_ESPERA)),
        )
    )
    membresias = dict(
        Membresia.objects.filter(fecha_inicio__lte=hoy)
        .values_list("estado")
        .annotate(n=Count("id"))
    )

    return {
        "instalacion": inst.nombre,
        "capacidad": inst.capacidad_maxima,
        "ocupacion": ocupacion,
        "disponibles": max(inst.capacidad_maxima - ocupacion, 0),
        "porcentaje": porcentaje,
        "nivel": nivel,
        "umbral_alerta": inst.umbral_alerta,
        "actualizado": timezone.localtime(ahora).strftime("%H:%M:%S"),
        "ingresos_hoy": stats["ingresos"],
        "salidas_hoy": stats["salidas"],
        "denegados_hoy": stats["denegados"],
        "latencia_promedio_ms": round(sum(latencias) / len(latencias), 2) if latencias else None,
        "membresias": {
            "ACTIVA": membresias.get("ACTIVA", 0),
            "VENCIDA": membresias.get("VENCIDA", 0),
            "CONGELADA": membresias.get("CONGELADA", 0),
        },
        "dentro": [
            {
                "socio": s.nombre,
                "rut": s.rut,
                "desde": timezone.localtime(s.hora_ingreso).strftime("%H:%M"),
            }
            for s in dentro
        ],
        "ultimos_accesos": [
            {
                "hora": timezone.localtime(r.fecha_hora).strftime("%H:%M:%S"),
                "socio": r.socio.nombre if r.socio else "Desconocido",
                "tipo": r.tipo,
                "resultado": r.resultado,
                "motivo": r.motivo,
                "latencia_ms": r.latencia_ms,
            }
            for r in ultimos
        ],
        "clases_hoy": [
            {
                "id": s.id,
                "clase": s.clase.nombre,
                "hora": timezone.localtime(s.inicio).strftime("%H:%M"),
                "sala": s.clase.sala,
                "confirmadas": s.n_conf,
                "cupo": s.cupo_maximo,
                "en_espera": s.n_esp,
                "finalizada": s.inicio + timedelta(minutes=s.clase.duracion_minutos) < ahora,
            }
            for s in sesiones_hoy
        ],
    }
