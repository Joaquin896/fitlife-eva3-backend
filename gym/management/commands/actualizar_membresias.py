from django.core.management.base import BaseCommand

from gym.models import Membresia


class Command(BaseCommand):
    help = "Marca como VENCIDA toda membresía activa cuya fecha de término ya pasó (se ejecuta por cron)."

    def handle(self, *args, **opts):
        n = Membresia.actualizar_vencidas()
        self.stdout.write(self.style.SUCCESS(f"{n} membresía(s) marcadas como vencidas."))
