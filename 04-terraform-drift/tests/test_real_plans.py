"""Plan JSON from real Terraform (examples/real, committed by the e2e job's snapshot step).

Expectations come from what e2e/run.sh did, not from plandrift's output: it edited out/app.conf
(the local provider treats a changed file as gone), deleted out/RUNBOOK.md, flipped the Consul
key feature_flag off -> on and added a key `debug`. full.json is a normal plan that also changes
name_words from 2 to 3.
"""

from plandrift.plan import parse_plan
from plandrift.render import RENDERERS
from plandrift.report import build_report

from .conftest import example


def real(name):
    doc = example(f"real/{name}.json")
    service = doc["prior_state"]["values"]["outputs"]["service"]["value"]
    return build_report(parse_plan(doc)), service


def drift_summary(report):
    return {c.address: (c.action, {a.name: (a.before, a.after) for a in c.attributes}) for c in report.drift}


EXPECTED_DRIFT = {
    "consul_key_prefix.app": ("update", {"subkeys.feature_flag": ("off", "on")}),
    "local_file.app_config": ("delete", {}),
    "local_file.runbook": ("delete", {}),
}


def test_these_are_real_terraform_plans():
    for name in ("clean", "refresh-only", "full"):
        doc = example(f"real/{name}.json")
        assert doc["format_version"].startswith("1.") and doc["terraform_version"]
        assert doc["timestamp"]


def test_clean_plan_reports_nothing():
    report, _ = real("clean")
    assert report.drift == () and report.changes == ()


def test_refresh_only_plan_reports_exactly_what_was_changed():
    report, _ = real("refresh-only")
    got = drift_summary(report)
    consul_attrs = got["consul_key_prefix.app"][1]
    before, after = consul_attrs.pop("subkeys.debug")  # added outside Terraform
    assert (repr(before), after) == ("ABSENT", "true")
    assert got == EXPECTED_DRIFT
    assert report.changes == ()  # a refresh-only plan proposes no changes


def test_full_plan_reports_drift_and_what_applying_would_do():
    report, service = real("full")
    assert set(drift_summary(report)) == set(EXPECTED_DRIFT)

    changes = {c.address: c for c in report.changes}
    assert {a: c.action for a, c in changes.items()} == {
        "consul_key_prefix.app": "replace",  # its path comes from the name, which is changing
        "local_file.app_config": "create",  # gone from state after refresh, so recreated
        "local_file.runbook": "create",
        "random_pet.service": "replace",  # name_words 2 -> 3
    }
    pet = {a.name: a for a in changes["random_pet.service"].attributes}
    assert (pet["length"].before, pet["length"].after, pet["length"].forces_replacement) == (2, 3, True)
    assert repr(pet["id"].after) == "UNKNOWN"

    consul = {a.name: a for a in changes["consul_key_prefix.app"].attributes}
    assert consul["path_prefix"].before == f"apps/{service}/"
    assert repr(consul["path_prefix"].after) == "UNKNOWN" and consul["path_prefix"].forces_replacement
    assert (consul["subkeys.feature_flag"].before, consul["subkeys.feature_flag"].after) == ("on", "off")


def test_real_plans_render_in_every_format():
    for name in ("clean", "refresh-only", "full"):
        report, _ = real(name)
        for render in RENDERERS.values():
            assert render(report, 2)
