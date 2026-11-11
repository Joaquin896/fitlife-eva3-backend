"""Validación de RUT chileno (algoritmo módulo 11)."""
from django.core.exceptions import ValidationError


def normalizar_rut(rut: str) -> str:
    """Convierte '12.345.678-5' o '123456785' en '12345678-5'."""
    limpio = (rut or "").replace(".", "").replace("-", "").replace(" ", "").upper()
    if len(limpio) < 2:
        return limpio
    return f"{limpio[:-1]}-{limpio[-1]}"


def calcular_dv(cuerpo: str) -> str:
    suma, factor = 0, 2
    for digito in reversed(cuerpo):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    return {11: "0", 10: "K"}.get(resto, str(resto))


def validar_rut(valor: str) -> None:
    rut = normalizar_rut(valor)
    if "-" not in rut:
        raise ValidationError("RUT inválido.")
    cuerpo, dv = rut.split("-")
    if not cuerpo.isdigit() or not (7 <= len(cuerpo) <= 8):
        raise ValidationError("RUT inválido: el cuerpo debe tener 7 u 8 dígitos.")
    if calcular_dv(cuerpo) != dv:
        raise ValidationError("RUT inválido: dígito verificador incorrecto.")
