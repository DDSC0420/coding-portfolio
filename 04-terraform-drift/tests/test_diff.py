from plandrift.diff import ABSENT, HIDDEN, UNKNOWN, attribute_changes, format_path, show_value


def changes(before, after, **extra):
    return {a.name: a for a in attribute_changes({"before": before, "after": after, **extra})}


def test_changed_leaf():
    out = changes({"size": 1, "name": "a"}, {"size": 2, "name": "a"})
    assert list(out) == ["size"]
    assert (out["size"].before, out["size"].after, out["size"].kind) == (1, 2, "changed")


def test_nested_maps_and_lists_give_leaf_paths():
    before = {"ingress": [{"ports": [443], "cidr": "10.0.0.0/8"}]}
    after = {"ingress": [{"ports": [443, 22], "cidr": "0.0.0.0/0"}]}
    out = changes(before, after)
    assert set(out) == {"ingress[0].ports[1]", "ingress[0].cidr"}
    assert out["ingress[0].ports[1]"].kind == "added"
    assert out["ingress[0].ports[1]"].before is ABSENT


def test_removed_key_and_shrunk_list():
    out = changes({"tags": {"a": "1", "b": "2"}, "list": [1, 2]}, {"tags": {"a": "1"}, "list": [1]})
    assert out["tags.b"].kind == "removed" and out["tags.b"].after is ABSENT
    assert out["list[1]"].kind == "removed"


def test_whole_block_added_is_listed_leaf_by_leaf():
    out = changes({"rule": None}, {"rule": {"port": 22, "proto": "tcp"}})
    assert set(out) == {"rule.port", "rule.proto"}
    assert all(a.before is ABSENT for a in out.values())


def test_null_to_empty_is_not_a_change():
    assert changes({"tags": None}, {"tags": {}}) == {}


def test_type_change_is_one_leaf():
    out = changes({"x": "1"}, {"x": [1]})
    assert list(out) == ["x"] and out["x"].after == [1]


def test_unknown_after_apply():
    out = changes({"id": "i-1", "ip": "10.0.0.1"}, {}, after_unknown={"id": True, "ip": True})
    assert out["id"].after is UNKNOWN and out["id"].before == "i-1"
    assert len(out) == 2


def test_unknown_key_absent_from_both_sides_is_still_listed():
    out = changes({}, {}, after_unknown={"arn": True})
    assert out["arn"].before is ABSENT and out["arn"].after is UNKNOWN


def test_unknown_inside_a_list():
    out = changes({"nics": [{"ip": "a"}]}, {"nics": [{}]}, after_unknown={"nics": [{"ip": True}]})
    assert out["nics[0].ip"].after is UNKNOWN


def test_sensitive_leaf_is_hidden_on_both_sides():
    out = changes(
        {"password": "old-secret", "size": 1},
        {"password": "new-secret", "size": 1},
        before_sensitive={"password": True},
        after_sensitive={"password": True},
    )
    a = out["password"]
    assert a.sensitive and a.before is HIDDEN and a.after is HIDDEN


def test_sensitive_unchanged_is_not_reported():
    assert changes({"password": "s"}, {"password": "s"}, after_sensitive={"password": True}) == {}


def test_sensitive_container_is_one_hidden_change():
    out = changes(
        {"conn": {"user": "u", "pass": "p1"}},
        {"conn": {"user": "u", "pass": "p2"}},
        before_sensitive={"conn": True},
        after_sensitive={"conn": True},
    )
    assert list(out) == ["conn"] and out["conn"].after is HIDDEN


def test_sensitive_only_on_one_side_still_hides():
    out = changes({"token": "plain-before"}, {"token": "secret-after"}, after_sensitive={"token": True})
    assert out["token"].before is HIDDEN and out["token"].after is HIDDEN


def test_block_removed_with_a_sensitive_child_hides_that_child():
    out = changes({"cfg": {"key": "s3cr3t", "name": "n"}}, {"cfg": None}, before_sensitive={"cfg": {"key": True}})
    assert out["cfg.key"].before is HIDDEN
    assert out["cfg.name"].before == "n"


def test_type_change_with_a_sensitive_child_hides_the_whole_value():
    # One side is a map holding a secret, the other isn't a map: never print the map.
    out = changes({"cfg": {"key": "s3cr3t"}}, {"cfg": "plain"}, before_sensitive={"cfg": {"key": True}})
    assert list(out) == ["cfg"]
    assert out["cfg"].before is HIDDEN and out["cfg"].after is HIDDEN


def test_whole_object_sensitive():
    out = changes({"a": 1}, {"a": 2}, before_sensitive=True, after_sensitive=True)
    assert list(out) == ["(whole value)"] and out["(whole value)"].after is HIDDEN


def test_replace_paths_mark_forcing_attributes():
    out = changes({"ami": "a", "type": "t3"}, {"ami": "b", "type": "t4"}, replace_paths=[["ami"]])
    assert out["ami"].forces_replacement and not out["type"].forces_replacement


def test_replace_path_inside_a_hidden_container_still_flags_it():
    out = changes(
        {"conf": {"k": 1}}, {"conf": {"k": 2}}, before_sensitive={"conf": True}, replace_paths=[["conf", "k"]]
    )
    assert out["conf"].forces_replacement


def test_create_and_delete_sides():
    assert {a.kind for a in attribute_changes({"before": None, "after": {"a": 1}})} == {"added"}
    assert {a.kind for a in attribute_changes({"before": {"a": 1}, "after": None})} == {"removed"}


def test_format_path():
    assert format_path(("ingress", 1, "cidr_blocks", 0)) == "ingress[1].cidr_blocks[0]"
    assert format_path(("tags", "kubernetes.io/role")) == 'tags["kubernetes.io/role"]'
    assert format_path(("tags", "Cost Centre")) == 'tags["Cost Centre"]'
    assert format_path(()) == "(whole value)"


def test_show_value():
    assert show_value("x") == '"x"'
    assert show_value(22) == "22"
    assert show_value(False) == "false"
    assert show_value(None) == "null"
    assert show_value(ABSENT) == "(none)"
    assert show_value(UNKNOWN) == "(known after apply)"
    assert show_value(HIDDEN) == "(sensitive)"
    assert show_value({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    long = show_value("x" * 200, limit=20)
    assert len(long) == 20 and long.endswith("...")
