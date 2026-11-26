"""The pure rules: passwords, tokens, CSRF, rate limits, plans, webhook signatures, subscription states."""

import json

import pytest

from tally import plans, security, webhooks
from tally.subscriptions import Subscription, apply_event, subscription_ref

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
#
# Each event is a nudge: the app fetches the subscription it names from the provider and stores
# that. `provider` below stands in for the provider's records (subscription id -> its copy now).


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


def run(*events, provider, start=None):
    """Apply events in the order given, fetching from `provider` as the webhook handler does."""
    sub, notes = start or Subscription(), []
    for e in events:
        ref = subscription_ref(e)
        out = apply_event(sub, e, provider.get(ref) if ref else None)
        sub, notes = out.sub, [*notes, out.note]
    return sub, notes


def test_checkout_then_subscription_created():
    sub, notes = run(
        event("checkout.session.completed", 100, customer="cus_1", subscription="sub_1"),
        event("customer.subscription.created", 100, **sub_obj()),
        provider={"sub_1": sub_obj()},
    )
    assert (sub.plan, sub.status, sub.subscription_id, sub.current_period_end) == ("pro", "active", "sub_1", 2000)
    assert notes == ["synced: active", "synced: active, no change"]


def test_failed_payment_then_recovery():
    provider = {"sub_1": sub_obj()}
    sub, _ = run(event("customer.subscription.created", 100, **sub_obj()), provider=provider)
    provider["sub_1"] = sub_obj(status="past_due")
    sub, _ = run(event("invoice.payment_failed", 200, subscription="sub_1"), provider=provider, start=sub)
    assert sub.status == "past_due" and plans.effective_plan(sub.plan, sub.status).key == "pro"
    provider["sub_1"] = sub_obj(period_end=4000)
    sub, _ = run(event("invoice.paid", 300, subscription="sub_1"), provider=provider, start=sub)
    assert sub.status == "active" and sub.current_period_end == 4000


def test_cancel_at_period_end_resume_and_end():
    provider = {"sub_1": sub_obj(cancel=True)}
    sub, _ = run(event("customer.subscription.updated", 200, **sub_obj(cancel=True)), provider=provider)
    assert sub.cancel_at_period_end and sub.status == "active"  # still paid up until the period ends
    provider["sub_1"] = sub_obj(cancel=False)
    sub, _ = run(event("customer.subscription.updated", 300, **sub_obj()), provider=provider, start=sub)
    assert not sub.cancel_at_period_end
    provider["sub_1"] = sub_obj(status="canceled", cancel=True)
    ended = event("customer.subscription.deleted", 400, **sub_obj(status="canceled"))
    sub, _ = run(ended, provider=provider, start=sub)
    assert (sub.plan, sub.status, sub.cancel_at_period_end) == ("free", "canceled", False)


def test_plan_change():
    changed = event("customer.subscription.updated", 200, **sub_obj(plan="team"))
    sub, _ = run(changed, provider={"sub_1": sub_obj(plan="team")})
    assert sub.plan == "team"


def test_the_events_own_copy_is_not_stored():
    # The event says active; by the time it's handled the provider says past due. The provider wins.
    stale = event("customer.subscription.updated", 100, **sub_obj())
    sub, notes = run(stale, provider={"sub_1": sub_obj(status="past_due")})
    assert sub.status == "past_due" and notes == ["synced: past_due"]


@pytest.mark.parametrize("reverse", [False, True], ids=["in order", "reversed"])
def test_events_in_the_same_second_end_in_the_providers_state_whatever_order_they_land(reverse):
    # Stripe's `created` is whole seconds, so a plan change and a cancel can carry the same time.
    # Ordering by timestamp can't tell which was last; a cancelled subscription must stay cancelled.
    provider = {"sub_1": sub_obj(status="canceled", plan="team")}
    both = [
        event("customer.subscription.updated", 500, **sub_obj(plan="team")),
        event("customer.subscription.deleted", 500, **sub_obj(status="canceled", plan="team")),
    ]
    start, _ = run(event("customer.subscription.created", 100, **sub_obj()), provider={"sub_1": sub_obj()})
    sub, _ = run(*(both[::-1] if reverse else both), provider=provider, start=start)
    assert (sub.plan, sub.status) == ("free", "canceled")


