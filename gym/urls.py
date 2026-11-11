from django.urls import path

from . import views

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("acceso/", views.AccesoView.as_view(), name="acceso"),
    path("clases/", views.ClasesView.as_view(), name="clases"),
    path("sesiones/<int:pk>/", views.SesionDetalleView.as_view(), name="sesion_detalle"),
    path("reservas/<int:pk>/cancelar/", views.reserva_cancelar, name="reserva_cancelar"),
    path("reservas/<int:pk>/asistio/", views.reserva_asistio, name="reserva_asistio"),
    path("mi-cuenta/", views.MiCuentaView.as_view(), name="mi_cuenta"),
    path("socios/<int:pk>/qr.svg", views.qr_svg, name="qr_svg"),
    path("socios/<int:pk>/qr/regenerar/", views.qr_regenerar, name="qr_regenerar"),
    path("membresias/<int:pk>/<str:accion>/", views.membresia_accion, name="membresia_accion"),
    # CRUD genérico para recepción
    path("gestion/<slug:entidad>/", views.CrudListView.as_view(), name="crud_list"),
    path("gestion/<slug:entidad>/nuevo/", views.CrudCreateView.as_view(), name="crud_create"),
    path("gestion/<slug:entidad>/<int:pk>/editar/", views.CrudUpdateView.as_view(), name="crud_update"),
    path("gestion/<slug:entidad>/<int:pk>/eliminar/", views.CrudDeleteView.as_view(), name="crud_delete"),
]
