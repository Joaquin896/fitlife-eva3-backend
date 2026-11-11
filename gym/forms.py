from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.db import transaction

from .models import Clase, Membresia, Pago, Plan, Sesion, Socio
from .permissions import GRUPO_SOCIO

User = get_user_model()


class BootstrapMixin:
    """Aplica clases de Bootstrap a todos los campos del formulario."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class FechaInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class FechaHoraInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class PlanForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Plan
        fields = ["nombre", "descripcion", "precio", "duracion_dias", "activo"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}


class SocioForm(BootstrapMixin, forms.ModelForm):
    """Crea/edita el usuario de Django y su perfil de Socio en una sola operación."""

    username = forms.CharField(label="Nombre de usuario", max_length=150)
    first_name = forms.CharField(label="Nombre", max_length=150)
    last_name = forms.CharField(label="Apellido", max_length=150)
    email = forms.EmailField(label="Correo", required=False)
    password = forms.CharField(
        label="Contraseña", widget=forms.PasswordInput, required=False,
        help_text="Obligatoria al crear. Al editar, déjela vacía para no cambiarla.",
    )

    class Meta:
        model = Socio
        fields = ["rut", "telefono", "fecha_nacimiento"]
        widgets = {"fecha_nacimiento": FechaInput()}

    field_order = ["username", "first_name", "last_name", "email", "password", "rut", "telefono", "fecha_nacimiento"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            u = self.instance.usuario
            self.fields["username"].initial = u.username
            self.fields["first_name"].initial = u.first_name
            self.fields["last_name"].initial = u.last_name
            self.fields["email"].initial = u.email
        self.fields["rut"].help_text = "Formato 12345678-9 (se valida el dígito verificador)."

    def clean_username(self):
        username = self.cleaned_data["username"]
        qs = User.objects.filter(username=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.usuario_id)
        if qs.exists():
            raise forms.ValidationError("Ese nombre de usuario ya existe.")
        return username

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not self.instance.pk and not password:
            raise forms.ValidationError("La contraseña es obligatoria para un socio nuevo.")
        if password:
            validate_password(password)
        return password

    @transaction.atomic
    def save(self, commit=True):
        socio = super().save(commit=False)
        datos = {k: self.cleaned_data[k] for k in ("username", "first_name", "last_name", "email")}
        if socio.pk:
            usuario = socio.usuario
            for k, v in datos.items():
                setattr(usuario, k, v)
        else:
            usuario = User(**datos)
        if self.cleaned_data.get("password"):
            usuario.set_password(self.cleaned_data["password"])
        usuario.save()
        usuario.groups.add(Group.objects.get_or_create(name=GRUPO_SOCIO)[0])
        socio.usuario = usuario
        socio.save()
        return socio


class MembresiaForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Membresia
        fields = ["socio", "plan", "fecha_inicio", "fecha_fin", "estado", "observaciones"]
        widgets = {"fecha_inicio": FechaInput(), "fecha_fin": FechaInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_fin"].required = False
        if not self.instance.pk:
            self.fields["plan"].queryset = Plan.objects.filter(activo=True)


class PagoForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Pago
        fields = ["membresia", "monto", "metodo", "estado", "fecha_pago", "referencia"]
        widgets = {"fecha_pago": FechaHoraInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["membresia"].queryset = Membresia.objects.select_related("socio__usuario", "plan")


class ClaseForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Clase
        fields = ["nombre", "descripcion", "instructor", "sala", "cupo_maximo",
                  "duracion_minutos", "dias_semana", "hora_inicio", "activa"]
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 2}),
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }


class SesionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Sesion
        fields = ["clase", "inicio", "cupo_maximo", "cancelada"]
        widgets = {"inicio": FechaHoraInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cupo_maximo"].required = False


class ReservaManualForm(BootstrapMixin, forms.Form):
    socio = forms.ModelChoiceField(queryset=Socio.objects.select_related("usuario"), label="Socio")
