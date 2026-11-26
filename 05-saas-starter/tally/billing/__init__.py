"""Billing providers. The app talks to a provider through five calls:

    start_checkout(workspace_id, email, plan) -> URL to send the customer to
    change_plan(subscription_id, plan)
    cancel(subscription_id)        # at period end
    resume(subscription_id)
    retrieve_subscription(subscription_id) -> the provider's copy now, or None if there's no such one

The first four start changes; the app learns their results from signed webhooks, the way it
would with a real one. A webhook only tells the app to look: it then calls
retrieve_subscription and stores what that returns (Stripe: GET /v1/subscriptions/:id), so the
stored state is right whatever order the events arrive in.

`fake.FakeProvider` is the only implementation: a stand-in that behaves like a hosted checkout
and sends Stripe-format events. A real provider would implement the same five calls.
"""


class ProviderError(RuntimeError):
    """The provider couldn't be reached or failed. Webhooks answer 503 so the provider retries."""
