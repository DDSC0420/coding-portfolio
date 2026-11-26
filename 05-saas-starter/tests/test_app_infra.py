"""Headers, migrations, and state that must survive a restart."""

import sqlite3

from fastapi.testclient import TestClient

from tally.store import MIGRATIONS, connect, migrate

from .conftest import Browser


def test_security_headers(owner):
    r = owner.get("/app")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "form-action 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_pages_have_no_inline_scripts_or_styles(owner):
    # The CSP forbids them, so the pages must not depend on them.
    owner.upgrade("pro")
    for path in ("/", "/app", "/app/team", "/app/billing", "/dev/outbox"):
        html = owner.get(path).text
        assert "<script" not in html and " style=" not in html, path


def test_migrations_are_numbered_and_idempotent(tmp_path):
    conn = connect(tmp_path / "m.db")
    assert migrate(conn) == 2
    assert migrate(conn) == 2
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "sessions", "workspaces", "subscriptions", "webhook_events", "fake_subscriptions"} <= tables
    conn.close()


def test_a_database_from_before_migration_2_upgrades_in_place(tmp_path):
    conn = connect(tmp_path / "old.db")
    first = sorted(MIGRATIONS.glob("001_*.sql"))[0].read_text(encoding="utf-8")
    conn.executescript(f"BEGIN;\n{first}\nPRAGMA user_version = 1;\nCOMMIT;")
    conn.execute("INSERT INTO users (id, email, password_hash, created_at) VALUES (1, 'a@example.com', 'x', 0)")
    conn.execute("INSERT INTO workspaces (id, name, owner_id, created_at) VALUES (1, 'A', 1, 0)")
    conn.execute("INSERT INTO subscriptions (workspace_id, plan, status, subscription_id, last_event_at) "
                 "VALUES (1, 'pro', 'active', 'sub_1', 1800000000)")  # fmt: skip
    assert migrate(conn) == 2
    row = conn.execute("SELECT * FROM subscriptions").fetchone()
    assert (row["plan"], row["status"], row["subscription_id"]) == ("pro", "active", "sub_1")
    assert "last_event_at" not in row.keys()
    conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    conn = connect(tmp_path / "f.db")
    migrate(conn)
    try:
        conn.execute("INSERT INTO projects (workspace_id, name, created_at) VALUES (999, 'x', 0)")
        raise AssertionError("expected a foreign key error")
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_secrets_and_sessions_survive_a_restart(make_app, browser):
    browser.signup("a@example.com")
    cookies = dict(browser.http.cookies)
    restarted = make_app()
    assert restarted.state.webhook_secret == browser.app.state.webhook_secret
    again = TestClient(restarted, follow_redirects=False, cookies=cookies)
    assert again.get("/app").status_code == 200


def test_unknown_pages_render_a_friendly_404(browser):
    r = browser.get("/no-such-page")
    assert r.status_code == 404 and "Page not found." in r.text


def test_healthz(app):
    assert TestClient(app).get("/healthz").json() == {"ok": True}


def test_new_browser_without_cookies_gets_redirected_not_errors(app):
    b = Browser(app)
    assert b.get("/app/billing").status_code == 303
