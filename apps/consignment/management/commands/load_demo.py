import datetime
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Account
from apps.accounts.services import accept_rules, create_seller, create_staff, link_seller
from apps.consignment.models import ItemStatus, PayoutOutcome, Register
from apps.consignment.services import checkin, checkout, fees, listings, payouts, pickup
from apps.events.models import Event
from apps.events.services import advance_status, create_event, set_waiver

PASSWORD = "demo-password-2026"
GAMES = [
    ("Wingspan", "Stonemaier Games"), ("Azul", "Plan B Games"), ("Catan", "Catan Studio"),
    ("Root", "Leder Games"), ("Ticket to Ride", "Days of Wonder"), ("Splendor", "Space Cowboys"),
    ("Pandemic", "Z-Man Games"), ("Carcassonne", "Z-Man Games"), ("Everdell", "Starling Games"),
    ("Cascadia", "Flatout Games"), ("Terraforming Mars", "FryxGames"), ("Dominion", "Rio Grande"),
    ("7 Wonders", "Repos Production"), ("Codenames", "Czech Games Edition"),
    ("Brass: Birmingham", "Roxley"), ("Patchwork", "Lookout Games"), ("Agricola", "Lookout Games"),
    ("Scythe", "Stonemaier Games"), ("Kingdomino", "Blue Orange"), ("Dixit", "Libellud"),
]
SELLERS = ["Pat Jones", "Lee Smith", "Robin Diaz", "Sam Okafor", "Alex Chen", "Jordan Reyes"]


