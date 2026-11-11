from .permissions import es_personal, es_socio


def roles(request):
    user = getattr(request, "user", None)
    return {
        "es_personal": es_personal(user),
        "es_socio": es_socio(user),
    }
