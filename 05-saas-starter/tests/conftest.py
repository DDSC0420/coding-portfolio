import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tally import webhooks
from tally.web import Config, create_app

PASSWORD = "tangerine-orbit-71"
START = 1_800_000_000


class Clock:
    def __init__(self) -> None:
        self.t = float(START)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class Browser:
    """A TestClient with a cookie jar, plus helpers that fill in the CSRF token like a form would."""

    def __init__(self, app) -> None:
        self.app = app
        self.http = TestClient(app, follow_redirects=False)
        self.http.get("/")  # picks up the CSRF cookie

    @property
    def csrf(self) -> str:
        return self.http.cookies.get("tally_csrf")

    def get(self, path, **kw):
        return self.http.get(path, **kw)

    def post(self, path, follow=False, **data):
        data.setdefault("csrf", self.csrf)
        return self.http.post(path, data=data, follow_redirects=follow)

    def signup(self, email, password=PASSWORD, **extra):
        return self.post("/signup", email=email, password=password, **extra)

    def login(self, email, password=PASSWORD, **extra):
        return self.post("/login", email=email, password=password, **extra)

    def upgrade(self, plan="pro", card="4242 4242 4242 4242"):
        where = self.post("/app/billing/checkout", plan=plan).headers["location"]
        return self.post(where, card=card)

    def status(self) -> tuple[str, str]:
        html = self.get("/app/billing").text
        status = re.search(r'data-testid="status">([^<]+)<', html).group(1)
        plan = re.search(r'data-testid="plan-badge">([^<]+)<', html).group(1)
        return status, plan

    def outbox_links(self, path_prefix: str) -> list[str]:
        html = self.get("/dev/outbox").text
        found = re.findall(rf'https?://[^\s"<]+({re.escape(path_prefix)}[A-Za-z0-9_-]+)', html)
        return list(dict.fromkeys(found))  # each link appears as text and as a hyperlink


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "tally.db"


@pytest.fixture
def make_app(db_path, clock):
    def make(**overrides):
        return create_app(Config(db_path=db_path, base_time=clock, limiter_clock=clock, **overrides))

    return make


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def browser(app):
    return Browser(app)


@pytest.fixture
def new_browser(app):
    return lambda: Browser(app)


@pytest.fixture
def owner(browser):
    assert browser.signup("owner@example.com").status_code == 303
    return browser


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def send_event(app, clock):
    """POST a correctly signed event to the webhook endpoint."""
    client = TestClient(app)

    def send(kind, obj, event_id=None, created=None, secret=None, header=None, body=None):
        event = {"id": event_id or f"evt_{kind}_{clock()}", "type": kind, "created": int(created or clock()),
                 "data": {"object": obj}}  # fmt: skip
        raw = body if body is not None else json.dumps(event).encode()
        sig = header if header is not None else webhooks.sign(raw, secret or app.state.webhook_secret, int(clock()))
        return client.post("/webhooks/billing", content=raw, headers={"Stripe-Signature": sig})

    return send
