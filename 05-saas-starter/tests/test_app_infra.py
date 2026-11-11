"""Headers, migrations, and state that must survive a restart."""

import sqlite3

from fastapi.testclient import TestClient

from tally.store import connect, migrate

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
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"users", "sessions", "workspaces", "subscriptions", "webhook_events", "fake_subscriptions"} <= tables
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
