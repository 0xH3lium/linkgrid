"""LinkGrid: a small, self-hosted link and text shortener."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
import string
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
CODE_RE = re.compile(r"^[a-z0-9_-]{2,32}$")
ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 7
RESERVED_CODES = {
    "admin",
    "api",
    "favicon.ico",
    "healthz",
    "links",
    "robots.txt",
    "shares",
    "static",
    "texts",
}


def create_app(test_config: dict | None = None) -> Flask:
    """Create the application.

    Configuration can come from environment variables (see README.md); values
    in *test_config* take precedence.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("LINKGRID_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get(
            "LINKGRID_DB", os.path.join(app.instance_path, "linkgrid.db")
        ),
        ADMIN_USER=os.environ.get("LINKGRID_ADMIN_USER", "admin"),
        ADMIN_PASSWORD=os.environ.get("LINKGRID_ADMIN_PASSWORD") or None,
        SHORT_BASE_URL=(os.environ.get("LINKGRID_SHORT_BASE_URL") or "").rstrip("/"),
        MAX_URL_LENGTH=2048,
        MAX_TEXT_LENGTH=20000,
        MAX_CONTENT_LENGTH=512 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("LINKGRID_SECURE_COOKIES") == "1",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database = app.config["DATABASE"]
    if database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    init_db(database)
    register_routes(app)
    register_security_headers(app)
    return app


# ---------------------------------------------------------------------------
# Database helpers


def get_db() -> sqlite3.Connection:
    """Return the request-scoped database connection."""
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        g.db = connection
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(database: str) -> None:
    """Create or upgrade the schema. Safe to run repeatedly."""
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(links)").fetchall()
        }
        if "kind" not in columns:
            connection.execute(
                "ALTER TABLE links ADD COLUMN kind TEXT NOT NULL DEFAULT 'url'"
            )
        if "text_content" not in columns:
            connection.execute("ALTER TABLE links ADD COLUMN text_content TEXT")
        if "owner_id" not in columns:
            connection.execute(
                "ALTER TABLE links ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''"
            )
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Small helpers


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def generate_code() -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if code not in RESERVED_CODES:
            return code


def validate_url(value: str) -> str | None:
    """Return an error message when *value* is not a usable http(s) URL."""
    if not value:
        return "Paste a URL to shorten."
    if len(value) > current_app.config["MAX_URL_LENGTH"]:
        maximum = current_app.config["MAX_URL_LENGTH"]
        return f"That URL is too long ({maximum:,} characters maximum)."
    if any(character.isspace() for character in value):
        return "URLs cannot contain spaces."
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "That does not look like a valid URL."
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return "Use a full URL beginning with http:// or https://."
    try:
        parsed.port
    except ValueError:
        return "That URL has an invalid port."
    return None


