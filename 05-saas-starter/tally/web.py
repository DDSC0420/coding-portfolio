"""The web app: pages, forms, the webhook endpoint and the dev panel.

Rules live elsewhere (plans.py, subscriptions.py, security.py, webhooks.py); this file wires
them to HTTP. Every POST form carries a CSRF token; every gated action is checked here, on the
server, not just hidden in the page.
"""


import csv
import io
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import plans, security
from .billing.fake import CARDS, CheckoutError, FakeProvider
from .store import Store, connect, migrate
from .subscriptions import LIVE, Subscription, apply_event
from .webhooks import EventError, SignatureError, parse_event, verify

HERE = Path(__file__).resolve().parent
SESSION_COOKIE, CSRF_COOKIE = "tally_session", "tally_csrf"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
NOTICES = {
    "welcome": "Payment received. Your plan is live.",
    "signed-out": "You're signed out.",
    "reset-sent": "If there's an account for that address, we've sent a reset link. (Locally: see the dev outbox.)",
    "password-changed": "Password changed. Sign in with the new one; other sessions were signed out.",
    "cancelled": "Cancelled. You keep your plan until the end of the period.",
    "resumed": "Your subscription will renew as normal.",
    "plan-changed": "Plan changed.",
    "invited": "Invite sent. (Locally: see the dev outbox.)",
    "joined": "You've joined the workspace.",
    "owner-only": "Only the workspace owner can do that.",
}


@dataclass
class Config:
    db_path: str | Path
    dev: bool = True
    base_time: Callable[[], float] = time.time
    secret_key: str | None = None
    webhook_secret: str | None = None
    login_limit: tuple[int, int] = (5, 900)  # attempts, seconds (per email)
    ip_limit: tuple[int, int] = (30, 900)  # per client address, across emails
    limiter_clock: Callable[[], float] = time.monotonic


@dataclass
class Ctx:
    """Who is asking, in which workspace, on which plan."""

    user_id: int
    email: str
    token_hash: str
    workspace: sqlite3.Row
    role: str
    workspaces: list[sqlite3.Row]
    sub: Subscription
    plan: plans.Plan
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


