"""Accounts: sign-up, sign-in, sessions, CSRF, rate limits, password resets."""

import pytest

from tally.security import token_digest

from .conftest import PASSWORD, Browser


def test_signup_creates_account_workspace_and_session(browser, db):
    r = browser.signup("Kyran@Example.com")
    assert r.status_code == 303 and r.headers["location"] == "/app"
    user = db.execute("SELECT * FROM users").fetchone()
    assert user["email"] == "kyran@example.com"
    assert user["password_hash"].startswith("scrypt$") and PASSWORD not in user["password_hash"]
    ws = db.execute("SELECT * FROM workspaces").fetchone()
    assert ws["name"] == "kyran's workspace" and ws["owner_id"] == user["id"]
    assert browser.status() == ("free", "Free")


@pytest.mark.parametrize(
    ("email", "password", "message"),
    [
        ("not-an-email", PASSWORD, "valid email"),
        ("a@example.com", "short", "at least 10"),
        ("a@example.com", "password123", "too easy"),
    ],
)
def test_signup_validation(browser, email, password, message):
    r = browser.signup(email, password)
    assert r.status_code == 400 and message in r.text


def test_duplicate_signup(browser, new_browser):
    browser.signup("a@example.com")
    r = new_browser().signup("a@example.com")
    assert r.status_code == 400 and "already an account" in r.text


def test_session_cookie_flags_and_storage(browser, db):
    r = browser.signup("a@example.com")
    cookie = r.headers["set-cookie"]
    assert "tally_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = browser.http.cookies.get("tally_session")
    stored = db.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert stored == token_digest(token) and token not in stored  # only a digest is kept


def test_login_and_logout_ends_the_session_server_side(owner, new_browser):
    other = new_browser()
    assert other.login("owner@example.com").headers["location"] == "/app"
    stolen = other.http.cookies.get("tally_session")
    assert other.post("/logout").headers["location"] == "/login?n=signed-out"
    replay = new_browser()
    replay.http.cookies.set("tally_session", stolen)
    assert replay.get("/app").headers["location"] == "/login?next=/app"


def test_wrong_password_and_unknown_email_get_the_same_answer(owner, new_browser):
    b = new_browser()
    wrong = b.login("owner@example.com", "wrong-password-1")
    unknown = b.login("nobody@example.com", "wrong-password-1")
    assert wrong.status_code == unknown.status_code == 400
    assert "Email or password is wrong." in wrong.text and "Email or password is wrong." in unknown.text


def test_login_rate_limit_per_email(owner, new_browser, clock):
    b = new_browser()
    for _ in range(5):
        assert b.login("owner@example.com", "wrong-password-1").status_code == 400
    blocked = b.login("owner@example.com")  # even the right password is refused for now
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    clock.advance(901)
    assert b.login("owner@example.com").status_code == 303


def test_login_rate_limit_per_address_across_emails(make_app):
    b = Browser(make_app(ip_limit=(3, 900)))
    for i in range(3):
        b.login(f"guess{i}@example.com", "wrong-password-1")
    assert b.login("another@example.com", "wrong-password-1").status_code == 429


@pytest.mark.parametrize(
    "target", ["//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)"]
)
def test_login_next_only_allows_local_paths(owner, new_browser, target):
    assert new_browser().login("owner@example.com", next=target).headers["location"] == "/app"


def test_login_next_local_path_is_kept(owner, new_browser):
    assert new_browser().login("owner@example.com", next="/app/team").headers["location"] == "/app/team"


def test_pages_need_a_session(browser):
    for path in ("/app", "/app/team", "/app/billing", "/app/export.csv"):
        assert browser.get(path).headers["location"] == f"/login?next={path}"


# --- CSRF ----------------------------------------------------------------------------------


def test_post_without_csrf_token_is_refused(owner):
    r = owner.http.post("/app/projects", data={"name": "x"})
    assert r.status_code == 403 and "form expired" in r.text


def test_post_with_wrong_csrf_token_is_refused(owner):
    assert owner.post("/app/projects", name="x", csrf="something-else").status_code == 403


def test_forged_cookie_and_token_pair_is_refused(owner):
    owner.http.cookies.delete("tally_csrf")
    owner.http.cookies.set("tally_csrf", "attacker.chosen")
    assert owner.post("/app/projects", name="x", csrf="attacker.chosen").status_code == 403


def test_login_form_is_csrf_protected_too(browser):
    assert browser.http.post("/login", data={"email": "a@example.com", "password": PASSWORD}).status_code == 403


def test_csrf_cookie_flags(app):
    from fastapi.testclient import TestClient

    r = TestClient(app).get("/")
    cookie = r.headers["set-cookie"]
    assert "tally_csrf=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie


# --- password reset ------------------------------------------------------------------------


def test_reset_request_does_not_reveal_whether_an_account_exists(owner, new_browser, db):
    b = new_browser()
    known = b.post("/forgot", email="owner@example.com")
    unknown = b.post("/forgot", email="nobody@example.com")
    assert known.headers["location"] == unknown.headers["location"] == "/login?n=reset-sent"
    assert [r["to_email"] for r in db.execute("SELECT to_email FROM outbox")] == ["owner@example.com"]


def test_reset_flow_single_use_and_signs_out_other_sessions(owner, new_browser):
    other_device = new_browser()
    other_device.login("owner@example.com")
    b = new_browser()
    b.post("/forgot", email="owner@example.com")
    (link,) = b.outbox_links("/reset/")
    assert b.get(link).status_code == 200
    assert b.post(link, password="short").status_code == 400
    assert b.post(link, password="new-river-stone-88").headers["location"] == "/login?n=password-changed"
    assert b.get(link).status_code == 410  # used
    assert b.post(link, password="another-one-99x").status_code == 410
    assert other_device.get("/app").status_code == 303  # signed out everywhere
    assert owner.get("/app").status_code == 303
    assert b.login("owner@example.com").status_code == 400  # old password gone
    assert b.login("owner@example.com", "new-river-stone-88").status_code == 303


def test_reset_link_expires_after_30_minutes(owner, new_browser, clock):
    b = new_browser()
    b.post("/forgot", email="owner@example.com")
    (link,) = b.outbox_links("/reset/")
    clock.advance(31 * 60)
    assert b.get(link).status_code == 410
    assert b.post(link, password="new-river-stone-88").status_code == 410


def test_reset_token_is_stored_as_a_digest(owner, new_browser, db):
    b = new_browser()
    b.post("/forgot", email="owner@example.com")
    (link,) = b.outbox_links("/reset/")
    token = link.rsplit("/", 1)[1]
    assert db.execute("SELECT token_hash FROM password_resets").fetchone()[0] == token_digest(token)


def test_reset_requests_are_rate_limited(owner, new_browser, db):
    b = new_browser()
    for _ in range(8):
        b.post("/forgot", email="owner@example.com")
    assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 5
