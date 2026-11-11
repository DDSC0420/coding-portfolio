"""The bundled examples, checked against what each scenario did (written from the scenario, not from output).

Scenario: on a Friday someone fixed things by hand in the AWS console. They opened SSH to the
world on the web security group "temporarily", changed a bucket's Owner tag, terminated the batch
worker and scaled the database up a size. console-changes.json is the refresh-only plan that
finds that. drift-and-changes.json is a normal plan on the same estate: the same drift, plus
what applying would do (put it all back, and ship this week's code changes).
"""

import json

import pytest

from plandrift.plan import parse_plan
from plandrift.report import build_report

from .conftest import EXAMPLES, example

CONSOLE_DRIFT = {
    "aws_security_group.web": (
        "update",
        {
            "ingress[1].cidr_blocks[0]": (None, "0.0.0.0/0"),
            "ingress[1].description": (None, "temp debug"),
            "ingress[1].from_port": (None, 22),
            "ingress[1].protocol": (None, "tcp"),
            "ingress[1].self": (None, False),
            "ingress[1].to_port": (None, 22),
        },
    ),
    "aws_s3_bucket.logs": (
        "update",
        {"tags.Owner": ("platform", "data-team"), "tags_all.Owner": ("platform", "data-team")},
    ),
    "aws_instance.batch_worker": ("delete", {}),
    "aws_db_instance.main": ("update", {"instance_class": ("db.t3.medium", "db.t3.large")}),
}


def summarise(changes):
    out = {}
    for c in changes:
        attrs = {a.name: (_plain(a.before), _plain(a.after)) for a in c.attributes}
        out[c.address] = (c.action, attrs)
    return out


def _plain(value):
    return None if repr(value) in ("ABSENT", "UNKNOWN", "HIDDEN") else value


def test_console_changes_is_drift_only():
    report = build_report(parse_plan(example("console-changes.json")))
    assert summarise(report.drift) == CONSOLE_DRIFT
    assert report.changes == ()


def test_drift_and_changes():
    report = build_report(parse_plan(example("drift-and-changes.json")))
    assert summarise(report.drift) == CONSOLE_DRIFT

    changes = {c.address: c for c in report.changes}
    assert [(c.address, c.action) for c in report.changes] == [
        ("aws_security_group.web", "update"),  # closes SSH again
        ("aws_s3_bucket.logs", "update"),  # Owner back to platform
        ("aws_instance.batch_worker", "create"),  # recreated
        ("aws_db_instance.main", "update"),  # back down a size
        ("aws_secretsmanager_secret_version.api_key", "update"),  # rotated key
        ("aws_instance.web", "replace"),  # new AMI
        ("aws_cloudwatch_log_group.web", "move"),  # renamed in code with a moved block
        ("aws_s3_bucket.reports", "create"),  # new bucket
    ]  # data.aws_ami.ubuntu is a read and isn't listed

    sg = {a.name: (a.before, a.after) for a in changes["aws_security_group.web"].attributes}
    assert sg["ingress[1].from_port"][0] == 22 and repr(sg["ingress[1].from_port"][1]) == "ABSENT"

    secret = {a.name: a for a in changes["aws_secretsmanager_secret_version.api_key"].attributes}
    assert secret["secret_string"].sensitive
    assert repr(secret["version_id"].after) == "UNKNOWN"

    web = {a.name: a for a in changes["aws_instance.web"].attributes}
    assert web["ami"].forces_replacement
    assert (web["ami"].before, web["ami"].after) == ("ami-0c1a7f89451184c8b", "ami-0f9de6e2d2f067fca")
    assert repr(web["public_ip"].after) == "UNKNOWN"
    assert "a changed attribute forces replacement" in changes["aws_instance.web"].notes
    assert changes["aws_cloudwatch_log_group.web"].notes == ("from aws_cloudwatch_log_group.this",)


def test_no_drift():
    report = build_report(parse_plan(example("no-drift.json")))
    assert report.drift == () and report.changes == ()


@pytest.mark.parametrize("path", sorted(EXAMPLES.rglob("*.json")), ids=lambda p: p.relative_to(EXAMPLES).as_posix())
def test_every_example_parses(path):
    parse_plan(json.loads(path.read_text(encoding="utf-8")))
