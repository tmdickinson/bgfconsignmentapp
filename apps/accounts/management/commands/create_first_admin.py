import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import StaffProfile
from apps.accounts.services import create_staff


class Command(BaseCommand):
    help = ("Create the first admin account. Run once on a new installation. "
            "The password is typed at a prompt, never on the command line.")

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--volunteer", action="store_true",
                            help="Also give the account the Volunteer role and a Volunteer ID.")

    def handle(self, *args, name, email, volunteer, **options):
        if StaffProfile.objects.filter(is_admin=True).exists():
            raise CommandError("An admin already exists. Admins create other staff in the app.")
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Password again: "):
            raise CommandError("The passwords don't match.")
        try:
            validate_password(password)
        except ValidationError as e:
            raise CommandError(" ".join(e.messages)) from e
        profile = create_staff(None, name=name, email=email, is_admin=True,
                               is_volunteer=volunteer, password=password, send_email=False)
        self.stdout.write(self.style.SUCCESS(
            f"Created admin {profile.name} ({profile.account.email}), badge {profile.admin_id}"
            + (f", Volunteer ID {profile.volunteer_id}" if profile.volunteer_id else "")
            + ". Sign in with the email address and set a PIN on the account page."))
