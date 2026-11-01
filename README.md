# LinkGrid

A deliberately small link and text shortener, built with Flask and SQLite.

It is designed for a handful of people: no user accounts, no background jobs,
and no services to wire up. One Python process and one database file.

## What it does

- Serves short URLs at `https://linkgrid/<code>` with a 302 redirect.
- Serves shared plain text at the same kind of short URL, on a clean reader page.
- Gives you a dashboard at `/` to create, copy, and delete links and text shares.
- Generates 7-character codes, or accepts custom endings of 2–32 characters.
- Counts clicks on links and views on text shares.
- Optionally protects the dashboard with HTTP Basic Auth.
- Includes `/healthz` for uptime checks and `/robots.txt` that keeps shares out of search engines.

Text shares are plain text (no Markdown or HTML) and can be up to 20,000
characters. That keeps the reader page and the data model simple.

## Run it locally

Requires Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:8000>. The database is created automatically at
`instance/linkgrid.db`.

On macOS/Linux, activate the environment with `source .venv/bin/activate` and
the rest is the same.

## Configure the domain

Set `LINKGRID_SHORT_BASE_URL` to the address people will use for short links.
For the `linkgrid` domain:

```powershell
$env:LINKGRID_SHORT_BASE_URL = "https://linkgrid"
```

If you leave it unset, LinkGrid uses the host from the incoming request, which
is convenient locally and behind a correctly configured reverse proxy.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LINKGRID_SHORT_BASE_URL` | request host | Public base used when displaying and copying short links. No trailing slash. |
| `LINKGRID_DB` | `instance/linkgrid.db` | SQLite database path. |
| `LINKGRID_ADMIN_PASSWORD` | unset | Enables Basic Auth on the dashboard when set. |
| `LINKGRID_ADMIN_USER` | `admin` | Basic Auth username. |
| `LINKGRID_SECRET_KEY` | random per process | Signs session cookies (flash messages and CSRF tokens). Set a stable value in production. |
| `LINKGRID_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS so cookies are marked Secure. |
| `PORT` | `8000` | Port used by `python app.py`. |
| `FLASK_DEBUG` | `0` | Set to `1` for Flask debug mode. Never use debug in production. |

Generate a secret key with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

**If LinkGrid is reachable by anyone other than you, set
`LINKGRID_ADMIN_PASSWORD`.** Without it, the dashboard is open and anyone can
create links or text shares. The password is sent on each admin request, so
serve the app over HTTPS.

## Production

[Waitress](https://docs.pylonsproject.org/projects/waitress/) is included in the
requirements. It works on Windows and Linux:

```powershell
$env:LINKGRID_ADMIN_PASSWORD = "use-a-long-random-password"
$env:LINKGRID_SECRET_KEY = "the-generated-secret"
$env:LINKGRID_SHORT_BASE_URL = "https://linkgrid"
$env:LINKGRID_SECURE_COOKIES = "1"
waitress-serve --listen=0.0.0.0:8000 --call app:create_app
```

Put it behind a reverse proxy that terminates HTTPS. For example, with Caddy:

```caddyfile
linkgrid {
    reverse_proxy 127.0.0.1:8000
}
```

If your public address has a suffix (for example `linkgrid.example`), use that
full hostname in both the Caddyfile and `LINKGRID_SHORT_BASE_URL`.

Keep the process running with your service manager of choice (systemd, NSSM,
Task Scheduler, or a container supervisor). SQLite needs a local, persistent
disk; do not put the database on a network share.

## Data and backups

Everything lives in one SQLite database in WAL mode. To take a consistent
backup while the service is running, use SQLite's backup API:

```powershell
python -c "import sqlite3; src=sqlite3.connect('instance/linkgrid.db'); dst=sqlite3.connect('linkgrid-backup.db'); src.backup(dst); dst.close(); src.close()"
```

Copying only `linkgrid.db` while the service is writing can miss recent changes
that are still in the `-wal` file.

Databases created by the original link-only version are upgraded automatically
when the app starts. Existing links keep working; the new text fields are added
in place.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Scope

This is intentionally not a multi-tenant product. It has one shared admin
credential, no teams, no custom domains per share, no editing, and no rate
limiting. Those choices keep the app small enough to understand and operate.
