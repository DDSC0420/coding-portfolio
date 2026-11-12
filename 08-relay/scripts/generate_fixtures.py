"""Generate the synthetic enquiry fixtures deterministically.

    uv run python scripts/generate_fixtures.py

Writes fixtures/enquiries/golden.json (100 valid enquiries, none needing
approval) and fixtures/enquiries/invalid.json (enquiries that must fail
validation). All people, companies and domains are fictional; domains use the
reserved .example TLD. Re-running produces identical files.
"""

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "enquiries"

FIRST = [
    "Priya",
    "Tom",
    "Aisha",
    "Callum",
    "Mei",
    "Oliver",
    "Sofia",
    "Kwame",
    "Hannah",
    "Ravi",
    "Elena",
    "Jack",
    "Zara",
    "Liam",
    "Nadia",
    "George",
    "Imani",
    "Owen",
    "Chloe",
    "Arjun",
]
LAST = [
    "Shah",
    "Hughes",
    "Okafor",
    "Reid",
    "Chen",
    "Patel",
    "Rossi",
    "Mensah",
    "Clarke",
    "Iyer",
    "Novak",
    "Turner",
    "Ahmed",
    "Doyle",
    "Kowalski",
    "Evans",
    "Bello",
    "Price",
    "Moreau",
    "Singh",
]
NEEDS = [
    "automate our weekly stock reconciliation between the warehouse system and our accounts",
    "replace a spreadsheet-based booking process with a small web app",
    "integrate our CRM with the support inbox so enquiries are logged automatically",
    "build a dashboard showing delivery performance by region",
    "migrate a legacy Access database to PostgreSQL",
    "set up automated invoice reminders for overdue accounts",
    "connect our e-commerce orders to the dispatch team's tracking sheet",
    "produce monthly compliance reports from our case management data",
]
SOURCES = ["web_form", "email", "referral", "event"]
UNKNOWN_COMPANIES = [
    ("Larkspur Studio", "larkspur.example"),
    ("Millbank Joinery", "millbank-joinery.example"),
]


def money(rng: random.Random, value: int) -> str:
    if rng.random() < 0.3 and value % 1000 == 0:
        return f"£{value // 1000}k"
    return f"£{value:,}"


def golden(rng: random.Random, companies: list[dict]) -> list[dict]:
    items = []
    for i in range(1, 101):
        first, last = FIRST[(i * 7) % len(FIRST)], LAST[(i * 11) % len(LAST)]
        if i % 10 == 0:
            company_name, domain = UNKNOWN_COMPANIES[(i // 10) % 2]
        else:
            c = companies[(i * 3) % len(companies)]
            company_name, domain = c["name"], c["domain"]
        # Unique email per enquiry so the CRM creates one contact each.
        email = f"{first}.{last}.{i:03d}@{domain}".lower()
        value = rng.choice([4_000, 7_500, 12_000, 18_000, 25_000, 32_000, 40_000, 45_000])
        need = NEEDS[i % len(NEEDS)]
        message = f"Hello, we'd like help to {need}. Our budget is around {money(rng, value)}."
        item = {
            "enquiry_ref": f"GOLD-{i:03d}",
            "name": f"{first} {last}",
            "source": SOURCES[i % len(SOURCES)],
            "message": message,
        }
        # Vary which fields arrive structured and which must be extracted from text.
        if i % 4 == 1:
            item["message"] += f"\n\nName: {first} {last}\nCompany: {company_name}\nEmail: {email}"
        else:
            item["company"], item["email"] = company_name, email
        items.append(item)
    return items


def invalid() -> list[dict]:
    base = "Please get in touch about automating our monthly reporting."
    return [
        {
            "enquiry_ref": "BAD-001",
            "name": "Missing Email",
            "company": "Vantage Print Works",
            "message": base,
        },
        {
            "enquiry_ref": "BAD-002",
            "name": "Broken Email",
            "email": "not-an-email",
            "company": "Kestrel Robotics",
            "message": base,
        },
        {
            "enquiry_ref": "BAD-003",
            "email": "nameless@saltmarsh.example",
            "company": "Saltmarsh Analytics",
            "message": base,
        },
        {
            "enquiry_ref": "BAD-004",
            "name": "Too Short",
            "email": "short@quayside-events.example",
            "company": "Quayside Events",
            "message": "Hi",
        },
        {
            "enquiry_ref": "BAD-005",
            "name": "No Company",
            "email": "solo@larkspur.example",
            "message": base,
        },
    ]


def main() -> None:
    rng = random.Random(20261104)
    companies = json.loads((ROOT / "fixtures/companies/companies.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("golden.json", golden(rng, companies)), ("invalid.json", invalid())):
        (OUT / name).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {name}: {len(data)} enquiries")


if __name__ == "__main__":
    main()
