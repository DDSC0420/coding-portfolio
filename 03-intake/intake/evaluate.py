"""Score the extractor against hand-labelled answers for the sample CVs.

Each file in ``samples/expected/`` was written by hand from the CV, not dumped from
Intake's output. The scorecard checks every field, including which fields were flagged for
review: catching a problem is part of getting the answer right.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .llm import LlmExtractor
from .pipeline import extract
from .schema import Result

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIELDS = ["name", "email", "phone", "location", "headline", "links", "skills", "experience", "education",
          "years", "review"]


@dataclass
class Check:
    field: str
    ok: bool
    got: object
    want: object


def compare(result: Result, expected: dict) -> list[Check]:
    c = result.candidate
    got = {
        "name": c.name.value, "email": c.email.value, "phone": c.phone.value,
        "location": c.location.value, "headline": c.headline.value,
        "links": sorted(i.value for i in c.links),
        "skills": sorted(i.value.casefold() for i in c.skills),
        "experience": [[r.title, r.company, r.start, r.end] for r in c.experience],
        "education": [[e.qualification, e.institution, e.year] for e in c.education],
        "years": c.years_experience.value,
        "review": sorted(item.field for item in result.review),
    }
    want = {
        **{k: expected[k] for k in ("name", "email", "phone", "location", "headline", "experience", "education")},
        "links": sorted(expected["links"]),
        "skills": sorted(s.casefold() for s in expected["skills"]),
        "years": expected["years_experience"],
        "review": sorted(expected["review"]),
    }
    checks = []
    for field in FIELDS:
        if field == "years":
            ok = (got[field] is None) == (want[field] is None) and (
                got[field] is None or abs(got[field] - want[field]) < 0.05)
        else:
            ok = got[field] == want[field]
        checks.append(Check(field, ok, got[field], want[field]))
    return checks


def run(samples: Path = SAMPLES, llm: LlmExtractor | None = None) -> dict[str, list[Check]]:
    out = {}
    for path in sorted((samples / "expected").glob("*.json")):
        expected = json.loads(path.read_text(encoding="utf-8"))
        cv = samples / expected["file"]
        result = extract(cv.read_bytes(), cv.name, llm=llm, today=date.fromisoformat(expected["as_of"]))
        out[cv.name] = compare(result, expected)
    return out


def render(report: dict[str, list[Check]], extractor: str) -> str:
    width = max(len(name) for name in report) + 2
    header = "".join(f"{f:>{max(len(f), 3) + 2}}" for f in FIELDS)
    lines = [f"Scorecard: {extractor} extractor vs hand-labelled answers", "", f"{'sample':<{width}}{header}"]
    for name, checks in report.items():
        row = "".join(f"{('ok' if c.ok else 'MISS'):>{max(len(c.field), 3) + 2}}" for c in checks)
        lines.append(f"{name:<{width}}{row}")
    total = sum(len(c) for c in report.values())
    passed = sum(c.ok for checks in report.values() for c in checks)
    lines += ["", f"{passed}/{total} fields correct across {len(report)} invented CVs"]
    for name, checks in report.items():
        for c in checks:
            if not c.ok:
                lines.append(f"  {name} {c.field}: got {c.got!r}, expected {c.want!r}")
    return "\n".join(lines)