def test_a_late_event_cannot_undo_newer_state():
    provider = {"sub_1": sub_obj(cancel=True)}
    sub, notes = run(
        event("customer.subscription.updated", 300, **sub_obj(cancel=True)),
        event("customer.subscription.updated", 200, **sub_obj(cancel=False)),  # delivered late
        provider=provider,
    )
    assert sub.cancel_at_period_end and notes[-1] == "synced: active, no change"


def test_events_for_another_subscription_are_ignored():
    provider = {"sub_1": sub_obj(), "sub_OTHER": sub_obj(sid="sub_OTHER", status="past_due")}
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj(sid="sub_1")),
        event("customer.subscription.deleted", 200, **sub_obj(sid="sub_OTHER")),
        event("invoice.payment_failed", 300, subscription="sub_OTHER"),
        provider=provider,
    )
    assert sub.status == "active" and sub.subscription_id == "sub_1"
    assert notes[1:] == ["ignored: a different subscription", "ignored: a different subscription"]


def test_unknown_types_prices_and_subscriptions_are_ignored():
    sub, notes = run(
        event("customer.created", 100, id="cus_1"),
        event("customer.subscription.created", 100, **sub_obj(plan="mystery")),
        event("invoice.paid", 100, subscription=None),
        event("customer.subscription.updated", 100, **sub_obj(sid="sub_gone")),
        provider={"sub_1": sub_obj(plan="mystery")},
    )
    assert sub == Subscription()
    assert notes == [
        "ignored: customer.created isn't used",
        "ignored: unknown price",
        "ignored: the event names no subscription",
        "ignored: the provider has no such subscription",
    ]


def test_payment_failure_after_cancellation_does_nothing():
    provider = {"sub_1": sub_obj()}
    sub, _ = run(event("customer.subscription.created", 100, **sub_obj()), provider=provider)
    provider["sub_1"] = sub_obj(status="canceled")
    sub, notes = run(
        event("customer.subscription.deleted", 200, **sub_obj(status="canceled")),
        event("invoice.payment_failed", 300, subscription="sub_1"),
        provider=provider,
        start=sub,
    )
    assert sub.status == "canceled" and notes[-1] == "synced: canceled, no change"


def test_resubscribe_after_cancel_with_a_new_subscription():
    provider = {"sub_1": sub_obj(sid="sub_1", status="canceled"), "sub_2": sub_obj(sid="sub_2", plan="team")}
    start = Subscription(plan="free", status="canceled", customer_id="cus_1", subscription_id="sub_1")
    sub, notes = run(
        event("customer.subscription.created", 300, **sub_obj(sid="sub_2", plan="team")),  # before checkout, so unknown
        event("checkout.session.completed", 300, customer="cus_1", subscription="sub_2"),
        event("customer.subscription.deleted", 200, **sub_obj(sid="sub_1", status="canceled")),  # the old one, late
        provider=provider,
        start=start,
    )
    assert (sub.subscription_id, sub.plan, sub.status) == ("sub_2", "team", "active")
    assert notes == ["ignored: a different subscription", "synced: active", "ignored: a different subscription"]


def test_second_checkout_while_subscribed_is_ignored():
    provider = {"sub_1": sub_obj(sid="sub_1"), "sub_2": sub_obj(sid="sub_2", plan="team")}
    sub, notes = run(
        event("customer.subscription.created", 100, **sub_obj(sid="sub_1")),
        event("checkout.session.completed", 200, customer="cus_1", subscription="sub_2"),
        provider=provider,
    )
    assert sub.subscription_id == "sub_1" and notes[-1] == "ignored: already subscribed"
