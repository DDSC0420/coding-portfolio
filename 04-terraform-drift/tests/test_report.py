import pytest

from plandrift.plan import parse_plan
from plandrift.report import build_report, exit_code, parse_rule

from .conftest import entry, example, plan_doc


@pytest.mark.parametrize(
    ("rule", "address", "expected"),
    [
        ("aws_instance.web", "aws_instance.web", True),
        ("aws_instance.web", "aws_instance.web2", False),
        ("aws_instance.*", "aws_instance.web", True),
        ("aws_instance.*", "aws_s3_bucket.web", False),
        ("*", "module.a.aws_instance.web[0]", True),
        ("aws_instance.web[0]", "aws_instance.web[0]", True),  # brackets are literal, not a character class
        ("aws_instance.web[0]", "aws_instance.web0", False),
        ("aws_instance.web[?]", "aws_instance.web[3]", True),
        ('aws_s3_bucket.b["a:b"]', 'aws_s3_bucket.b["a:b"]', True),  # colon inside a key isn't the separator
        ("module.*.aws_instance.web", "module.app.aws_instance.web", True),
    ],
)
def test_address_rules(rule, address, expected):
    assert parse_rule(rule).matches_resource(address) is expected


@pytest.mark.parametrize(
    ("rule", "attribute", "expected"),
    [
        ("*:tags_all", "tags_all.Owner", True),  # a rule on a parent covers its children
        ("*:tags_all", "tags.Owner", False),
        ("*:tags.Last*", "tags.LastScanned", True),
        ("*:tags.Last*", "tags.Owner", False),
        ("*:ingress[1]", "ingress[1].from_port", True),
        ("*:ingress[1]", "ingress[10].from_port", False),
        ('*:tags["kubernetes.io/role"]', 'tags["kubernetes.io/role"]', True),
        ("*:tags", 'tags["kubernetes.io/role"]', True),
    ],
)
def test_attribute_rules(rule, attribute, expected):
    assert parse_rule(rule).matches_attribute("aws_s3_bucket.logs", attribute) is expected


def test_attribute_rule_does_not_ignore_the_whole_resource():
    assert not parse_rule("aws_s3_bucket.logs:tags").matches_resource("aws_s3_bucket.logs")


@pytest.mark.parametrize("bad", ["", ":tags", "aws_s3_bucket.logs:"])
def test_bad_rules(bad):
    with pytest.raises(ValueError, match="ADDRESS"):
        parse_rule(bad)


def test_ignoring_a_resource():
    plan = parse_plan(example("console-changes.json"))
    report = build_report(plan, [parse_rule("aws_instance.batch_worker")])
    assert [c.address for c in report.drift] == ["aws_security_group.web", "aws_s3_bucket.logs", "aws_db_instance.main"]
    assert report.ignored == ("aws_instance.batch_worker",)


def test_ignoring_some_attributes_keeps_the_rest():
    plan = parse_plan(example("console-changes.json"))
    report = build_report(plan, [parse_rule("*:tags_all")])
    logs = next(c for c in report.drift if c.address == "aws_s3_bucket.logs")
    assert [a.name for a in logs.attributes] == ["tags.Owner"]
    assert "aws_s3_bucket.logs:tags_all.Owner" in report.ignored


def test_an_attribute_ignored_in_both_sections_is_listed_once():
    plan = parse_plan(example("drift-and-changes.json"))
    report = build_report(plan, [parse_rule("*:tags_all")])
    assert report.ignored == ("aws_s3_bucket.logs:tags_all.Owner",)


def test_an_update_whose_every_difference_is_ignored_drops_out():
    plan = parse_plan(example("console-changes.json"))
    report = build_report(plan, [parse_rule("aws_s3_bucket.logs:tags*")])
    assert "aws_s3_bucket.logs" not in [c.address for c in report.drift]
    assert len(report.drift) == 3


def test_attribute_rules_never_hide_a_deletion():
    plan = parse_plan(plan_doc(drift=[entry("aws_instance.a", ["delete"], {"tags": {"a": 1}}, None)]))
    assert len(build_report(plan, [parse_rule("*:*")]).drift) == 1


def test_attribute_rules_never_hide_a_replacement():
    rc = entry("aws_instance.a", ["delete", "create"], {"ami": "a"}, {"ami": "b"}, replace_paths=[["ami"]])
    plan = parse_plan(plan_doc(changes=[rc]))
    report = build_report(plan, [parse_rule("*:ami")])
    assert len(report.changes) == 1 and report.changes[0].attributes == ()


@pytest.mark.parametrize(
    ("drift", "changes", "fail_on", "code"),
    [
        (False, False, "any", 0),
        (True, False, "any", 2),
        (False, True, "any", 2),
        (True, True, "any", 2),
        (False, True, "drift", 0),
        (True, False, "drift", 2),
        (True, True, "never", 0),
    ],
)
def test_exit_codes(drift, changes, fail_on, code):
    plan = parse_plan(
        plan_doc(
            drift=[entry("a.b", ["delete"], {"x": 1}, None)] if drift else (),
            changes=[entry("a.c", ["create"], None, {"x": 1})] if changes else (),
        )
    )
    assert exit_code(build_report(plan), fail_on) == code
