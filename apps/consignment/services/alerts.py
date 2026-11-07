from ..models import RegisterConflict, SoldItemFlag


def open_alert_count(event):
    """Open two-register conflicts and Sold flags, which admins must resolve."""
    return (RegisterConflict.objects.filter(event=event, resolved_at__isnull=True).count()
            + SoldItemFlag.objects.filter(event=event, resolved_at__isnull=True).count())