def validate_text(value: str) -> str | None:
    """Return an error message when *value* is not usable text."""
    if not value.strip():
        return "Paste some text to share."
    maximum = current_app.config["MAX_TEXT_LENGTH"]
    if len(value) > maximum:
        return f"That text is too long ({maximum:,} characters maximum)."
    return None


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def admin_required(view):
    """Protect management pages with HTTP Basic Auth when configured."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        password = current_app.config["ADMIN_PASSWORD"]
        if not password:
            return view(*args, **kwargs)

        auth = request.authorization
        user_ok = bool(auth) and secrets.compare_digest(
            auth.username or "", current_app.config["ADMIN_USER"]
        )
        password_ok = bool(auth) and secrets.compare_digest(
            auth.password or "", password
        )
        if auth and auth.type == "basic" and user_ok and password_ok:
            return view(*args, **kwargs)

        return Response(
            "Authentication required.",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="LinkGrid", charset="UTF-8"'},
            mimetype="text/plain",
        )

    return wrapped


def short_base_url() -> str:
    return current_app.config["SHORT_BASE_URL"] or request.host_url.rstrip("/")


def current_owner() -> str:
    """Return the per-browser owner id, creating it on first use."""
    owner = session.get("owner_id")
    if not owner:
        owner = secrets.token_urlsafe(16)
        session["owner_id"] = owner
    return owner


def insert_share(
    code: str,
    *,
    kind: str,
    target_url: str = "",
    text_content: str | None = None,
    owner_id: str = "",
) -> bool:
    """Insert a link or text share. Return False when the code exists."""
    # target_url is non-null for compatibility with databases from the first
    # release; text shares use an empty string there.
    try:
        get_db().execute(
            "INSERT INTO links "
            "(code, kind, target_url, text_content, created_at, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (code, kind, target_url, text_content, utc_now(), owner_id),
        )
        get_db().commit()
        return True
    except sqlite3.IntegrityError:
        get_db().rollback()
        return False


# ---------------------------------------------------------------------------
# Routes


def register_routes(app: Flask) -> None:
    @app.before_request
    def require_csrf_token():
        if request.method != "POST":
            return None
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token", "")
        if (
            not expected
            or not supplied
            or not secrets.compare_digest(expected, supplied)
        ):
            abort(400, description="Invalid form token. Reload the page and try again.")
        return None

    @app.context_processor
    def template_globals() -> dict:
        csrf_token = session.get("_csrf_token")
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)
            session["_csrf_token"] = csrf_token
        return {"short_base": short_base_url(), "csrf_token": csrf_token}

    @app.template_filter("friendly_date")
    def friendly_date(value: str | None) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"

    @app.template_filter("excerpt")
    def excerpt(value: str | None, limit: int = 110) -> str:
        flattened = " ".join((value or "").split())
        if len(flattened) <= limit:
            return flattened
        return flattened[: limit - 1].rstrip() + "…"

    def render_dashboard(status: int = 200, form: dict | None = None):
        form = form or {}
        mode = form.get("kind") or request.args.get("mode", "url")
        if mode not in {"url", "text"}:
            mode = "url"
        shares = get_db().execute(
            "SELECT id, code, kind, target_url, text_content, created_at, "
            "click_count, last_clicked_at "
            "FROM links WHERE owner_id = ? ORDER BY created_at DESC, id DESC",
            (current_owner(),),
        ).fetchall()
        return (
            render_template(
                "index.html",
                shares=shares,
                created=request.args.get("created"),
                mode=mode,
                form=form,
            ),
            status,
        )

    def create_share(kind: str):
        custom_code = (request.form.get("code") or "").strip().lower()
        form = {"kind": kind, "code": custom_code}
        target_url = ""
        text_content = None

        if kind == "text":
            text_content = normalize_text(request.form.get("text") or "")
            form["text"] = text_content
            error = validate_text(text_content)
        else:
            target_url = (request.form.get("url") or "").strip()
            form["url"] = target_url
            error = validate_url(target_url)

        if error:
            flash(error, "error")
            return render_dashboard(400, form)

        def store(code: str) -> bool:
            return insert_share(
                code,
                kind=kind,
                target_url=target_url,
                text_content=text_content,
                owner_id=current_owner(),
            )

        if custom_code:
            if not CODE_RE.fullmatch(custom_code):
                flash(
                    "Custom endings need 2–32 letters, numbers, hyphens, or underscores.",
                    "error",
                )
                return render_dashboard(400, form)
            if custom_code in RESERVED_CODES:
                flash("That custom ending is reserved. Try another one.", "error")
                return render_dashboard(400, form)
            if not store(custom_code):
                flash("That custom ending is already taken.", "error")
                return render_dashboard(400, form)
            code = custom_code
        else:
            for _attempt in range(8):
                code = generate_code()
                if store(code):
                    break
            else:
                flash("Could not generate a unique code. Please try again.", "error")
                return render_dashboard(400, form)

        message = "Text link created." if kind == "text" else "Short link created."
        flash(message, "success")
        return redirect(url_for("index", mode=kind, created=code), code=303)

    @app.get("/")
    @admin_required
    def index():
        return render_dashboard()

    @app.post("/links")
    @admin_required
    def create_link():
        return create_share("url")

    @app.post("/texts")
    @admin_required
    def create_text():
        return create_share("text")

    @app.post("/shares/<code>/delete")
    @admin_required
    def delete_share(code: str):
        share = get_db().execute(
            "SELECT kind FROM links WHERE code = ? AND owner_id = ?",
            (code, current_owner()),
        ).fetchone()
        if share is None:
            flash("That share does not exist.", "error")
            return redirect(url_for("index"), code=303)
        mode = share["kind"]
        get_db().execute(
            "DELETE FROM links WHERE code = ? AND owner_id = ?",
            (code, current_owner()),
        )
        get_db().commit()
        flash(f"Deleted /{code}.", "success")
        return redirect(url_for("index", mode=mode), code=303)

    @app.get("/<code>")
    def follow_code(code: str):
        share = get_db().execute(
            "SELECT id, code, kind, target_url, text_content, created_at, "
            "click_count, last_clicked_at "
            "FROM links WHERE code = ?",
            (code,),
        ).fetchone()
        if share is None:
            abort(404)

        get_db().execute(
            "UPDATE links SET click_count = click_count + 1, "
            "last_clicked_at = ? WHERE id = ?",
            (utc_now(), share["id"]),
        )
        get_db().commit()

        if share["kind"] == "text":
            viewed_share = dict(share)
            viewed_share["click_count"] += 1
            return render_template("text.html", share=viewed_share)

        response = redirect(share["target_url"], code=302)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz():
        try:
            get_db().execute("SELECT 1").fetchone()
        except sqlite3.Error:
            current_app.logger.exception("Database health check failed")
            return jsonify(status="error"), 503
        return jsonify(status="ok")

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def add_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        if response.mimetype == "text/html":
            response.headers.setdefault("Cache-Control", "no-store")
        return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    create_app().run(
        host="0.0.0.0",
        port=port,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
