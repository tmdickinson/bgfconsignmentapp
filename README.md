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

Tests need Postgres running (`docker compose up -d`), because the double-sale protections rely on it. The test run creates and reuses a database named `test_bgf`. Add `--create-db` after changing models.

## Starting a new event

Each year is a new event with its own sellers, fees and records. Past events stay available to admins and are never changed. The steps are in the admin section of [docs/HOW_TO.md](docs/HOW_TO.md).

## Deployment

The board is leaning toward one managed cPanel server for the WordPress site, email and every Django app. The steps, including what to confirm with the host before buying, are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). On a cPanel server the app runs through `passenger_wsgi.py` and reads its settings from a `.env` file in its folder. Elsewhere, any host with Python 3.12 and PostgreSQL 14 or newer works, started with `gunicorn bgf.wsgi`.

## Seller signups from Google Forms

CSV import always works: download the responses from the form as CSV and upload them on the **Seller signups** page.

For the live connection, a developer creates a Google Cloud project with the Google Forms API enabled and an OAuth web client whose redirect URI is `<SITE_URL>/events/google/callback/`. Then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Someone with access to the form presses **Connect to Google Form** on the event, and new responses come in through **Sync now** or the scheduled command:

```sh
uv run manage.py sync_forms     # run every few minutes from the host's scheduler
```

## Configuration

All settings come from environment variables. See [.env.example](.env.example). Variables already set take priority over a `.env` file in the project folder. In production, set `DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `SITE_URL` and the `EMAIL_*` values, and leave `DJANGO_DEBUG` unset.

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
passenger_wsgi.py   Entry point for cPanel hosting
requirements.txt    Generated from uv.lock for hosts that use pip. See docs/DEPLOYMENT.md.
```

Business rules live in each app's `services.py`, not in views.
