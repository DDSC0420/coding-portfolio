"""Plans, checkout, renewals, failed payments, cancellation, and the webhook endpoint."""

import csv
import io
import json

import pytest

from tally import webhooks

# --- gating on the free plan ---------------------------------------------------------------


def test_free_plan_limits_are_enforced_on_the_server(owner):
    for i in range(3):
        assert owner.post("/app/projects", name=f"P{i}").status_code == 303
    page = owner.get("/app").text
    assert 'data-testid="limit"' in page and "3 projects" in page
    blocked = owner.post("/app/projects", name="P4")  # the form is hidden, but post anyway
    assert blocked.status_code == 402 and "includes 3 projects" in blocked.text
    assert owner.get("/app/export.csv").status_code == 402


def test_checkout_unlocks_pro(owner):
    for i in range(3):
        owner.post("/app/projects", name=f"P{i}")
    assert owner.upgrade("pro").headers["location"] == "/app/billing?n=welcome"
    assert owner.status() == ("active", "Pro")
    assert owner.post("/app/projects", name="P4").status_code == 303
    assert owner.get("/app/export.csv").status_code == 200


def test_checkout_records_the_webhooks_it_received(owner, db):
    owner.upgrade("pro")
    events = [(r["type"], r["outcome"]) for r in db.execute("SELECT type, outcome FROM webhook_events ORDER BY rowid")]
    assert events == [
        ("checkout.session.completed", "checkout completed"),
        ("customer.subscription.created", "subscription active"),
        ("invoice.paid", "invoice paid"),
    ]


def test_declined_card_changes_nothing(owner, db):
    r = owner.upgrade("pro", card="4000 0000 0000 0002")
    assert r.status_code == 402 and "card was declined" in r.text
    assert owner.status() == ("free", "Free")
    assert db.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 0


def test_unknown_card_number_is_refused(owner):
    r = owner.upgrade("pro", card="5555 5555 5555 4444")
    assert r.status_code == 402 and "test card" in r.text


def test_checkout_link_works_once(owner):
    where = owner.post("/app/billing/checkout", plan="pro").headers["location"]
    owner.post(where, card="4242 4242 4242 4242")
    again = owner.post(where, card="4242 4242 4242 4242")
    assert again.status_code == 402 and "expired" in again.text


def test_unknown_plan_is_refused(owner):
    assert owner.post("/app/billing/checkout", plan="enterprise").status_code == 400


def test_another_workspaces_checkout_page_is_not_found(owner, new_browser):
    where = owner.post("/app/billing/checkout", plan="pro").headers["location"]
    other = new_browser()
    other.signup("other@example.com")
    assert other.get(where).status_code == 404
    assert other.post(where, card="4242 4242 4242 4242").status_code == 404


def test_renewal_succeeds_and_extends_the_period(owner, db):
    owner.upgrade("pro")
    end = db.execute("SELECT current_period_end FROM subscriptions").fetchone()[0]
    owner.post("/dev/renew", outcome="ok")
    assert owner.status() == ("active", "Pro")
    assert db.execute("SELECT current_period_end FROM subscriptions").fetchone()[0] > end


def test_failed_renewal_is_past_due_with_access_then_recovers(owner):
    owner.upgrade("pro")
    owner.post("/dev/renew", outcome="fail")
    assert owner.status() == ("past_due", "Pro")  # grace period: still Pro
    assert 'data-testid="past-due"' in owner.get("/app").text
    owner.post("/dev/renew", outcome="ok")
    assert owner.status() == ("active", "Pro")
    assert 'data-testid="past-due"' not in owner.get("/app").text


def test_two_failed_renewals_end_the_subscription(owner):
    owner.upgrade("pro")
    owner.post("/dev/renew", outcome="fail")
    owner.post("/dev/renew", outcome="fail")
    assert owner.status() == ("canceled", "Free")


def test_card_that_declines_at_renewal(owner):
    owner.upgrade("pro", card="4000 0000 0000 0341")
    assert owner.status() == ("active", "Pro")
    owner.post("/dev/renew")  # no forced outcome: the card decides
    assert owner.status() == ("past_due", "Pro")


def test_cancel_keeps_the_plan_until_period_end_and_can_be_resumed(owner):
    owner.upgrade("pro")
    assert owner.post("/app/billing/cancel").headers["location"] == "/app/billing?n=cancelled"
    page = owner.get("/app/billing").text
    assert "Resume subscription" in page and "Ends" in page
    assert owner.status() == ("active", "Pro")
    owner.post("/app/billing/resume")
    assert "Cancel subscription" in owner.get("/app/billing").text
    owner.post("/app/billing/cancel")
    owner.post("/dev/renew", outcome="ok")  # the period ends
    assert owner.status() == ("canceled", "Free")


def test_downgraded_workspace_keeps_its_projects_but_is_limited_again(owner):
    owner.upgrade("pro")
    for i in range(5):
        owner.post("/app/projects", name=f"P{i}")
    owner.post("/app/billing/cancel")
    owner.post("/dev/renew", outcome="ok")
    assert owner.get("/app").text.count("aria-label=\"Delete P") == 5
    assert owner.post("/app/projects", name="P6").status_code == 402


def test_resubscribe_after_cancellation(owner):
    owner.upgrade("pro")
    owner.post("/app/billing/cancel")
    owner.post("/dev/renew", outcome="ok")
    owner.upgrade("team")
    assert owner.status() == ("active", "Team")


def test_switching_plan_while_subscribed(owner):
    owner.upgrade("pro")
    assert owner.post("/app/billing/checkout", plan="team").headers["location"] == "/app/billing?n=plan-changed"
    assert owner.status() == ("active", "Team")


