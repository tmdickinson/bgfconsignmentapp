from django.apps import AppConfig


class ConsignmentConfig(AppConfig):
    name = "apps.consignment"

    def ready(self):
        from apps.events.models import Status
        from apps.events.services import STATUS_CHECKS

        def no_open_carts(event):
            from .services.checkout import active_carts

            count = active_carts(event).count()
            return [f"{count} cart{'s are' if count != 1 else ' is'} still open. Finish or cancel "
                    "them first."] if count else []

        def ready_to_close(event):
            from .services.payouts import closing_blockers

            return closing_blockers(event)

        STATUS_CHECKS.setdefault(Status.PICKUP, []).append(no_open_carts)
        STATUS_CHECKS.setdefault(Status.POSTSALE, []).append(ready_to_close)
