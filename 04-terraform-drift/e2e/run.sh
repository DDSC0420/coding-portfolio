#!/usr/bin/env bash
# Real end-to-end check: apply, change things outside Terraform, make sure plandrift notices.
# Needs: terraform (or TF=tofu), plandrift on PATH, a Consul dev agent on 127.0.0.1:8500.
# Writes the plan JSON it made to $OUT_DIR (default ./plans).
set -euo pipefail
cd "$(dirname "$0")"

TF="${TF:-terraform}"
OUT="${OUT_DIR:-$PWD/plans}"
CONSUL="http://127.0.0.1:8500"
mkdir -p "$OUT"

step() { printf '\n== %s\n' "$*"; }

expect_exit() { # expect_exit CODE command...
  local want=$1
  shift
  set +e
  "$@"
  local got=$?
  set -e
  if [ "$got" != "$want" ]; then
    echo "FAIL: expected exit $want, got $got from: $*"
    exit 1
  fi
  echo "ok: exit $got from: $*"
}

step "Apply ($($TF version | head -1))"
rm -rf out ./*.tfplan terraform.tfstate*
$TF init -input=false -no-color
$TF apply -auto-approve -input=false -no-color
SERVICE=$($TF output -raw service)
echo "service name: $SERVICE"

step "Nothing changed yet: expect no drift"
$TF plan -refresh-only -input=false -no-color -out=clean.tfplan
$TF show -json clean.tfplan >"$OUT/clean.json"
expect_exit 0 plandrift check "$OUT/clean.json"

step "Change things behind Terraform's back"
echo "replicas = 5" >>out/app.conf                                               # hand edit
rm out/RUNBOOK.md                                                                # deleted
curl -fsS -X PUT --data "on" "$CONSUL/v1/kv/apps/$SERVICE/feature_flag" >/dev/null # flag flipped
curl -fsS -X PUT --data "true" "$CONSUL/v1/kv/apps/$SERVICE/debug" >/dev/null      # key added

step "Refresh-only plan: expect drift"
$TF plan -refresh-only -input=false -no-color -out=drift.tfplan
$TF show -json drift.tfplan >"$OUT/refresh-only.json"
expect_exit 2 plandrift check "$OUT/refresh-only.json"
plandrift check "$OUT/refresh-only.json" -f json -o "$OUT/refresh-only.report.json" --fail-on never
python3 check_report.py refresh-only "$OUT/refresh-only.report.json" "$SERVICE"

step "plandrift run makes the same plan itself"
expect_exit 2 plandrift run --chdir . --terraform-bin "$TF" -f json -o "$OUT/run.report.json" -- -lock-timeout=30s
python3 check_report.py refresh-only "$OUT/run.report.json" "$SERVICE"

step "Normal plan with a code change as well: drift plus pending changes"
$TF plan -input=false -no-color -var name_words=3 -out=full.tfplan
$TF show -json full.tfplan >"$OUT/full.json"
expect_exit 2 plandrift check "$OUT/full.json" --fail-on drift
plandrift check "$OUT/full.json" -f json -o "$OUT/full.report.json" --fail-on never
python3 check_report.py full "$OUT/full.report.json" "$SERVICE"

step "Reports as a person sees them"
plandrift check "$OUT/full.json" --color always --fail-on never
plandrift check "$OUT/full.json" -f markdown -o "$OUT/full.md" --fail-on never
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "## End-to-end run with $($TF version | head -1)"
    echo
    sed 's/^## /### /; s/^### /#### /' "$OUT/full.md"
  } >>"$GITHUB_STEP_SUMMARY"
fi

printf '\ne2e passed\n'
