# Changelog

All notable changes to plandrift. Versions follow [semantic versioning](https://semver.org); release tags are `drift-vX.Y.Z`.

## 0.1.0 (2026-11-12)

First release.

- `plandrift check PLAN_JSON` reads `terraform show -json` output from a file or stdin and reports two things: resources changed outside Terraform (`resource_drift`) and what applying the plan would do (`resource_changes`).
- Attribute-level differences for updates and replacements, with the attributes that force a replacement marked.
- Sensitive values are never printed, in any format: only that they changed. Values known only after apply are shown as such, not as null.
- Three formats: a terminal table (`table`), Markdown for a PR comment or job summary (`markdown`), and JSON with a versioned schema (`json`).
- Exit codes for CI: `0` nothing found, `2` found, `1` error. `--fail-on any|drift|never` chooses what counts.
- `--ignore ADDRESS` and `--ignore ADDRESS:ATTRIBUTE` rules with `*` and `?` wildcards.
- `plandrift run` makes the plan itself (`terraform plan -refresh-only`, or `--full-plan`), with `--terraform-bin tofu` for OpenTofu.
- A composite GitHub Action (`DDSC0420/coding-portfolio/04-terraform-drift`) that writes the report to the job summary and sets outputs.
- No runtime dependencies. Python 3.10 to 3.13 on Linux, macOS and Windows.
- MIT licence.
