"""The subscription state machine. Pure: (current state, verified event, provider's copy) -> new state.

The billing provider is the source of truth; this keeps a local copy in step with it. A webhook
event is treated as a nudge: it says *which* subscription changed, and the caller then fetches
that subscription from the provider and passes it in. The event's own copy of the subscription
is never stored, because it may already be out of date:

- Events can arrive late or out of order, and Stripe's `created` is in whole seconds, so two
  events (an update and a cancel, say) can carry the same time. Ordering by timestamp can't
  tell which came last; the provider's current copy is right whatever order events land in.
- Each event is applied at most once (the caller records event ids; see web.py).
- Events for some other subscription, and types we don't use, are ignored, not errors.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .plans import PAID

HANDLED = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }
)
LIVE = frozenset({"trialing", "active", "past_due", "incomplete", "unpaid"})


@dataclass(frozen=True)
class Subscription:
    plan: str = "free"
    status: str = "none"
    customer_id: str | None = None
    subscription_id: str | None = None
    current_period_end: int | None = None
    cancel_at_period_end: bool = False


@dataclass(frozen=True)
class Outcome:
    sub: Subscription
    applied: bool
    note: str


def subscription_ref(event: dict[str, Any]) -> str | None:
    """The id of the subscription an event is about: the one to fetch. None if there's nothing to fetch."""
    kind, obj = event["type"], event["data"]["object"]
    if kind not in HANDLED:
        return None
    ref = obj.get("id") if kind.startswith("customer.subscription.") else obj.get("subscription")
    return ref if isinstance(ref, str) and ref else None


def apply_event(sub: Subscription, event: dict[str, Any], current: dict[str, Any] | None) -> Outcome:
    """Bring `sub` in line with `current`: the provider's copy of the event's subscription, fetched
    after the event arrived (None if the provider has no such subscription)."""
    kind = event["type"]
    if kind not in HANDLED:
        return Outcome(sub, False, f"ignored: {kind} isn't used")
    ref = subscription_ref(event)
    if ref is None:
        return Outcome(sub, False, "ignored: the event names no subscription")
    if sub.subscription_id and ref != sub.subscription_id:
        if kind != "checkout.session.completed":
            return Outcome(sub, False, "ignored: a different subscription")
        if sub.status in LIVE:
            return Outcome(sub, False, "ignored: already subscribed")
    if current is None or current.get("id") != ref:
        return Outcome(sub, False, "ignored: the provider has no such subscription")

    status = str(current.get("status"))
    ended = status == "canceled"
    plan = "free" if ended else plan_of(current)
    if plan is None:
        return Outcome(sub, False, "ignored: unknown price")
    new = replace(
        sub,
        plan=plan,
        status=status,
        customer_id=current.get("customer") or sub.customer_id,
        subscription_id=ref,
        current_period_end=current.get("current_period_end"),
        cancel_at_period_end=bool(current.get("cancel_at_period_end")) and not ended,
    )
    if new == sub:
        return Outcome(sub, False, f"synced: {status}, no change")
    return Outcome(new, True, f"synced: {status}")


def plan_of(obj: dict[str, Any]) -> str | None:
    try:
        key = obj["items"]["data"][0]["price"]["lookup_key"]
    except (KeyError, IndexError, TypeError):
        return None
    return key if key in PAID else None
