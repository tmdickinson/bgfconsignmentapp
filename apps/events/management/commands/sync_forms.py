from django.core.management.base import BaseCommand

from apps.events.google_forms import GoogleError, sync_event
from apps.events.models import Event, Status


class Command(BaseCommand):
    help = "Fetch new Google Form responses for every connected, open event. Run on a schedule."

    def handle(self, *args, **options):
        events = Event.objects.exclude(status=Status.POSTSALE).filter(google__isnull=False)
        for event in events:
            try:
                summary = sync_event(event)
            except GoogleError as e:
                self.stderr.write(f"{event.code}: sync failed: {e}")
            else:
                self.stdout.write(f"{event.code}: {summary}")
