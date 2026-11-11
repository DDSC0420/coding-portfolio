"""The subscription state machine. Pure: (current state, verified event) -> new state.

The billing provider is the source of truth; this keeps a local copy in step with it from
webhooks. Rules that matter:

- Events can arrive late or out of order. One older than the last applied is ignored.
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
    last_event_at: int = 0


@dataclass(frozen=True)
class Outcome:
    sub: Subscription
    applied: bool
    note: str


def apply_event(sub: Subscription, event: dict[str, Any]) -> Outcome:
    kind, created, obj = event["type"], event["created"], event["data"]["object"]
    if kind not in HANDLED:
        return Outcome(sub, False, f"ignored: {kind} isn't used")
    if created < sub.last_event_at:
        return Outcome(sub, False, "ignored: older than the last event applied")

    if kind == "checkout.session.completed":
        if sub.status in LIVE and sub.subscription_id and sub.subscription_id != obj.get("subscription"):
            return Outcome(sub, False, "ignored: already subscribed")
        new = replace(
            sub, customer_id=obj.get("customer"), subscription_id=obj.get("subscription"), last_event_at=created
        )
        return Outcome(new, True, "checkout completed")

    if kind.startswith("customer.subscription."):
        if sub.subscription_id and obj.get("id") != sub.subscription_id:
            return Outcome(sub, False, "ignored: a different subscription")
        if kind == "customer.subscription.deleted":
            new = replace(
                sub, plan="free", status="canceled", cancel_at_period_end=False, last_event_at=created
            )
            return Outcome(new, True, "subscription ended")
        plan = plan_of(obj)
        if plan is None:
            return Outcome(sub, False, "ignored: unknown price")
        new = replace(
            sub,
            plan=plan,
            status=str(obj.get("status")),
            customer_id=obj.get("customer") or sub.customer_id,
            subscription_id=obj.get("id"),
            current_period_end=obj.get("current_period_end"),
            cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
            last_event_at=created,
        )
        return Outcome(new, True, f"subscription {new.status}")

    # invoices
    if obj.get("subscription") != sub.subscription_id or sub.subscription_id is None:
        return Outcome(sub, False, "ignored: invoice for a different subscription")
    if kind == "invoice.payment_failed":
        if sub.status not in ("active", "trialing", "past_due"):
            return Outcome(sub, False, f"ignored: payment failed while {sub.status}")
        return Outcome(replace(sub, status="past_due", last_event_at=created), True, "payment failed")
    if sub.status in ("past_due", "incomplete", "unpaid", "active", "trialing"):
        period_end = obj.get("period_end") or sub.current_period_end
        new = replace(sub, status="active", current_period_end=period_end, last_event_at=created)
        return Outcome(new, True, "invoice paid")
    return Outcome(sub, False, f"ignored: invoice paid while {sub.status}")


def plan_of(obj: dict[str, Any]) -> str | None:
    try:
        key = obj["items"]["data"][0]["price"]["lookup_key"]
    except (KeyError, IndexError, TypeError):
        return None
    return key if key in PAID else None
