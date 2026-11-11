"""The pure rules: passwords, tokens, CSRF, rate limits, plans, webhook signatures, subscription states."""

import json

import pytest

from tally import plans, security, webhooks
from tally.subscriptions import Subscription, apply_event

# --- passwords and tokens ----------------------------------------------------------------


def test_password_hash_round_trip_and_salting():
    a, b = security.hash_password("correct horse battery"), security.hash_password("correct horse battery")
    assert a != b and a.startswith("scrypt$16384$8$1$")
    assert security.verify_password("correct horse battery", a)
    assert not security.verify_password("correct horse batterY", a)


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$aa$bb", "scrypt$16384$8$1$!!$??"])
def test_verify_rejects_malformed_hashes(stored):
    assert security.verify_password("anything", stored) is False


@pytest.mark.parametrize(
    ("password", "email", "problem"),
    [
        ("short", "", "at least 10"),
        ("x" * 257, "", "at most 256"),
        ("password123", "", "too easy"),
        ("aaaaaaaaaaaa", "", "too easy"),
        ("kyran-rocks-2026", "kyran@example.com", "email address"),
        ("tangerine-orbit-71", "kyran@example.com", None),
    ],
)
def test_password_rules(password, email, problem):
    got = security.password_problem(password, email)
    assert (got is None) if problem is None else (problem in got)


def test_tokens_are_random_and_stored_as_digests():
    t1, t2 = security.new_token(), security.new_token()
    assert t1 != t2 and len(t1) >= 40
    assert security.token_digest(t1) == security.token_digest(t1) != t1


def test_signed_values():
    signed = security.sign_value("abc", "key")
    assert security.unsign_value(signed, "key") == "abc"
    assert security.unsign_value(signed, "other-key") is None
    assert security.unsign_value(signed.replace("abc", "abd"), "key") is None
    assert security.unsign_value(None, "key") is None


def test_csrf_double_submit():
    token = security.new_csrf("key")
    assert security.csrf_ok(token, token, "key")
    assert not security.csrf_ok(token, None, "key")
    assert not security.csrf_ok(token, security.new_csrf("key"), "key")
    forged = "attacker.value"
    assert not security.csrf_ok(forged, forged, "key")  # matching but unsigned


def test_rate_limiter_window():
    now = [1000.0]
    limiter = security.RateLimiter(limit=3, window=60, clock=lambda: now[0])
    for _ in range(3):
        assert not limiter.blocked("k")
        limiter.hit("k")
    assert limiter.blocked("k") and limiter.retry_after("k") == 61
    assert not limiter.blocked("other")
    now[0] += 60.5
    assert not limiter.blocked("k")
    limiter.hit("k")
    limiter.reset("k")
    assert not limiter.blocked("k")


# --- plans ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan", "status", "effective"),
    [
        ("pro", "active", "pro"),
        ("team", "trialing", "team"),
        ("pro", "past_due", "pro"),  # grace period while the payment is retried
        ("pro", "canceled", "free"),
        ("pro", "incomplete", "free"),
        ("pro", "unpaid", "free"),
        ("free", "none", "free"),
        ("enterprise", "active", "free"),
    ],
)
def test_effective_plan(plan, status, effective):
    assert plans.effective_plan(plan, status).key == effective


def test_project_limit():
    free, pro = plans.PLANS["free"], plans.PLANS["pro"]
    assert plans.can_create_project(free, 2).allowed
    blocked = plans.can_create_project(free, 3)
    assert not blocked.allowed and blocked.upgrade_to == "pro" and "3 projects" in blocked.reason
    assert plans.can_create_project(pro, 10_000).allowed


def test_features():
    assert not plans.can_use(plans.PLANS["free"], "export").allowed
    assert plans.can_use(plans.PLANS["free"], "export").upgrade_to == "pro"
    assert plans.can_use(plans.PLANS["pro"], "export").allowed
    team_check = plans.can_use(plans.PLANS["pro"], "team")
    assert not team_check.allowed and team_check.upgrade_to == "team"


def test_seats():
    team = plans.PLANS["team"]
    assert plans.can_add_member(team, 4).allowed
    full = plans.can_add_member(team, 5)
    assert not full.allowed and "5 seats" in full.reason
    assert plans.can_add_member(plans.PLANS["pro"], 1).upgrade_to == "team"


# --- webhook signatures ------------------------------------------------------------------

BODY = b'{"id":"evt_1","type":"invoice.paid","created":1000,"data":{"object":{}}}'


def test_signature_good():
    webhooks.verify(BODY, webhooks.sign(BODY, "whsec_test", 1000), "whsec_test", now=1100)


def test_signature_matches_stripes_scheme():
    # HMAC-SHA256 over "<t>.<body>", hex, as Stripe documents.
    import hashlib
    import hmac

    expected = hmac.new(b"whsec_test", b"1000." + BODY, hashlib.sha256).hexdigest()
    assert webhooks.sign(BODY, "whsec_test", 1000) == f"t=1000,v1={expected}"


@pytest.mark.parametrize(
    ("header", "secret", "now", "message"),
    [
        (None, "whsec_test", 1000, "missing"),
        ("t=1000", "whsec_test", 1000, "no v1"),
        ("v1=abc", "whsec_test", 1000, "no timestamp"),
        ("t=soon,v1=abc", "whsec_test", 1000, "isn't a number"),
        ("SIGNED", "whsec_wrong", 1000, "doesn't match"),
        ("SIGNED", "whsec_test", 1000 + 301, "tolerance"),
        ("SIGNED", "whsec_test", 1000 - 301, "tolerance"),
    ],
)
def test_signature_failures(header, secret, now, message):
    if header == "SIGNED":
        header = webhooks.sign(BODY, "whsec_test", 1000)
    with pytest.raises(webhooks.SignatureError, match=message):
        webhooks.verify(BODY, header, secret, now=now)


