"""A stand-in billing provider that runs inside the app, for local use and tests.

It keeps its own records (the fake_* tables), shows a test checkout page, and reports every
change as a Stripe-format event, signed with the webhook secret and handed to `deliver`: the same
function the HTTP webhook endpoint calls, signature check included. No card number is stored or
logged; the test card only decides how the fake behaves.

Test cards (as with Stripe's test mode):
    4242 4242 4242 4242   pays now and at every renewal
    4000 0000 0000 0341   pays now, declines at renewal
    4000 0000 0000 0002   declined at checkout
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ..security import new_token
from ..store import Store
from ..webhooks import sign

PERIOD = 30 * 86400
MAX_FAILED_RENEWALS = 2  # then the provider gives up and ends the subscription

CARDS = {
    "4242424242424242": "ok",
    "4000000000000341": "declines_on_renewal",
    "4000000000000002": "declined",
}

Deliver = Callable[[bytes, str], tuple[int, str]]


class CheckoutError(ValueError):
    pass


def _id(prefix: str) -> str:
    return f"{prefix}_{new_token(12).replace('-', '').replace('_', '')[:16]}"


class FakeProvider:
    def __init__(self, store: Store, now: Callable[[], int], secret: str, deliver: Deliver) -> None:
        self.store, self.now, self.secret, self.deliver = store, now, secret, deliver

    # --- the four calls the app makes -----------------------------------------------------

    def start_checkout(self, workspace_id: int, email: str, plan: str) -> str:
        checkout_id = _id("cs_test")
        self.store.conn.execute(
            "INSERT INTO fake_checkouts (id, workspace_id, email, plan, created_at) VALUES (?, ?, ?, ?, ?)",
            (checkout_id, workspace_id, email, plan, self.now()),
        )
        return f"/fake-checkout/{checkout_id}"

    def change_plan(self, subscription_id: str, plan: str) -> None:
        self._update(subscription_id, plan=plan)
        self._emit("customer.subscription.updated", self._sub_object(subscription_id))

    def cancel(self, subscription_id: str) -> None:
        self._update(subscription_id, cancel_at_period_end=1)
        self._emit("customer.subscription.updated", self._sub_object(subscription_id))

    def resume(self, subscription_id: str) -> None:
        self._update(subscription_id, cancel_at_period_end=0)
        self._emit("customer.subscription.updated", self._sub_object(subscription_id))

    # --- the hosted checkout page ---------------------------------------------------------

    def checkout(self, checkout_id: str):
        return self.store._one("SELECT * FROM fake_checkouts WHERE id = ?", checkout_id)

    def complete_checkout(self, checkout_id: str, card_number: str) -> None:
        checkout = self.checkout(checkout_id)
        if checkout is None or checkout["completed_at"] is not None:
            raise CheckoutError("This checkout link has expired.")
        card = CARDS.get("".join(ch for ch in card_number if ch.isdigit()))
        if card is None:
            raise CheckoutError("Use one of the test card numbers shown on this page.")
        if card == "declined":
            raise CheckoutError("Your card was declined.")

        now = self.now()
        customer_id, subscription_id = _id("cus"), _id("sub")
        self.store.conn.execute("UPDATE fake_checkouts SET completed_at = ? WHERE id = ?", (now, checkout_id))
        self.store.conn.execute(
            "INSERT INTO fake_subscriptions (id, customer_id, workspace_id, plan, status, current_period_end, card) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (subscription_id, customer_id, checkout["workspace_id"], checkout["plan"], now + PERIOD, card),
        )
        self._emit(
            "checkout.session.completed",
            {
                "id": checkout_id,
                "object": "checkout.session",
                "client_reference_id": str(checkout["workspace_id"]),
                "customer": customer_id,
                "subscription": subscription_id,
                "mode": "subscription",
                "payment_status": "paid",
            },
        )
        self._emit("customer.subscription.created", self._sub_object(subscription_id))
        self._emit("invoice.paid", self._invoice(subscription_id, now + PERIOD))

    # --- dev panel: things that happen with time ------------------------------------------

    def renew(self, subscription_id: str, succeed: bool | None = None) -> str:
        """Move the clock to the end of the period and run the renewal. Returns what happened."""
        sub = self._row(subscription_id)
        if sub is None or sub["status"] == "canceled":
            return "There's no live subscription to renew."
        offset = int(self.store.setting("clock_offset", "0"))
        gap = sub["current_period_end"] - self.now()
        if gap > 0:
            self.store.set_setting("clock_offset", str(offset + gap + 60))

        if sub["cancel_at_period_end"]:
            self._update(subscription_id, status="canceled")
            self._emit("customer.subscription.deleted", self._sub_object(subscription_id))
            return "The period ended and the subscription was cancelled, as requested."

        pays = (sub["card"] == "ok") if succeed is None else succeed
        if pays:
            period_end = max(sub["current_period_end"], self.now()) + PERIOD
            self._update(subscription_id, status="active", current_period_end=period_end, failed_attempts=0)
            self._emit("invoice.paid", self._invoice(subscription_id, period_end))
            self._emit("customer.subscription.updated", self._sub_object(subscription_id))
            return "Renewal payment succeeded."

        attempts = sub["failed_attempts"] + 1
        if attempts >= MAX_FAILED_RENEWALS:
            self._update(subscription_id, status="canceled", failed_attempts=attempts)
            self._emit("invoice.payment_failed", self._invoice(subscription_id, sub["current_period_end"]))
            self._emit("customer.subscription.deleted", self._sub_object(subscription_id))
            return f"Renewal payment failed again ({attempts} attempts), so the subscription was ended."
        self._update(subscription_id, status="past_due", failed_attempts=attempts)
        self._emit("invoice.payment_failed", self._invoice(subscription_id, sub["current_period_end"]))
        self._emit("customer.subscription.updated", self._sub_object(subscription_id))
        return "Renewal payment failed. The subscription is past due; the next attempt is the next renewal."

    def replay_last(self) -> tuple[int, str] | None:
        """Send the most recent event again, freshly signed, the way a provider retries."""
        row = self.store._one("SELECT payload FROM fake_sent_events ORDER BY created_at DESC, rowid DESC LIMIT 1")
        if row is None:
            return None
        raw = row["payload"].encode()
        return self.deliver(raw, sign(raw, self.secret, self.now()))

    # --- internals ------------------------------------------------------------------------

    def _row(self, subscription_id: str):
        return self.store._one("SELECT * FROM fake_subscriptions WHERE id = ?", subscription_id)

    def _update(self, subscription_id: str, **fields) -> None:
        if self._row(subscription_id) is None:
            raise CheckoutError("No such subscription.")
        sets = ", ".join(f"{k} = ?" for k in fields)
        sql = f"UPDATE fake_subscriptions SET {sets} WHERE id = ?"  # keys are fixed names from this module
        self.store.conn.execute(sql, (*fields.values(), subscription_id))

    def _sub_object(self, subscription_id: str) -> dict:
        sub = self._row(subscription_id)
        return {
            "id": sub["id"],
            "object": "subscription",
            "customer": sub["customer_id"],
            "status": sub["status"],
            "current_period_end": sub["current_period_end"],
            "cancel_at_period_end": bool(sub["cancel_at_period_end"]),
            "metadata": {"workspace_id": str(sub["workspace_id"])},
            "items": {"object": "list", "data": [{"price": {"lookup_key": sub["plan"]}}]},
        }

    def _invoice(self, subscription_id: str, period_end: int) -> dict:
        sub = self._row(subscription_id)
        return {
            "id": _id("in"),
            "object": "invoice",
            "customer": sub["customer_id"],
            "subscription": subscription_id,
            "period_end": period_end,
        }

    def _emit(self, kind: str, obj: dict) -> tuple[int, str]:
        now = self.now()
        event = {"id": _id("evt"), "object": "event", "type": kind, "created": now, "data": {"object": obj}}
        raw = json.dumps(event, separators=(",", ":")).encode()
        self.store.conn.execute(
            "INSERT INTO fake_sent_events (id, payload, created_at) VALUES (?, ?, ?)", (event["id"], raw.decode(), now)
        )
        return self.deliver(raw, sign(raw, self.secret, now))
