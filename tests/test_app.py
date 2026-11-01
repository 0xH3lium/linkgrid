import base64
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class LinkGridTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "test.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE": self.database,
                "ADMIN_PASSWORD": None,
                "SHORT_BASE_URL": "https://linkgrid",
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, client=None, headers=None):
        client = client or self.client
        page = client.get("/", headers=headers)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match, "CSRF token missing from dashboard")
        return match.group(1).decode()

    def create_link(self, url="https://example.com/a/long/path", code=""):
        return self.client.post(
            "/links",
            data={"url": url, "code": code, "_csrf_token": self.csrf_token()},
            follow_redirects=False,
        )

    def create_text(self, text="A small note worth sharing.", code=""):
        return self.client.post(
            "/texts",
            data={"text": text, "code": code, "_csrf_token": self.csrf_token()},
            follow_redirects=False,
        )

    def fetch_share(self, code):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                "SELECT * FROM links WHERE code = ?", (code,)
            ).fetchone()
        finally:
            connection.close()

    def count_shares(self):
        connection = sqlite3.connect(self.database)
        try:
            return connection.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        finally:
            connection.close()

    def test_dashboard_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Make it", response.data)
        self.assertIn(b"Shorten a URL", response.data)
        self.assertIn(b"?mode=text", response.data)
        self.assertIn(b"Your shares", response.data)
        self.assertIn(b"0 shares", response.data)
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_text_tab_renders_textarea(self):
        response = self.client.get("/?mode=text")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<textarea', response.data)
        self.assertIn(b'name="text"', response.data)
        self.assertIn(b"Share text", response.data)
        self.assertIn(b"20000", response.data)

    def test_post_without_csrf_token_is_rejected(self):
        response = self.client.post(
            "/links", data={"url": "https://example.com/no-token"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count_shares(), 0)

    def test_create_url_follow_and_count_click(self):
        response = self.create_link(code="Launch-Notes")
        self.assertEqual(response.status_code, 303)

        link = self.fetch_share("launch-notes")
        self.assertIsNotNone(link)
        self.assertEqual(link["kind"], "url")
        self.assertEqual(link["target_url"], "https://example.com/a/long/path")
        self.assertIsNone(link["text_content"])
        self.assertEqual(link["click_count"], 0)

        dashboard = self.client.get("/?created=launch-notes")
        self.assertIn(b"https://linkgrid/launch-notes", dashboard.data)
        self.assertIn(b"New", dashboard.data)

        first_click = self.client.get("/launch-notes")
        self.assertEqual(first_click.status_code, 302)
        self.assertEqual(first_click.headers["Location"], "https://example.com/a/long/path")
        self.assertEqual(first_click.headers["Cache-Control"], "no-store")

        self.client.get("/launch-notes")
        updated = self.fetch_share("launch-notes")
        self.assertEqual(updated["click_count"], 2)
        self.assertIsNotNone(updated["last_clicked_at"])

    def test_generated_code_is_short_and_lowercase(self):
        response = self.create_link()
        self.assertEqual(response.status_code, 303)

        connection = sqlite3.connect(self.database)
        try:
            code = connection.execute("SELECT code FROM links").fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(len(code), 7)
        self.assertRegex(code, r"^[a-z0-9]{7}$")

    def test_invalid_urls_are_rejected(self):
        invalid_urls = [
            "example.com/no-scheme",
            "javascript:alert(1)",
            "ftp://example.com/file",
            "https://exa mple.com",
            "https://example.com:not-a-port/path",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                response = self.create_link(url=url)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"flash-error", response.data)

        self.assertEqual(self.count_shares(), 0)

    def test_custom_code_rules_and_duplicates(self):
        self.create_link(code="promo")

        duplicate = self.create_link(url="https://example.org/other", code="promo")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn(b"already taken", duplicate.data)

        reserved = self.create_link(url="https://example.org/other", code="healthz")
        self.assertEqual(reserved.status_code, 400)
        self.assertIn(b"reserved", reserved.data)

        invalid = self.create_link(url="https://example.org/other", code="Not Valid!")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"Custom endings need", invalid.data)

    def test_delete_url_share(self):
        self.create_link(code="temporary")
        response = self.client.post(
            "/shares/temporary/delete",
            data={"_csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.fetch_share("temporary"))

        missing = self.client.get("/temporary")
        self.assertEqual(missing.status_code, 404)
        self.assertIn(b"That share isn't here.", missing.data)

    def test_destination_is_escaped(self):
        self.create_link(
            url="https://example.com/?next=<script>alert(1)</script>",
            code="safe",
        )
        page = self.client.get("/")
        self.assertNotIn(b"<script>alert(1)</script>", page.data)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page.data)

    def test_health_and_robots(self):
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json(), {"status": "ok"})

        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn(b"Disallow: /", robots.data)

    def test_create_and_view_text_share(self):
        response = self.create_text("Line one\nLine two", code="note")
        self.assertEqual(response.status_code, 303)

        share = self.fetch_share("note")
        self.assertEqual(share["kind"], "text")
        self.assertEqual(share["target_url"], "")
        self.assertEqual(share["text_content"], "Line one\nLine two")

        page = self.client.get("/note")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"A text drop.", page.data)
        self.assertIn(b"Line one\nLine two", page.data)
        self.assertIn(b"1 view", page.data)
        self.assertEqual(page.headers["Cache-Control"], "no-store")

        second_view = self.client.get("/note")
        self.assertIn(b"2 views", second_view.data)
        self.assertEqual(self.fetch_share("note")["click_count"], 2)

        dashboard = self.client.get("/?mode=text")
        self.assertIn(b"Text", dashboard.data)
        self.assertIn(b"Line one Line two", dashboard.data)
        self.assertIn(b"https://linkgrid/note", dashboard.data)

    def test_text_content_is_escaped(self):
        self.create_text('<script>alert("x")</script>\n<b>hello</b>', code="safe-text")
        page = self.client.get("/safe-text")
        self.assertNotIn(b"<script>alert", page.data)
        self.assertIn(b"&lt;script&gt;", page.data)
        self.assertNotIn(b"<b>hello</b>", page.data)
        self.assertIn(b"&lt;b&gt;hello&lt;/b&gt;", page.data)

    def test_text_validation_and_cross_type_collisions(self):
        empty = self.create_text("   \n  ", code="empty")
        self.assertEqual(empty.status_code, 400)
        self.assertIn(b"Paste some text", empty.data)

        too_long = self.create_text("x" * 20001, code="long")
        self.assertEqual(too_long.status_code, 400)
        self.assertIn(b"too long", too_long.data)

        self.create_link(code="shared-code")
        text_collision = self.create_text("Some text", code="shared-code")
        self.assertEqual(text_collision.status_code, 400)
        self.assertIn(b"already taken", text_collision.data)

        self.create_text("Text first", code="text-first")
        link_collision = self.create_link(
            url="https://example.org", code="text-first"
        )
        self.assertEqual(link_collision.status_code, 400)
        self.assertIn(b"already taken", link_collision.data)

    def test_unicode_text_near_limit_is_accepted(self):
        content = "好" * 20000
        response = self.create_text(content, code="unicode-note")
        self.assertEqual(response.status_code, 303)
        page = self.client.get("/unicode-note")
        self.assertEqual(page.status_code, 200)
        self.assertIn(content.encode("utf-8"), page.data)

    def test_text_form_keeps_content_on_error(self):
        self.create_text("Original", code="taken-text")
        response = self.create_text(
            "This long text should survive the validation error.", code="taken-text"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"already taken", response.data)
        self.assertIn(b"This long text should survive the validation error.", response.data)

    def test_delete_text_share(self):
        self.create_text("Temporary note", code="temp-note")
        response = self.client.post(
            "/shares/temp-note/delete",
            data={"_csrf_token": self.csrf_token()},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("mode=text", response.headers["Location"])
        self.assertIsNone(self.fetch_share("temp-note"))
        self.assertEqual(self.client.get("/temp-note").status_code, 404)

    def test_legacy_database_is_migrated(self):
        legacy_database = str(Path(self.temp_dir.name) / "legacy.db")
        connection = sqlite3.connect(legacy_database)
        try:
            connection.executescript(
                """
                CREATE TABLE links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    target_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    last_clicked_at TEXT
                );
                INSERT INTO links (code, target_url, created_at)
                VALUES ('legacy', 'https://example.com/legacy', '2026-01-01T00:00:00+00:00');
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE": legacy_database,
                "ADMIN_PASSWORD": None,
                "SHORT_BASE_URL": "https://linkgrid",
            }
        )
        client = migrated_app.test_client()

        old_link = client.get("/legacy")
        self.assertEqual(old_link.status_code, 302)
        self.assertEqual(old_link.headers["Location"], "https://example.com/legacy")

        token = self.csrf_token(client=client)
        created = client.post(
            "/texts",
            data={
                "text": "A migrated note",
                "code": "migrated-note",
                "_csrf_token": token,
            },
        )
        self.assertEqual(created.status_code, 303)
        self.assertEqual(client.get("/migrated-note").status_code, 200)

        connection = sqlite3.connect(legacy_database)
        connection.row_factory = sqlite3.Row
        try:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(links)")
            }
            self.assertIn("kind", columns)
            self.assertIn("text_content", columns)
            text_share = connection.execute(
                "SELECT kind, target_url, text_content FROM links WHERE code = ?",
                ("migrated-note",),
            ).fetchone()
            self.assertEqual(text_share["kind"], "text")
            self.assertEqual(text_share["target_url"], "")
            self.assertEqual(text_share["text_content"], "A migrated note")
        finally:
            connection.close()

    def test_admin_auth_when_password_is_configured(self):
        protected_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "DATABASE": self.database,
                "ADMIN_USER": "admin",
                "ADMIN_PASSWORD": "correct horse",
                "SHORT_BASE_URL": "https://linkgrid",
            }
        )
        client = protected_app.test_client()

        unauthenticated = client.get("/")
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertIn("Basic", unauthenticated.headers["WWW-Authenticate"])
        self.assertEqual(client.get("/healthz").status_code, 200)

        credentials = base64.b64encode(b"admin:correct horse").decode()
        headers = {"Authorization": f"Basic {credentials}"}
        authenticated = client.get("/", headers=headers)
        self.assertEqual(authenticated.status_code, 200)

        bad_credentials = base64.b64encode(b"admin:wrong").decode()
        denied = client.get("/", headers={"Authorization": f"Basic {bad_credentials}"})
        self.assertEqual(denied.status_code, 401)

        token = self.csrf_token(client=client, headers=headers)
        created = client.post(
            "/links",
            data={
                "url": "https://example.com",
                "code": "private",
                "_csrf_token": token,
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 303)
        self.assertEqual(client.get("/private").status_code, 302)


if __name__ == "__main__":
    unittest.main()
