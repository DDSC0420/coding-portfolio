"""Assert a plandrift JSON report matches what run.sh did. Written from the scenario, not from output.

    python3 check_report.py refresh-only|full REPORT.json SERVICE_NAME
"""

import json
import sys

# What run.sh changes outside Terraform, and how each shows up in the plan:
#  - out/app.conf edited: the local provider treats a file whose content changed as gone, so
#    Terraform reports it deleted.
#  - out/RUNBOOK.md removed: deleted.
#  - Consul: feature_flag flipped off -> on, and a key `debug` added that Terraform doesn't manage.
DRIFT = {
    "local_file.app_config": ("delete", {}),
    "local_file.runbook": ("delete", {}),
    "consul_key_prefix.app": (
        "update",
        {"subkeys.feature_flag": ("off", "on"), "subkeys.debug": (None, "true")},
    ),
}

# The full plan also sets name_words=3: a new random name, so everything built from it changes.
CHANGES = {
    "random_pet.service": ("replace", {"length"}),  # attributes that force replacement
    "local_file.app_config": ("create", set()),  # gone from state after refresh, so recreated
    "local_file.runbook": ("create", set()),
    "consul_key_prefix.app": ("replace", {"path_prefix"}),
}


def main(mode: str, path: str, service: str) -> None:
    report = json.load(open(path, encoding="utf-8"))
    problems = []

    drift = {c["address"]: c for c in report["drift"]}
    if set(drift) != set(DRIFT):
        problems.append(f"drift addresses: expected {sorted(DRIFT)}, got {sorted(drift)}")
    for address, (action, attrs) in DRIFT.items():
        got = drift.get(address)
        if not got:
            continue
        if got["action"] != action:
            problems.append(f"{address}: expected drift action {action}, got {got['action']}")
        seen = {a["path"]: (a["before"], a["after"]) for a in got["attributes"]}
        if seen != attrs:
            problems.append(f"{address}: expected attribute changes {attrs}, got {seen}")

    changes = {c["address"]: c for c in report["changes"]}
    if mode == "refresh-only":
        if changes:
            problems.append(f"a refresh-only plan should propose no changes, got {sorted(changes)}")
    else:
        if set(changes) != set(CHANGES):
            problems.append(f"change addresses: expected {sorted(CHANGES)}, got {sorted(changes)}")
        for address, (action, forcing) in CHANGES.items():
            got = changes.get(address)
            if not got:
                continue
            if got["action"] != action:
                problems.append(f"{address}: expected {action}, got {got['action']}")
            forced = {a["path"] for a in got["attributes"] if a["forces_replacement"]}
            if forced != forcing:
                problems.append(f"{address}: expected {forcing} to force replacement, got {forced}")
        prefix = next((a for a in changes["consul_key_prefix.app"]["attributes"] if a["path"] == "path_prefix"), None)
        if not prefix or prefix["before"] != f"apps/{service}/":
            problems.append(f"consul_key_prefix.app path_prefix should start from apps/{service}/, got {prefix}")

    if problems:
        print(f"FAIL ({mode}, {path}):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"ok: {mode} report matches the scenario ({len(drift)} drift, {len(changes)} changes)")


if __name__ == "__main__":
    main(*sys.argv[1:4])
