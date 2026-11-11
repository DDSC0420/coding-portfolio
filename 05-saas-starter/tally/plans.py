"""Plans and what they allow. Pure: every check is a function of the plan and a count.

The server checks these on every request that needs them; the page only shows the result.
"""

from __future__ import annotations

from dataclasses import dataclass

# Statuses that keep paid features on. past_due is a grace period while the payment is retried.
ACCESS_STATUSES = frozenset({"active", "trialing", "past_due"})


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price: int  # GBP per month
    max_projects: int | None  # None: unlimited
    seats: int
    features: frozenset[str]
    blurb: str


PLANS = {
    "free": Plan("free", "Free", 0, 3, 1, frozenset(), "Try it out"),
    "pro": Plan("pro", "Pro", 12, None, 1, frozenset({"export"}), "For one person, no limits"),
    "team": Plan("team", "Team", 39, None, 5, frozenset({"export", "team"}), "Up to 5 people, one bill"),
}
PAID = ("pro", "team")


def effective_plan(plan_key: str, status: str) -> Plan:
    """The plan whose limits apply right now: paid only while the subscription grants access."""
    if plan_key in PAID and status in ACCESS_STATUSES:
        return PLANS[plan_key]
    return PLANS["free"]


@dataclass(frozen=True)
class Check:
    allowed: bool
    reason: str = ""
    upgrade_to: str | None = None


def can_create_project(plan: Plan, current: int) -> Check:
    if plan.max_projects is None or current < plan.max_projects:
        return Check(True)
    return Check(False, f"The {plan.name} plan includes {plan.max_projects} projects.", "pro")


def can_use(plan: Plan, feature: str) -> Check:
    if feature in plan.features:
        return Check(True)
    needed = next((PLANS[k] for k in PAID if feature in PLANS[k].features), None)
    name = {"export": "CSV export", "team": "Inviting people"}.get(feature, feature)
    if needed is None:
        return Check(False, f"{name} isn't available.")
    return Check(False, f"{name} is on the {needed.name} plan.", needed.key)


def can_add_member(plan: Plan, members_and_invites: int) -> Check:
    if "team" not in plan.features:
        return can_use(plan, "team")
    if members_and_invites < plan.seats:
        return Check(True)
    return Check(False, f"All {plan.seats} seats are taken (members and pending invites).")
