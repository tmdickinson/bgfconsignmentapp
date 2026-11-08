# Buckeye Game Fest Consignment Store

The web app that runs the convention's consignment store. Requirements and plans are in [docs/](docs/README.md), with [docs/SPEC.md](docs/SPEC.md) as the source of truth.

**Status:** All five planned phases are built: accounts and events, listings and slips, check-in, the point of sale, and pickup, payouts and closing. Undecided items are in [docs/OPEN_ITEMS.md](docs/OPEN_ITEMS.md).

**Using the app:** see [docs/HOW_TO.md](docs/HOW_TO.md) for admins, volunteers and sellers.

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

### Demo data

```sh
uv run --env-file .env manage.py load_demo
```

This adds demo staff, a closed 2025 event and a 2026 event in Sale, with 53 games in total. Every demo account's password is `demo-password-2026`. Sign in as `demo.admin@example.com`, with badge PIN 2468, or as seller `D26A-001`. The command only runs with `DJANGO_DEBUG=1`, so it can't be loaded on the real site by accident.

## Tests and checks

```sh
uv run --env-file .env pytest
uv run ruff check .
```

Tests need Postgres, because the double-sale protections rely on it. The test run creates and reuses a database named `test_bgf`. Add `--create-db` after changing models.

## Starting a new event

Each year is a new event with its own sellers, fees and records. Past events stay available to admins and are never changed. The steps are in the admin section of [docs/HOW_TO.md](docs/HOW_TO.md).

## Deployment

The app runs on any host with Python 3.12 and PostgreSQL 16. The build plan recommends Render, with a web service, a Postgres database and a cron job. Check current prices before signing up.

1. **Database:** create a PostgreSQL 16 database and copy its connection URL.
2. **Web service:** create one from this repository.
   - Build command: `uv sync --frozen && uv run manage.py collectstatic --noinput`
   - Pre-deploy command: `uv run manage.py migrate`
   - Start command: `uv run gunicorn bgf.wsgi --bind 0.0.0.0:$PORT`
3. **Environment variables:** set these on the web service:
   - `DJANGO_SECRET_KEY`: a long random value, generated once and kept secret
   - `DATABASE_URL`
   - `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`: the site's domain, with `https://` in the second
   - `SITE_URL`
   - Email: `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and `DEFAULT_FROM_EMAIL`, from a low-cost email service

   Leave `DJANGO_DEBUG` unset. HTTPS redirects and secure cookies turn on automatically.
4. **Cron job:** if the Google Form is connected, add one that runs `uv run manage.py sync_forms` every 10 minutes, with the same environment variables.
5. **First admin:** open a shell on the web service and run `uv run manage.py create_first_admin --name "..." --email ...`. It asks for the password.
6. **Backups:** turn on daily database backups. The database is the only thing that needs backing up.

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
apps/consignment/   Listings, slips, check-in, point of sale, pickup, payouts and reports
static/vendor/      HTMX, for live register alerts
templates/          Shared layout
static/css/app.css  The one stylesheet
```

Business rules live in each app's `services.py`, not in views.