def test_signature_covers_the_exact_bytes():
    header = webhooks.sign(BODY, "whsec_test", 1000)
    tampered = BODY.replace(b"invoice.paid", b"invoice.PAID")
    with pytest.raises(webhooks.SignatureError, match="doesn't match"):
        webhooks.verify(tampered, header, "whsec_test", now=1000)
    reformatted = json.dumps(json.loads(BODY)).encode()  # same JSON, different bytes
    with pytest.raises(webhooks.SignatureError):
        webhooks.verify(reformatted, header, "whsec_test", now=1000)


def test_any_of_several_v1_signatures_may_match():
    good = webhooks.compute(BODY, "whsec_new", 1000)
    webhooks.verify(BODY, f"t=1000,v1=deadbeef,v1={good}", "whsec_new", now=1000)


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b'{"type":"x","created":1,"data":{"object":{}}}', b'{"id":"e","type":"x","data":{}}']
)
def test_parse_event_rejects_malformed(body):
    with pytest.raises(webhooks.EventError):
        webhooks.parse_event(body)


# --- subscription state machine ----------------------------------------------------------


def event(kind, created, **obj):
    return {"id": f"evt_{kind}_{created}", "type": kind, "created": created, "data": {"object": obj}}


def sub_obj(status="active", plan="pro", period_end=2000, cancel=False, sid="sub_1"):
    return {
        "id": sid,
        "customer": "cus_1",
        "status": status,
        "current_period_end": period_end,
        "cancel_at_period_end": cancel,
        "items": {"data": [{"price": {"lookup_key": plan}}]},
    }


def run(*events, start=None):
    sub, notes = start or Subscription(), []
    for e in events:
        out = apply_event(sub, e)
        sub, notes = out.sub, [*notes, out.note]
    return sub, notes


def test_checkout_then_subscription_created():
    sub, _ = run(
        event("checkout.session.completed", 100, customer="cus_1", subscription="sub_1"),
        event("customer.subscription.created", 100, **sub_obj()),
    )
    assert (sub.plan, sub.status, sub.subscription_id, sub.current_period_end) == ("pro", "active", "sub_1", 2000)


def test_failed_payment_then_recovery():
    sub, _ = run(
        event("customer.subscription.created", 100, **sub_obj()),
        event("invoice.payment_failed", 200, subscription="sub_1"),
    )
    assert sub.status == "past_due" and plans.effective_plan(sub.plan, sub.status).key == "pro"
    sub, _ = run(event("invoice.paid", 300, subscription="sub_1", period_end=4000), start=sub)
    assert sub.status == "active" and sub.current_period_end == 4000


def test_cancel_at_period_end_resume_and_end():
    sub, _ = run(
        event("customer.subscription.created", 100, **sub_obj()),
        event("customer.subscription.updated", 200, **sub_obj(cancel=True)),
    )
    assert sub.cancel_at_period_end and sub.status == "active"  # still paid up until the period ends
    sub, _ = run(event("customer.subscription.updated", 300, **sub_obj(cancel=False)), start=sub)
    assert not sub.cancel_at_period_end
    sub, _ = run(event("customer.subscription.deleted", 400, **sub_obj(status="canceled")), start=sub)
    assert (sub.plan, sub.status) == ("free", "canceled")


def test_plan_change():
    sub, _ = run(
        event("customer.subscription.created", 100, **sub_obj(plan="pro")),
        event("customer.subscription.updated", 200, **sub_obj(plan="team")),
    )
    assert sub.plan == "team"


def test_out_of_order_event_is_ignored():
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj()),
        event("customer.subscription.updated", 300, **sub_obj(cancel=True)),
        event("customer.subscription.updated", 200, **sub_obj(cancel=False)),  # delivered late
    )
    assert sub.cancel_at_period_end and notes[-1] == "ignored: older than the last event applied"


def test_events_for_another_subscription_are_ignored():
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj(sid="sub_1")),
        event("customer.subscription.deleted", 200, **sub_obj(sid="sub_OTHER")),
        event("invoice.payment_failed", 300, subscription="sub_OTHER"),
    )
    assert sub.status == "active"
    assert notes[1:] == ["ignored: a different subscription", "ignored: invoice for a different subscription"]


def test_unknown_types_and_prices_are_ignored():
    sub, notes = run(
        event("customer.created", 100, id="cus_1"),
        event("customer.subscription.created", 100, **sub_obj(plan="mystery")),
    )
    assert sub == Subscription() and notes == ["ignored: customer.created isn't used", "ignored: unknown price"]


def test_payment_failure_after_cancellation_does_nothing():
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj()),
        event("customer.subscription.deleted", 200, **sub_obj(status="canceled")),
        event("invoice.payment_failed", 300, subscription="sub_1"),
    )
    assert sub.status == "canceled" and notes[-1] == "ignored: payment failed while canceled"


def test_resubscribe_after_cancel_with_a_new_subscription():
    sub, _ = run(
        event("customer.subscription.created", 100, **sub_obj(sid="sub_1")),
        event("customer.subscription.deleted", 200, **sub_obj(sid="sub_1", status="canceled")),
        event("checkout.session.completed", 300, customer="cus_1", subscription="sub_2"),
        event("customer.subscription.created", 300, **sub_obj(sid="sub_2", plan="team")),
    )
    assert (sub.subscription_id, sub.plan, sub.status) == ("sub_2", "team", "active")


def test_second_checkout_while_subscribed_is_ignored():
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj(sid="sub_1")),
        event("checkout.session.completed", 200, customer="cus_1", subscription="sub_2"),
    )
    assert sub.subscription_id == "sub_1" and notes[-1] == "ignored: already subscribed"
