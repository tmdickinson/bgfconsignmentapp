# Deployment

The board is leaning toward one managed cPanel server for everything: the WordPress site, the email accounts and up to five Django apps, with InMotion Hosting as the leading candidate. The host takes care of patching, monitoring and backups. These steps assume a host like that. On any other host, the app needs Python 3.12 and PostgreSQL 14 or newer, and is started with `gunicorn bgf.wsgi`.

These instructions haven't been tested on the host yet. Follow them on a test subdomain first, well before the convention.

## 1. Confirm with the host before buying

Get these confirmed for the specific plan, in writing if possible:

- **PostgreSQL 14 or newer**, managed from cPanel. The app was built and tested on PostgreSQL 16. MySQL won't work.
- **cPanel's "Setup Python App"** (Phusion Passenger), offering **Python 3.12**.
- **SSH access** to the cPanel account.
- **Cron jobs**.
- **Free migration** of the WordPress site and all 15 mailboxes from DreamHost.
- **Daily backups that include PostgreSQL databases**, and how a restore is requested.
- **Outbound email limit** per hour. It should be able to send a notice to every seller in one go, or be raised to allow that.
- **AutoSSL** (free HTTPS certificates) for subdomains.

If "Setup Python App" isn't available, ask whether they'll run gunicorn behind Apache for you. If they won't, choose a different host.

## 2. Move WordPress and email from DreamHost

1. Ask the host to migrate the WordPress site and the mailboxes. Mail is copied over IMAP.
2. The day before the switch, lower the domain's DNS TTL at DreamHost to 5 minutes.
3. Switch the domain's nameservers to the new host. The domain registration can stay at DreamHost: only the nameservers change.
4. Once mail arrives at the new host, have the host run a final mailbox sync. That catches anything that reached DreamHost during the switch.
5. Check the website, sending and receiving mail on every account, and cPanel's **Email Deliverability** page, which should show valid SPF and DKIM.
6. Keep the DreamHost plan for two weeks before canceling, in case something was missed.

Switch in the off-season, not the month before the convention.

## 3. Set up the consignment app

Use names like these, or choose your own:

| Thing | Example |
| --- | --- |
| Subdomain | `consign.yourdomain.org` |
| App folder | `~/bgf-consignment`, outside `public_html` |
| Database and user | `cpuser_bgfconsign` and `cpuser_bgf`. cPanel adds the account name as a prefix. |

`cpuser` stands for the cPanel account name throughout.

1. **Subdomain:** create it in cPanel under **Domains**. AutoSSL issues its certificate within a few minutes.
2. **Database:** under **PostgreSQL Databases**, create the database and a user with a long random password. Then add the user to the database with all privileges.
3. **Code:** over SSH, run `git clone <repository URL> ~/bgf-consignment`.
4. **Python app:** under **Setup Python App**, create an app with:
   - **Python version:** 3.12
   - **Application root:** `bgf-consignment`
   - **Application URL:** the subdomain
   - **Startup file:** `passenger_wsgi.py`
   - **Entry point:** `application`

   If cPanel overwrites `passenger_wsgi.py`, restore it with `git checkout passenger_wsgi.py`.
5. **Settings:** create `~/bgf-consignment/.env` and make it readable only by the account with `chmod 600 .env`. The web app, SSH commands and cron jobs all read this one file.

   ```sh
   DJANGO_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(50))">
   DATABASE_URL=postgres://cpuser_bgf:<password>@localhost:5432/cpuser_bgfconsign
   DJANGO_ALLOWED_HOSTS=consign.yourdomain.org
   DJANGO_CSRF_TRUSTED_ORIGINS=https://consign.yourdomain.org
   SITE_URL=https://consign.yourdomain.org
   DEFAULT_FROM_EMAIL=Buckeye Game Fest Consignment <consignment@yourdomain.org>
   EMAIL_HOST=mail.yourdomain.org
   EMAIL_HOST_USER=consignment@yourdomain.org
   EMAIL_HOST_PASSWORD=<that mailbox's password>
   ```

   Leave out `DJANGO_DEBUG`. HTTPS redirects and secure cookies turn on automatically. If the database password contains symbols, percent-encode them in `DATABASE_URL`: for example, `@` becomes `%40`. Optional settings, such as branding and the Google Form keys, are listed in [.env.example](../.env.example).
6. **Mailbox:** create `consignment@yourdomain.org` in cPanel. The app sends set-password links and notices from it.
7. **Install and prepare.** The Python App page shows the exact `source` command for the virtual environment.

   ```sh
   source ~/virtualenv/bgf-consignment/3.12/bin/activate
   cd ~/bgf-consignment
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py collectstatic --noinput
   python manage.py check --deploy
   python manage.py create_first_admin --name "Your Name" --email you@yourdomain.org
   mkdir -p tmp && touch tmp/restart.txt
   ```

   `create_first_admin` asks for the password. Touching `tmp/restart.txt` restarts the app, and so does the **Restart** button on the Python App page.
8. **Check:** open the subdomain, sign in, and send yourself a set-password email.

## 4. Scheduled jobs

Add these under **Cron Jobs**, after creating `~/logs` and `~/backups`. In cron, `%` must be written as `\%`.

**Nightly database copy.** This is in addition to the host's backups, and keeps one copy per weekday:

```sh
30 3 * * * pg_dump -h localhost -U cpuser_bgf -Fc cpuser_bgfconsign > ~/backups/bgfconsign-$(date +\%a).dump
```

`pg_dump` reads the password from `~/.pgpass`. Create that file with `chmod 600`, containing:

```
localhost:5432:cpuser_bgfconsign:cpuser_bgf:<password>
```

**Google Form sync,** only if the form is connected:

```sh
*/10 * * * * cd ~/bgf-consignment && ~/virtualenv/bgf-consignment/3.12/bin/python manage.py sync_forms >> ~/logs/bgf-sync.log 2>&1
```

Test a restore of one dump into a scratch database before the first convention.

## 5. Updating the app

```sh
source ~/virtualenv/bgf-consignment/3.12/bin/activate
cd ~/bgf-consignment
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
touch tmp/restart.txt
```

Don't update during the convention unless something is broken.

A developer who changes dependencies regenerates `requirements.txt` with `uv export --no-dev --no-hashes --format requirements-txt -o requirements.txt` and commits it.

## 6. Adding another Django app

Each app gets its own subdomain, folder, Python App, PostgreSQL database and user, and `.env`, set up with the same steps. Up to five small apps fit on an entry-level managed VPS. Watch the server's memory in cPanel, and ask the host before adding the fifth.

## Troubleshooting

- **The site shows an error page:** the Python App page in cPanel shows where Passenger writes the app's error log. The app logs to standard error.
- **"Bad Request (400)":** the subdomain is missing from `DJANGO_ALLOWED_HOSTS`.
- **A form fails with "CSRF verification failed":** `DJANGO_CSRF_TRUSTED_ORIGINS` is missing the `https://` address.
- **Emails don't arrive:** check `EMAIL_*` in `.env`, the mailbox's sent folder and the Email Deliverability page.
