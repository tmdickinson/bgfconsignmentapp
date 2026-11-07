# Buckeye Game Fest Consignment Store

The web app that runs the convention's consignment store. Requirements and plans are in [docs/](docs/README.md), with [docs/SPEC.md](docs/SPEC.md) as the source of truth.

**Status:** Phases 1 and 2 of 5 are built: accounts, sign-in, staff, admin badges, events, seller signups, listings and printable sales slips.

## Local setup

You need [uv](https://docs.astral.sh/uv/) and PostgreSQL 16. Docker is the easiest way to get Postgres:

```sh
docker compose up -d          # Postgres on localhost:5432, user/password/database "bgf"
cp .env.example .env          # then set DJANGO_SECRET_KEY to any long random text
uv sync
uv run --env-file .env manage.py migrate
uv run --env-file .env manage.py create_first_admin --name "Your Name" --email you@example.com
uv run --env-file .env manage.py runserver
```

Open http://localhost:8000 and sign in with the admin's email. Emails, such as set-password links, are printed in the terminal during local development.

Without Docker, any Postgres 16 works. Point `DATABASE_URL` in `.env` at it.

## Tests and checks

```sh
uv run --env-file .env pytest
uv run ruff check .
```

Tests need Postgres, because the double-sale protections rely on it. The test run creates and reuses a database named `test_bgf`. Add `--create-db` after changing models.

## First steps in the app

1. On **My account**, set your 4-digit override PIN and print your badge.
2. Under **Staff**, add the other admins and volunteers. Each gets an email to set a password. Volunteers get a Volunteer ID, and admins get a badge.
3. Under **Events**, create the event. Fees lock when the first seller signs up.
4. Move the event to **Presale** when registration opens, then import signups on the event's **Seller signups** page.

## Seller signups from Google Forms

CSV import always works: download the responses from the form as CSV and upload them on the **Seller signups** page.

For the live connection, a developer creates a Google Cloud project with the Google Forms API enabled and an OAuth web client whose redirect URI is `<SITE_URL>/events/google/callback/`. Then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Someone with access to the form presses **Connect to Google Form** on the event, and new responses come in through **Sync now** or the scheduled command:

```sh
uv run manage.py sync_forms     # run every few minutes from the host's scheduler
```

## Configuration

All settings come from environment variables. See [.env.example](.env.example). In production, set `DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL` and the `EMAIL_*` values, and leave `DJANGO_DEBUG` unset.

**Branding:** the convention and app names come from `BRAND_CONVENTION` and `BRAND_APP`, and a logo from `BRAND_LOGO` (a path under `static/`). Colors are at the top of [static/css/app.css](static/css/app.css).

## Code layout

```
bgf/                Settings and URLs
apps/core/          Audit log, permissions, IDs, money helpers, badge-and-PIN override step
apps/accounts/      Accounts, staff, sellers, sign-in, seller linking, badges
apps/events/        Events, statuses, price windows, fees, waivers, signup import
apps/consignment/   Listings, slip versions and slip PDFs
templates/          Shared layout
static/css/app.css  The one stylesheet
```

Business rules live in each app's `services.py`, not in views.