class Command(BaseCommand):
    help = ("Load demo data: staff, a closed past event and a current event in Sale with about "
            "50 games. For local development and training only.")

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Allow loading when DEBUG is off. Never use on the real site.")

    def handle(self, *args, force, **options):
        if not settings.DEBUG and not force:
            raise CommandError("Demo data is for local development. DEBUG is off.")
        if Event.objects.filter(code__in=["D25A", "D26A"]).exists():
            raise CommandError("Demo data is already loaded.")
        self.rng = random.Random(2026)
        with transaction.atomic():
            self.staff()
            self.past_event()
            self.current_event()
        self.stdout.write(self.style.SUCCESS(
            "Demo data loaded. Every demo account's password is " + PASSWORD + "\n"
            f"  Admin:      demo.admin@example.com (badge {self.admin.staff.admin_id}, PIN 2468)\n"
            f"  Volunteer:  {self.volunteer.staff.volunteer_id}\n"
            "  Sellers:    D26A-001 to D26A-006 for the current event"))

    def staff(self):
        def make(name, email, **roles):
            existing = Account.objects.filter(email=email, staff_profile__isnull=False).first()
            return existing or create_staff(None, name=name, email=email, password=PASSWORD,
                                            send_email=False, **roles).account
        self.admin = make("Demo Admin", "demo.admin@example.com", is_admin=True,
                          is_volunteer=True)
        self.admin.staff.set_pin("2468")
        self.admin.staff.save()
        self.volunteer = make("Val Demo", "val.demo@example.com", is_admin=False,
                              is_volunteer=True)
        self.helper = make("Kim Demo", "kim.demo@example.com", is_admin=False,
                           is_volunteer=True)

    def build(self, code, name, year, sellers, games_each):
        event = create_event(self.admin, window_names=["Friday", "Saturday", "Sunday"],
                             code=code, name=name, start_date=datetime.date(year, 11, 20),
                             end_date=datetime.date(year, 11, 22))
        event = advance_status(self.admin, event, "setup")  # To Presale
        result = []
        for i, seller_name in enumerate(sellers):
            email = seller_name.lower().replace(" ", ".") + f".{year}@example.com"
            seller = create_seller(event, name=seller_name, email=email,
                                   payment_method=self.rng.choice(["PayPal", "Check", "Venmo"]),
                                   send_email=False)
            seller.account.set_password(PASSWORD)
            seller.account.save()
            accept_rules(seller, seller.account)
            games = []
            for j in range(games_each[i]):
                title, publisher = GAMES[(i * 7 + j) % len(GAMES)]
                top = self.rng.choice([15, 20, 25, 30, 40])
                prices = dict(zip([w.pk for w in event.windows.all()],
                                  [top, top * 3 // 4, max(1, top // 2)], strict=True))
                games.append(listings.create_listing(seller, seller.account, {
                    "name": title, "publisher": publisher,
                    "description": "All components present. From a smoke-free home.",
                    "condition": self.rng.choice(["new", "good", "good"]), "condition_note": "",
                    "is_bundle": False, "bundle_count": None, "bgg_id": None,
                    "slip_size": self.rng.choice(["large", "small"]),
                    "donate_if_unsold": j % 5 == 4}, prices))
            listings.generate_slips(seller, seller.account)
            result.append((seller, games))
        if len(result) > 1:
            link_seller(self.volunteer, result[-1][0].seller_id, PASSWORD)
            set_waiver(self.admin, event, category="volunteer", commission=False, listing=True,
                       unsold=True)
        event = advance_status(self.admin, event, "presale")  # To Check-in
        self.share(event, result)
        for seller, games in result:
            report = checkin.get_report(seller, self.helper)
            for game in games[:-1]:  # One game per seller never arrives.
                game.refresh_from_db()
                checkin.accept(report, self.helper, game.current_slip)
            checkin.complete(report, self.helper)
        event = advance_status(self.admin, event, "checkin")  # To Sale
        self.share(event, result)
        event = checkout.set_active_window(self.admin, event, event.windows.first())
        registers = [Register.objects.create(event=event, name=f"Register {n}") for n in (1, 2)]
        return event, result, registers

    @staticmethod
    def share(event, result):
        """Give every seller the current event, so status checks see the latest status."""
        for seller, _ in result:
            seller.event = event

    def sell(self, event, result, registers, share):
        checked_in = [g for _, games in result for g in games[:-1]]
        self.rng.shuffle(checked_in)
        to_sell = checked_in[:int(len(checked_in) * share)]
        for n, start in enumerate(range(0, len(to_sell), 3)):
            who = self.volunteer if n % 2 else self.helper
            c = checkout.start_regular(who, event, registers[n % 2])
            for game in to_sell[start:start + 3]:
                game.refresh_from_db()
                checkout.scan(c, who, game.accepted_slip.item_id)
            checkout.proceed_to_payment(c, who)
            checkout.payment_received(c, who)

    def past_event(self):
        event, result, registers = self.build("D25A", "Buckeye Game Fest 2025 (demo)", 2025,
                                              SELLERS[:4], [4, 3, 3, 3])
        self.sell(event, result, registers, 0.5)
        event = advance_status(self.admin, event, "sale")  # To Pickup
        self.share(event, result)
        for seller, games in result:
            for game in games:
                game.refresh_from_db()
                if game.status == ItemStatus.AVAILABLE and not game.accepted_slip.donate_if_unsold:
                    pickup.scan_return(seller, self.helper, game.accepted_slip.item_id)
        for _, games in result:
            for game in games:
                game.refresh_from_db()
                if game.status == ItemStatus.AVAILABLE:
                    pickup.scan_unsold(event, self.admin, game.accepted_slip.item_id)
        payouts.record_automatic(event, self.admin)
        for seller, _ in result:
            seller.refresh_from_db()
            if not hasattr(seller, "settlement") and not payouts.blockers(seller):
                f = fees.calculate(seller)
                outcome = payouts.allowed_outcomes(f)[0]
                payouts.record(seller, self.admin, outcome,
                               f"Check {1000 + seller.number}"
                               if outcome == PayoutOutcome.PAID else "")
        advance_status(self.admin, event, "pickup")

    def current_event(self):
        event, result, registers = self.build("D26A", "Buckeye Game Fest 2026 (demo)", 2026,
                                              SELLERS, [8, 7, 7, 6, 6, 6])
        self.sell(event, result, registers, 0.3)
