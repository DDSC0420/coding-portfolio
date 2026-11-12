"""Enrichment step: look the company up in local fixtures. No network, no paid services."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from relay.workflows.registry import StepContext

COMPANIES_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "companies" / "companies.json"


def _normalise(name: str) -> str:
    cleaned = "".join(ch for ch in name.lower() if ch.isalnum() or ch == " ")
    for suffix in (" ltd", " limited", " plc", " llp", " group"):
        cleaned = cleaned.removesuffix(suffix)
    return " ".join(cleaned.split())


@lru_cache
def _index(path: Path = COMPANIES_FILE) -> tuple[dict[str, dict], dict[str, dict]]:
    companies = json.loads(path.read_text(encoding="utf-8"))
    by_domain = {c["domain"]: c for c in companies}
    by_name = {_normalise(c["name"]): c for c in companies}
    return by_domain, by_name


def lookup(email: str, company: str) -> dict[str, Any]:
    by_domain, by_name = _index()
    domain = email.rsplit("@", 1)[-1]
    match, matched_on = by_domain.get(domain), "email_domain"
    if match is None:
        match, matched_on = by_name.get(_normalise(company)), "company_name"
    if match is None:
        return {"matched": False, "source": "fixtures"}
    return {
        "matched": True,
        "source": "fixtures",
        "matched_on": matched_on,
        "company_id": match["id"],
        "industry": match["industry"],
        "employees": match["employees"],
        "region": match["region"],
        "account_tier": match["account_tier"],
    }


def run(ctx: StepContext) -> dict[str, Any]:
    enquiry = ctx.input["enquiry"]
    return {**ctx.input, "enrichment": lookup(enquiry["email"], enquiry["company"])}