def test_cancel_without_a_subscription(owner):
    assert owner.post("/app/billing/cancel").status_code == 400
    assert owner.post("/app/billing/resume").status_code == 400


def test_replaying_a_webhook_is_ignored(owner, db):
    owner.upgrade("pro")
    before = db.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0]
    r = owner.post("/dev/replay")
    assert "duplicate" in owner.get(r.headers["location"]).text
    assert db.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == before


def test_dev_result_messages_must_be_signed(owner):
    page = owner.get("/app/billing?result=Your+account+is+suspended").text
    assert "suspended" not in page


def test_export_csv_neutralises_formulas(owner):
    owner.upgrade("pro")
    owner.post("/app/projects", name="=HYPERLINK(\"http://evil\",\"x\")")
    owner.post("/app/projects", name="Plain name")
    rows = list(csv.reader(io.StringIO(owner.get("/app/export.csv").text)))
    assert rows[0] == ["name", "created"]
    assert rows[1][0].startswith("'=") and rows[2][0] == "Plain name"


def test_project_names_are_escaped_in_pages(owner):
    owner.post("/app/projects", name="<script>alert(1)</script>")
    page = owner.get("/app").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


# --- the webhook endpoint, as an outside provider would call it ----------------------------


def sub_obj(sid="sub_ext", status="active", plan="pro", workspace="1", period_end=1_900_000_000, cancel=False):
    return {"id": sid, "object": "subscription", "customer": "cus_ext", "status": status,
            "current_period_end": period_end, "cancel_at_period_end": cancel, "metadata": {"workspace_id": workspace},
            "items": {"data": [{"price": {"lookup_key": plan}}]}}  # fmt: skip


def test_webhook_applies_a_signed_event(owner, send_event):
    r = send_event("customer.subscription.created", sub_obj())
    assert (r.status_code, r.text) == (200, "subscription active")
    assert owner.status() == ("active", "Pro")


def test_webhook_rejects_a_missing_signature(owner, send_event):
    r = send_event("customer.subscription.created", sub_obj(), header="")
    assert r.status_code == 400 and "missing" in r.text
    assert owner.status() == ("free", "Free")


def test_webhook_rejects_the_wrong_secret(owner, send_event):
    r = send_event("customer.subscription.created", sub_obj(), secret="whsec_attacker")
    assert r.status_code == 400 and "doesn't match" in r.text
    assert owner.status() == ("free", "Free")


def test_webhook_rejects_a_tampered_body(owner, send_event, app, clock):
    raw = json.dumps({"id": "evt_x", "type": "customer.subscription.created", "created": int(clock()),
                      "data": {"object": sub_obj(plan="pro")}}).encode()  # fmt: skip
    header = webhooks.sign(raw, app.state.webhook_secret, int(clock()))
    r = send_event("x", {}, body=raw.replace(b'"pro"', b'"team"'), header=header)
    assert r.status_code == 400 and owner.status() == ("free", "Free")


def test_webhook_rejects_a_replayed_old_request(owner, send_event, app, clock):
    raw = json.dumps({"id": "evt_old", "type": "customer.subscription.created", "created": int(clock()),
                      "data": {"object": sub_obj()}}).encode()  # fmt: skip
    header = webhooks.sign(raw, app.state.webhook_secret, int(clock()))
    clock.advance(600)  # an attacker resends a captured request ten minutes later
    r = send_event("x", {}, body=raw, header=header)
    assert r.status_code == 400 and "tolerance" in r.text


def test_webhook_rejects_malformed_events(owner, send_event):
    assert send_event("x", {}, body=b"[1, 2]").status_code == 400


def test_webhook_duplicate_event_id_is_applied_once(owner, send_event, db):
    assert send_event("customer.subscription.created", sub_obj(), event_id="evt_same").text == "subscription active"
    r = send_event("customer.subscription.deleted", sub_obj(status="canceled"), event_id="evt_same")
    assert (r.status_code, r.text) == (200, "duplicate: already processed, ignored")
    assert owner.status() == ("active", "Pro")
    assert db.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0] == 1


def test_webhook_out_of_order_event_is_ignored(owner, send_event, clock):
    send_event("customer.subscription.created", sub_obj(), created=clock() + 100)
    r = send_event("customer.subscription.updated", sub_obj(status="past_due"), created=clock() + 50)
    assert r.text == "ignored: older than the last event applied"
    assert owner.status() == ("active", "Pro")


def test_webhook_unknown_type_and_unknown_workspace_are_acknowledged(owner, send_event):
    r = send_event("customer.created", {"id": "cus_x", "object": "customer"})
    assert (r.status_code, r.text) == (200, "ignored: no matching workspace")
    r = send_event("customer.subscription.created", sub_obj(workspace="999"))
    assert (r.status_code, r.text) == (200, "ignored: no matching workspace")
    r = send_event("charge.refunded", {"id": "ch_1", "object": "charge", "metadata": {"workspace_id": "1"}})
    assert (r.status_code, r.text) == (200, "ignored: charge.refunded isn't used")


def test_webhook_invoice_is_matched_by_subscription_id(owner, send_event):
    send_event("customer.subscription.created", sub_obj(sid="sub_ext"))
    r = send_event("invoice.payment_failed", {"id": "in_1", "object": "invoice", "subscription": "sub_ext"})
    assert r.text == "payment failed" and owner.status() == ("past_due", "Pro")


@pytest.mark.parametrize("path", ["/dev/renew", "/dev/replay"])
def test_dev_tools_are_off_without_dev_mode(make_app, path):
    from .conftest import Browser

    b = Browser(make_app(dev=False))
    b.signup("a@example.com")
    assert b.post(path).status_code == 404
    assert b.get("/dev/outbox").status_code == 404
    assert 'data-testid="dev-panel"' not in b.get("/app/billing").text
