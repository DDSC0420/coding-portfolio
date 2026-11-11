import pytest

from plandrift.plan import PlanError, decode, parse_plan

from .conftest import entry, plan_doc


def one_change(rc):
    plan = parse_plan(plan_doc(changes=[rc]))
    assert len(plan.changes) <= 1
    return plan.changes[0] if plan.changes else None


@pytest.mark.parametrize(
    ("actions", "action", "symbol"),
    [
        (["create"], "create", "+"),
        (["update"], "update", "~"),
        (["delete"], "delete", "-"),
        (["delete", "create"], "replace", "-/+"),
        (["create", "delete"], "replace", "-/+"),
        (["forget"], "forget", "x"),
    ],
)
def test_actions(actions, action, symbol):
    c = one_change(entry("aws_s3_bucket.a", actions, {"x": 1} if actions != ["create"] else None, {"x": 2}))
    assert (c.action, c.symbol) == (action, symbol)


def test_no_op_is_skipped():
    assert one_change(entry("aws_s3_bucket.a", ["no-op"], {"x": 1}, {"x": 1})) is None


def test_data_source_reads_are_skipped():
    assert one_change(entry("data.aws_ami.u", ["read"], None, {"id": "x"}, mode="data")) is None


def test_moved_resource():
    rc = entry("aws_s3_bucket.new", ["no-op"], {"x": 1}, {"x": 1})
    rc["previous_address"] = "aws_s3_bucket.old"
    c = one_change(rc)
    assert c.action == "move" and c.notes == ("from aws_s3_bucket.old",)


def test_moved_and_updated_keeps_update():
    rc = entry("aws_s3_bucket.new", ["update"], {"x": 1}, {"x": 2})
    rc["previous_address"] = "aws_s3_bucket.old"
    c = one_change(rc)
    assert c.action == "update" and "moved from aws_s3_bucket.old" in c.notes


def test_import_only():
    c = one_change(entry("aws_s3_bucket.a", ["no-op"], {"x": 1}, {"x": 1}, importing={"id": "bucket-a"}))
    assert c.action == "import"


def test_import_with_update_notes_importing():
    c = one_change(entry("aws_s3_bucket.a", ["update"], {"x": 1}, {"x": 2}, importing={"id": "bucket-a"}))
    assert c.action == "update" and "importing" in c.notes


def test_deposed_and_reason():
    rc = entry("aws_instance.a", ["delete"], {"x": 1}, None)
    rc["deposed"] = "00000001"
    rc["action_reason"] = "delete_because_no_resource_config"
    c = one_change(rc)
    assert c.notes == ("deposed object 00000001", "removed from the configuration")


def test_unknown_reason_is_shown_readably():
    rc = entry("aws_instance.a", ["delete"], {"x": 1}, None)
    rc["action_reason"] = "delete_because_something_new"
    assert one_change(rc).notes == ("delete because something new",)


def test_unrecognised_action_combination_is_kept_not_dropped():
    c = one_change(entry("aws_instance.a", ["delete", "forget"], {"x": 1}, None))
    assert c.action == "delete+forget" and c.symbol == "?"


def test_create_and_delete_list_no_attributes():
    assert one_change(entry("a.b", ["create"], None, {"x": 1})).attributes == ()
    assert one_change(entry("a.b", ["delete"], {"x": 1}, None)).attributes == ()


def test_drift_and_changes_are_separate():
    plan = parse_plan(
        plan_doc(drift=[entry("a.b", ["delete"], {"x": 1}, None)], changes=[entry("a.b", ["create"], None, {"x": 1})])
    )
    assert [c.action for c in plan.drift] == ["delete"]
    assert [c.action for c in plan.changes] == ["create"]


def test_clean_plan_without_either_list():
    plan = parse_plan(plan_doc())
    assert plan.drift == () and plan.changes == ()


def test_metadata():
    plan = parse_plan(plan_doc(timestamp="2026-11-10T08:30:00Z", complete=False))
    assert plan.terraform_version == "1.9.8" and plan.timestamp == "2026-11-10T08:30:00Z"
    assert plan.complete is False


def test_old_format_version_is_accepted():
    assert parse_plan(plan_doc(format_version="0.2")).format_version == "0.2"


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "expected a JSON object"),
        ({"format_version": "1.0", "values": {"root_module": {}}}, "state JSON"),
        ({"hello": "world"}, "doesn't look like Terraform plan JSON"),
        ({"format_version": "1.2"}, "doesn't look like Terraform plan JSON"),
        ({"format_version": "2.0", "planned_values": {}}, "newer than this tool reads"),
        ({"format_version": "1.2", "planned_values": {}, "errored": True}, "errored"),
        ({"format_version": "1.2", "resource_changes": {"a": 1}}, "should be a list"),
        ({"format_version": "1.2", "resource_changes": [{"address": "a.b"}]}, "malformed"),
    ],
)
def test_bad_documents(doc, message):
    with pytest.raises(PlanError, match=message):
        parse_plan(doc)


def test_decode_binary_plan_file_explains_how_to_convert():
    with pytest.raises(PlanError, match=r"binary plan file.*terraform show -json plan.out"):
        decode(b"PK\x03\x04rest-of-zip", source="plan.out")


@pytest.mark.parametrize(
    ("raw", "message"),
    [(b"", "empty"), (b"   \n", "empty"), (b"{not json", "isn't valid JSON"), (b"\xff\xfe\x00", "isn't UTF-8")],
)
def test_decode_errors(raw, message):
    with pytest.raises(PlanError, match=message):
        decode(raw)


def test_decode_tolerates_a_byte_order_mark():
    # PowerShell's `>` redirection writes a BOM.
    assert decode(b'\xef\xbb\xbf{"a": 1}') == {"a": 1}
