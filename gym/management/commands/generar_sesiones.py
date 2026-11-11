from django.core.management.base import BaseCommand

from gym import services


class Command(BaseCommand):
    help = "Crea las sesiones de clases para los próximos N días según la programación semanal (se ejecuta por cron)."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=7)

    def handle(self, *args, **opts):
        n = services.generar_sesiones(opts["dias"])
        self.stdout.write(self.style.SUCCESS(f"{n} sesión(es) nuevas creadas."))
