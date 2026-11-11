"""Billing providers. The app talks to a provider through four calls, and learns results only
from signed webhooks, the way it would with a real one:

    start_checkout(workspace_id, email, plan) -> URL to send the customer to
    change_plan(subscription_id, plan)
    cancel(subscription_id)        # at period end
    resume(subscription_id)

`fake.FakeProvider` is the only implementation: a stand-in that behaves like a hosted checkout
and sends Stripe-format events. A real provider would implement the same four calls.
"""