class _Redirect(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


def create_app(config: Config) -> FastAPI:
    app = FastAPI(title="Tally", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["date"] = _date
    templates.env.filters["datetime"] = lambda ts: f"{_date(ts)} {datetime.fromtimestamp(ts, UTC):%H:%M:%S}"
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    conn = connect(config.db_path)
    migrate(conn)
    boot = Store(conn)
    secret_key = config.secret_key or boot.setting("secret_key") or security.new_token()
    webhook_secret = config.webhook_secret or boot.setting("webhook_secret") or f"whsec_{security.new_token(24)}"
    boot.set_setting("secret_key", secret_key)
    boot.set_setting("webhook_secret", webhook_secret)
    conn.close()

    login_limiter = security.RateLimiter(*config.login_limit, clock=config.limiter_clock)
    ip_limiter = security.RateLimiter(*config.ip_limit, clock=config.limiter_clock)
    app.state.config, app.state.webhook_secret = config, webhook_secret

    # --- per-request plumbing -------------------------------------------------------------

    @app.middleware("http")
    async def per_request(request: Request, call_next):
        store = Store(connect(config.db_path))
        request.state.store = store
        cookie = request.cookies.get(CSRF_COOKIE)
        fresh_csrf = None if security.unsign_value(cookie, secret_key) else security.new_csrf(secret_key)
        request.state.csrf = fresh_csrf or cookie
        try:
            response = await call_next(request)
        finally:
            store.close()
        if fresh_csrf:
            response.set_cookie(CSRF_COOKIE, fresh_csrf, httponly=True, samesite="strict", secure=_https(request))
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith(("/app", "/fake-checkout", "/dev", "/reset", "/invite")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(_Redirect)
    async def redirect_handler(request: Request, exc: _Redirect):
        return _redirect(exc.url)

    @app.exception_handler(StarletteHTTPException)
    async def error_page(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/webhooks/"):
            return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
        detail = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else "Page not found."
        return page(request, "error.html", exc.status_code, detail=detail, upgrade=exc.status_code == 402)

    def now(store: Store) -> int:
        """Wall-clock time: sessions, reset links, invites."""
        return int(config.base_time())

    def billing_now(store: Store) -> int:
        """Billing time, which the dev panel can move forward to simulate renewals."""
        return int(config.base_time()) + int(store.setting("clock_offset", "0"))

    def store_of(request: Request) -> Store:
        return request.state.store

    def check_csrf(request: Request, csrf: Annotated[str, Form()] = "") -> None:
        if not security.csrf_ok(request.cookies.get(CSRF_COOKIE), csrf, secret_key):
            raise HTTPException(403, "The form expired or came from somewhere else. Go back, reload and try again.")

    def current(request: Request) -> Ctx | None:
        store = store_of(request)
        session = store.session(request.cookies.get(SESSION_COOKIE), now(store))
        if session is None:
            return None
        workspaces = store.workspaces_for(session["user_id"])
        role = store.role(session["workspace_id"], session["user_id"])
        workspace_id = session["workspace_id"]
        if role is None:  # removed from that workspace: fall back to their own
            workspace_id, role = workspaces[0]["id"], workspaces[0]["role"]
            store.set_session_workspace(session["token_hash"], workspace_id)
        sub = store.subscription(workspace_id)
        return Ctx(
            user_id=session["user_id"],
            email=session["email"],
            token_hash=session["token_hash"],
            workspace=store.workspace(workspace_id),
            role=role,
            workspaces=workspaces,
            sub=sub,
            plan=plans.effective_plan(sub.plan, sub.status),
        )

    def require_user(request: Request) -> Ctx:
        ctx = current(request)
        if ctx is None:
            raise _Redirect(f"/login?next={request.url.path}")
        return ctx

    def require_owner(ctx: Annotated[Ctx, Depends(require_user)]) -> Ctx:
        if not ctx.is_owner:
            raise _Redirect("/app?n=owner-only")
        return ctx

    def page(request: Request, name: str, status: int = 200, **values) -> HTMLResponse:
        values.setdefault("ctx", current(request))
        notice = NOTICES.get(request.query_params.get("n", ""))
        return templates.TemplateResponse(
            request,
            name,
            {"csrf": request.state.csrf, "dev": config.dev, "notice": notice, "plans": plans.PLANS, **values},
            status_code=status,
        )

    def provider(store: Store) -> FakeProvider:
        return FakeProvider(store, lambda: billing_now(store), webhook_secret,
                            lambda raw, sig: handle_webhook(store, raw, sig))

    def sign_in(store: Store, user_id: int, workspace_id: int, request: Request, to: str) -> RedirectResponse:
        token = store.create_session(user_id, workspace_id, now(store))
        response = _redirect(to)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax", secure=_https(request), max_age=14 * 86400
        )
        return response

    # --- webhooks -------------------------------------------------------------------------

    def handle_webhook(store: Store, raw: bytes, signature: str | None) -> tuple[int, str]:
        try:
            verify(raw, signature, webhook_secret, now=billing_now(store))
            event = parse_event(raw)
        except (SignatureError, EventError) as exc:
            return 400, f"rejected: {exc}"
        obj = event["data"]["object"]
        workspace_id = _workspace_ref(obj) or store.workspace_for_billing(
            obj.get("subscription") if obj.get("object") != "subscription" else obj.get("id"), obj.get("customer")
        )
        try:
            with store.transaction():
                if store.event_seen(event["id"]):
                    return 200, "duplicate: already processed, ignored"
                if workspace_id is None or store.workspace(workspace_id) is None:
                    note = "ignored: no matching workspace"
                else:
                    outcome = apply_event(store.subscription(workspace_id), event)
                    if outcome.applied:
                        store.save_subscription(workspace_id, outcome.sub)
                    note = outcome.note
                store.record_event(event["id"], event["type"], workspace_id, billing_now(store), note, raw.decode())
        except sqlite3.IntegrityError:
            return 200, "duplicate: already processed, ignored"
        return 200, note

    @app.post("/webhooks/billing")
    async def webhook(request: Request) -> PlainTextResponse:
        status, message = handle_webhook(
            store_of(request), await request.body(), request.headers.get("stripe-signature")
        )
        return PlainTextResponse(message, status_code=status)

    # --- public pages ---------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(request, "pricing.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request, invite: str = ""):
        invitation = store_of(request).invite(invite, now(store_of(request))) if invite else None
        return page(request, "signup.html", invitation=invitation, invite=invite, email="")

    @app.post("/signup", dependencies=[Depends(check_csrf)])
    def signup(
        request: Request,
        email: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
        invite: Annotated[str, Form()] = "",
    ):
        store = store_of(request)
        email = email.strip().lower()
        invitation = store.invite(invite, now(store)) if invite else None
        ip = _client(request)
        if ip_limiter.blocked(ip):
            return page(request, "signup.html", 429, error="Too many attempts. Try again later.", email=email,
                        invitation=invitation, invite=invite)  # fmt: skip
        ip_limiter.hit(ip)
        error = None
        if not EMAIL.match(email):
            error = "Enter a valid email address."
        elif problem := security.password_problem(password, email):
            error = problem
        elif store.user_by_email(email):
            error = "There's already an account for that email. Sign in instead."
        elif invitation and invitation["email"] != email:
            error = f"This invite is for {invitation['email']}."
        if error:
            return page(request, "signup.html", 400, error=error, email=email, invitation=invitation, invite=invite)

        with store.transaction():
            user_id = store.create_user(email, security.hash_password(password), now(store))
            workspace_id = store.create_workspace(f"{email.split('@')[0]}'s workspace", user_id, now(store))
            if invitation:
                workspace_id = store.accept_invite(invite, user_id, now(store)) or workspace_id
        return sign_in(store, user_id, workspace_id, request, "/app?n=joined" if invitation else "/app")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/app"):
        return page(request, "login.html", next=_safe_next(next), email="")

    @app.post("/login", dependencies=[Depends(check_csrf)])
    def login(
        request: Request,
        email: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
        next: Annotated[str, Form()] = "/app",
    ):
        store = store_of(request)
        email, next = email.strip().lower(), _safe_next(next)
        ip = _client(request)
        if login_limiter.blocked(email) or ip_limiter.blocked(ip):
            wait = max(login_limiter.retry_after(email), ip_limiter.retry_after(ip))
            response = page(request, "login.html", 429, email=email, next=next,
                            error=f"Too many attempts. Try again in {max(1, wait // 60)} min.")  # fmt: skip
            response.headers["Retry-After"] = str(wait)
            return response
        user = store.user_by_email(email)
        if user is None:
            security.dummy_verify(password)  # same time as a real check
        if user is None or not security.verify_password(password, user["password_hash"]):
            login_limiter.hit(email)
            ip_limiter.hit(ip)
            return page(request, "login.html", 400, error="Email or password is wrong.", email=email, next=next)
        login_limiter.reset(email)
        workspaces = store.workspaces_for(user["id"])
        return sign_in(store, user["id"], workspaces[0]["id"], request, next)

    @app.post("/logout", dependencies=[Depends(check_csrf)])
    def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            store_of(request).delete_session(token)
        response = _redirect("/login?n=signed-out")
        response.delete_cookie(SESSION_COOKIE)
        return response

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_form(request: Request):
        return page(request, "forgot.html")

    @app.post("/forgot", dependencies=[Depends(check_csrf)])
    def forgot(request: Request, email: Annotated[str, Form()] = ""):
        store = store_of(request)
        email = email.strip().lower()
        key = f"reset:{email}"
        user = store.user_by_email(email)
        if user and not login_limiter.blocked(key):  # same answer either way: no account enumeration
            login_limiter.hit(key)
            token = store.create_reset(user["id"], now(store))
            link = f"{_origin(request)}/reset/{token}"
            store.send_email(email, "Reset your Tally password",
                             f"Someone asked to reset your password. If it was you, open:\n{link}\n\n"
                             "The link works once, for 30 minutes.", now(store))  # fmt: skip
        return _redirect("/login?n=reset-sent")

    @app.get("/reset/{token}", response_class=HTMLResponse)
    def reset_form(request: Request, token: str):
        valid = store_of(request).reset_valid(token, now(store_of(request)))
        return page(request, "reset.html", 200 if valid else 410, token=token, valid=valid)

    @app.post("/reset/{token}", dependencies=[Depends(check_csrf)])
    def reset(request: Request, token: str, password: Annotated[str, Form()] = ""):
        store = store_of(request)
        if not store.reset_valid(token, now(store)):
            return page(request, "reset.html", 410, token=token, valid=False)
        problem = security.password_problem(password)
        if problem:
            return page(request, "reset.html", 400, token=token, valid=True, error=problem)
        with store.transaction():
            user_id = store.use_reset(token, now(store))
            if user_id is None:
                return page(request, "reset.html", 410, token=token, valid=False)
            store.set_password(user_id, security.hash_password(password))
            store.delete_user_sessions(user_id)
        response = _redirect("/login?n=password-changed")
        response.delete_cookie(SESSION_COOKIE)
        return response

    # --- the product ----------------------------------------------------------------------

    @app.get("/app", response_class=HTMLResponse)
    def dashboard(request: Request, ctx: Annotated[Ctx, Depends(require_user)]):
        store = store_of(request)
        projects = store.projects(ctx.workspace["id"])
        return page(request, "dashboard.html", ctx=ctx, projects=projects,
                    can_create=plans.can_create_project(ctx.plan, len(projects)),
                    can_export=plans.can_use(ctx.plan, "export"))  # fmt: skip

    @app.post("/app/projects", dependencies=[Depends(check_csrf)])
    def create_project(request: Request, ctx: Annotated[Ctx, Depends(require_user)],
                       name: Annotated[str, Form()] = ""):  # fmt: skip
        store = store_of(request)
        name = " ".join(name.split())[:80]
        with store.transaction():
            check = plans.can_create_project(ctx.plan, store.count_projects(ctx.workspace["id"]))
            if not check.allowed:
                raise HTTPException(402, check.reason)
            if name:
                store.create_project(ctx.workspace["id"], name, now(store))
        return _redirect("/app")

    @app.post("/app/projects/{project_id}/delete", dependencies=[Depends(check_csrf)])
    def delete_project(request: Request, project_id: int, ctx: Annotated[Ctx, Depends(require_user)]):
        store_of(request).delete_project(ctx.workspace["id"], project_id)
        return _redirect("/app")

    @app.get("/app/export.csv")
    def export(request: Request, ctx: Annotated[Ctx, Depends(require_user)]):
        check = plans.can_use(ctx.plan, "export")
        if not check.allowed:
            raise HTTPException(402, check.reason)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["name", "created"])
        for p in store_of(request).projects(ctx.workspace["id"]):
            writer.writerow([_csv_safe(p["name"]), datetime.fromtimestamp(p["created_at"], UTC).isoformat()])
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="projects.csv"'})  # fmt: skip

    @app.post("/app/switch/{workspace_id}", dependencies=[Depends(check_csrf)])
    def switch(request: Request, workspace_id: int, ctx: Annotated[Ctx, Depends(require_user)]):
        store = store_of(request)
        if store.role(workspace_id, ctx.user_id) is None:
            raise HTTPException(404)
        store.set_session_workspace(ctx.token_hash, workspace_id)
        return _redirect("/app")

    # --- team -----------------------------------------------------------------------------

    @app.get("/app/team", response_class=HTMLResponse)
    def team(request: Request, ctx: Annotated[Ctx, Depends(require_user)]):
        store = store_of(request)
        ws = ctx.workspace["id"]
        return page(request, "team.html", ctx=ctx, members=store.members(ws),
                    invites=store.pending_invites(ws, now(store)),
                    can_invite=plans.can_add_member(ctx.plan, store.seats_used(ws, now(store))))  # fmt: skip

    @app.post("/app/team/invite", dependencies=[Depends(check_csrf)])
    def invite(request: Request, ctx: Annotated[Ctx, Depends(require_owner)], email: Annotated[str, Form()] = ""):
        store = store_of(request)
        email, ws = email.strip().lower(), ctx.workspace["id"]
        if not EMAIL.match(email):
            raise HTTPException(400, "Enter a valid email address.")
        if any(m["email"] == email for m in store.members(ws)):
            raise HTTPException(400, f"{email} is already a member.")
        with store.transaction():
            already_invited = any(i["email"] == email for i in store.pending_invites(ws, now(store)))
            check = plans.can_add_member(ctx.plan, store.seats_used(ws, now(store)) - already_invited)
            if not check.allowed:
                raise HTTPException(402, check.reason)
            token = store.create_invite(ws, email, now(store))
            store.send_email(email, f"Join {ctx.workspace['name']} on Tally",
                             f"{ctx.email} invited you. Open:\n{_origin(request)}/invite/{token}\n\n"
                             "The link works for 7 days.", now(store))  # fmt: skip
        return _redirect("/app/team?n=invited")

    @app.post("/app/team/revoke", dependencies=[Depends(check_csrf)])
    def revoke(request: Request, ctx: Annotated[Ctx, Depends(require_owner)], email: Annotated[str, Form()] = ""):
        store_of(request).revoke_invite(ctx.workspace["id"], email.strip().lower())
        return _redirect("/app/team")

    @app.post("/app/team/remove/{user_id}", dependencies=[Depends(check_csrf)])
    def remove(request: Request, user_id: int, ctx: Annotated[Ctx, Depends(require_owner)]):
        store_of(request).remove_member(ctx.workspace["id"], user_id)
        return _redirect("/app/team")

    @app.get("/invite/{token}", response_class=HTMLResponse)
    def invite_page(request: Request, token: str):
        store = store_of(request)
        invitation = store.invite(token, now(store))
        return page(request, "invite.html", 200 if invitation else 410, invitation=invitation, token=token)

    @app.post("/invite/{token}/accept", dependencies=[Depends(check_csrf)])
    def accept(request: Request, token: str, ctx: Annotated[Ctx, Depends(require_user)]):
        store = store_of(request)
        invitation = store.invite(token, now(store))
        if invitation is None or invitation["email"] != ctx.email:
            raise HTTPException(403, "This invite isn't for this account, or it has expired.")
        workspace_id = store.accept_invite(token, ctx.user_id, now(store))
        if workspace_id is None:
            raise HTTPException(410, "This invite has already been used.")
        store.set_session_workspace(ctx.token_hash, workspace_id)
        return _redirect("/app?n=joined")

    # --- billing --------------------------------------------------------------------------

    @app.get("/app/billing", response_class=HTMLResponse)
    def billing(request: Request, ctx: Annotated[Ctx, Depends(require_user)]):
        store = store_of(request)
        offset = int(store.setting("clock_offset", "0"))
        return page(request, "billing.html", ctx=ctx, live=ctx.sub.status in LIVE,
                    events=store.recent_events(ctx.workspace["id"]), clock_days=round(offset / 86400, 1),
                    result=security.unsign_value(request.query_params.get("result"), secret_key))  # fmt: skip

    @app.post("/app/billing/checkout", dependencies=[Depends(check_csrf)])
    def checkout(request: Request, ctx: Annotated[Ctx, Depends(require_owner)], plan: Annotated[str, Form()] = ""):
        if plan not in plans.PAID:
            raise HTTPException(400, "Unknown plan.")
        store = store_of(request)
        if ctx.sub.status in LIVE and ctx.sub.subscription_id:
            provider(store).change_plan(ctx.sub.subscription_id, plan)
            return _redirect("/app/billing?n=plan-changed")
        return _redirect(provider(store).start_checkout(ctx.workspace["id"], ctx.email, plan))

    @app.post("/app/billing/cancel", dependencies=[Depends(check_csrf)])
    def cancel(request: Request, ctx: Annotated[Ctx, Depends(require_owner)]):
        if not (ctx.sub.status in LIVE and ctx.sub.subscription_id):
            raise HTTPException(400, "There's no subscription to cancel.")
        provider(store_of(request)).cancel(ctx.sub.subscription_id)
        return _redirect("/app/billing?n=cancelled")

    @app.post("/app/billing/resume", dependencies=[Depends(check_csrf)])
    def resume(request: Request, ctx: Annotated[Ctx, Depends(require_owner)]):
        if not (ctx.sub.status in LIVE and ctx.sub.subscription_id):
            raise HTTPException(400, "There's no subscription to resume.")
        provider(store_of(request)).resume(ctx.sub.subscription_id)
        return _redirect("/app/billing?n=resumed")

    @app.get("/fake-checkout/{checkout_id}", response_class=HTMLResponse)
    def fake_checkout(request: Request, checkout_id: str, ctx: Annotated[Ctx, Depends(require_user)]):
        session = provider(store_of(request)).checkout(checkout_id)
        if session is None or session["workspace_id"] != ctx.workspace["id"]:
            raise HTTPException(404)
        return page(request, "checkout.html", ctx=ctx, session=session, plan=plans.PLANS[session["plan"]],
                    cards=CARDS)  # fmt: skip

    @app.post("/fake-checkout/{checkout_id}", dependencies=[Depends(check_csrf)])
    def fake_pay(request: Request, checkout_id: str, ctx: Annotated[Ctx, Depends(require_user)],
                 card: Annotated[str, Form()] = ""):  # fmt: skip
        fake = provider(store_of(request))
        session = fake.checkout(checkout_id)
        if session is None or session["workspace_id"] != ctx.workspace["id"]:
            raise HTTPException(404)
        try:
            fake.complete_checkout(checkout_id, card)
        except CheckoutError as exc:
            return page(request, "checkout.html", 402, ctx=ctx, session=session, plan=plans.PLANS[session["plan"]],
                        cards=CARDS, error=str(exc))  # fmt: skip
        return _redirect("/app/billing?n=welcome")

    # --- dev tools (only with dev=True) ---------------------------------------------------

    def result_page(message: str) -> RedirectResponse:
        # Signed, so a crafted link can't put its own words on the page.
        return _redirect(f"/app/billing?result={quote(security.sign_value(message, secret_key))}")

    def require_dev() -> None:
        if not config.dev:
            raise HTTPException(404)

    @app.post("/dev/renew", dependencies=[Depends(require_dev), Depends(check_csrf)])
    def dev_renew(request: Request, ctx: Annotated[Ctx, Depends(require_user)], outcome: Annotated[str, Form()] = ""):
        if not ctx.sub.subscription_id:
            return result_page("No subscription yet: upgrade first.")
        result = provider(store_of(request)).renew(ctx.sub.subscription_id, {"ok": True, "fail": False}.get(outcome))
        return result_page(result)

    @app.post("/dev/replay", dependencies=[Depends(require_dev), Depends(check_csrf)])
    def dev_replay(request: Request, ctx: Annotated[Ctx, Depends(require_user)]):
        got = provider(store_of(request)).replay_last()
        return result_page("Nothing to replay." if got is None else f"Replayed the last event: {got[1]}.")

    @app.get("/dev/outbox", response_class=HTMLResponse, dependencies=[Depends(require_dev)])
    def dev_outbox(request: Request):
        return page(request, "outbox.html", emails=store_of(request).outbox())

    return app


def _date(ts: int | None) -> str:
    if not ts:
        return "-"
    d = datetime.fromtimestamp(ts, UTC)
    return f"{d.day} {d:%b %Y}"


def _workspace_ref(obj: dict) -> int | None:
    ref = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("workspace_id")
    return int(ref) if isinstance(ref, str) and ref.isdigit() else None


def _safe_next(target: str) -> str:
    """Only same-site paths: no //host, no backslashes, no schemes."""
    if not target.startswith("/") or target.startswith("//") or "\\" in target or ":" in target.split("?")[0]:
        return "/app"
    return target


def _csv_safe(value: str) -> str:
    """Stop a spreadsheet treating a project name as a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _https(request: Request) -> bool:
    return request.url.scheme == "https"


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"
