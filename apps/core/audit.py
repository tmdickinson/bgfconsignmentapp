from .models import AuditLog


def audit(actor, action, *, event=None, target=None, **details):
    """Record an action. `actor` is an Account or None for the system."""
    return AuditLog.objects.create(
        actor=actor if actor and actor.is_authenticated else None,
        action=action,
        event=event,
        target_type=type(target).__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        details=details,
    )
