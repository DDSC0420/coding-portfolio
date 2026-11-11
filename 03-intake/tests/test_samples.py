"""The invented sample CVs against their hand-labelled answers, and the command line."""

import json

import pytest

from intake import evaluate
from intake.__main__ import main

from .conftest import SAMPLES

REPORT = evaluate.run()


@pytest.mark.parametrize("name", sorted(REPORT))
def test_sample_matches_its_hand_labelled_answers(name):
    misses = [f"{c.field}: got {c.got!r}, expected {c.want!r}" for c in REPORT[name] if not c.ok]
    assert misses == []


def test_samples_are_invented_people_with_reserved_contact_details():
    for path in SAMPLES.glob("expected/*.json"):
        expected = json.loads(path.read_text())
        if expected["email"]:
            assert expected["email"].split("@")[1].split(".")[0] == "example"
        if expected["phone"] and expected["phone"].startswith("+44"):
            assert expected["phone"].startswith("+447700900")  # Ofcom's drama range
    for cv in ("sofia-marlowe.txt", "rowan-hale.txt"):
        assert "Fictional person" in (SAMPLES / cv).read_text()


def test_the_scorecard_shows_a_miss(monkeypatch):
    report = {"x.txt": [evaluate.Check("name", True, "A", "A"), evaluate.Check("phone", False, None, "+44")]}
    text = evaluate.render(report, "rule-based")
    assert "1/2 fields correct" in text and "x.txt phone: got None, expected '+44'" in text


def test_cli_extract_prints_json(capsys):
    assert main(["extract", str(SAMPLES / "sofia-marlowe.txt")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["candidate"]["name"]["value"] == "Sofia Marlowe"
    assert "text" not in out


def test_cli_extract_reports_unreadable_files(tmp_path, capsys):
    bad = tmp_path / "scan.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    assert main(["extract", str(bad)]) == 1
    assert "OCR" in capsys.readouterr().err


def test_cli_eval_passes(capsys):
    assert main(["eval"]) == 0
    assert "44/44 fields correct" in capsys.readouterr().out


def test_cli_refuses_half_configured_llm(monkeypatch, capsys):
    monkeypatch.setenv("INTAKE_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["eval"]) == 2
    assert "needs ANTHROPIC_API_KEY" in capsys.readouterr().err
