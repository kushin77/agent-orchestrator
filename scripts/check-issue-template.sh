#!/usr/bin/env bash
# check-issue-template.sh — the issue-brief contract gate (issue #165).
#
# The fleet briefs every task on the GitHub issues board, and issue #165 makes
# that brief a governed artifact: `.github/ISSUE_TEMPLATE/fleet-task.yml` must
# carry every field the dispatcher needs, and its FinOps vocabularies must be
# the harvested canonical ones. A template nobody validates is a dead artifact,
# so this gate fails, by name, when a required field or the file itself is
# missing.
#
#   * `.github/ISSUE_TEMPLATE/fleet-task.yml` exists and parses as YAML
#   * it is a GitHub issue form: top-level name/description/body all present
#   * the required field ids exist in `body` and are marked `required: true`
#   * the model-tier and thinking-effort option sets are exactly canonical
#
# It also runs its own negative control: it drops one required field from a copy
# of the template and requires the validator to refuse it. If the mutant passes,
# this gate reports FAIL — a check that cannot fail is a formality (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-issue-template.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

template=".github/ISSUE_TEMPLATE/fleet-task.yml"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-issue-template: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$template" ]; then
  echo "check-issue-template: FAIL — $template is missing (the fleet has no brief contract)" >&2
  exit 1
fi

# validate <path> — verify one issue-form file. 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$1" <<'PY'
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-issue-template: CANNOT-ASSESS - PyYAML not installed ({exc})", file=sys.stderr)
    raise SystemExit(2)

# The fields the dispatcher and the FinOps block read out of every brief
# (issue #165). Each must exist and must be marked required: true.
CORE = [
    "role",
    "epic",
    "lane",
    "model-tier",
    "thinking-effort",
    "verify-command",
    "chain-markers",
]
# Declared by the issue's scope; presence is enforced, requiredness is not.
ALSO = ["pillar", "acceptance-criteria", "evidence"]

# Canonical harvested vocabularies: tier names from the FinOps tier policy and
# the role-effort ladder (flash/pro/auditor; none/low/medium/high). A rename in
# either place is drift this gate must catch.
VOCAB = {
    "role": {"brain", "sister", "subagent"},
    "model-tier": {"flash", "pro", "auditor"},
    "thinking-effort": {"none", "low", "medium", "high"},
}

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        form = yaml.safe_load(handle)
except OSError as exc:
    print(f"check-issue-template: FAIL - cannot read {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
except yaml.YAMLError as exc:
    print(f"check-issue-template: FAIL - {path} is not valid YAML: {exc}", file=sys.stderr)
    raise SystemExit(1)

findings = []
if not isinstance(form, dict):
    findings.append(f"{path}: top level is not a mapping (not a GitHub issue form)")
    form = {}

for key in ("name", "description", "body"):
    if not form.get(key):
        findings.append(f"{path}: top-level key '{key}' is missing")

body = form.get("body")
if not isinstance(body, list):
    findings.append(f"{path}: 'body' is not a list")
    body = []

elements = {}
for item in body:
    if isinstance(item, dict) and item.get("id"):
        elements[item["id"]] = item

for field_id in CORE:
    item = elements.get(field_id)
    if item is None:
        findings.append(f"{path}: required field id '{field_id}' is missing from body")
        continue
    validations = item.get("validations")
    if not isinstance(validations, dict) or validations.get("required") is not True:
        findings.append(f"{path}: field '{field_id}' is not marked required: true")

for field_id in ALSO:
    if field_id not in elements:
        findings.append(f"{path}: field id '{field_id}' is missing from body")

for field_id, allowed in VOCAB.items():
    item = elements.get(field_id)
    if item is None:
        continue
    attributes = item.get("attributes")
    options = attributes.get("options") if isinstance(attributes, dict) else None
    if not isinstance(options, list):
        findings.append(f"{path}: field '{field_id}' has no options list")
        continue
    got = set(options)
    if got != allowed:
        findings.append(
            f"{path}: field '{field_id}' options {sorted(got)} != canonical {sorted(allowed)}"
        )

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(f"  OK    {path}: required fields present, FinOps vocabularies canonical")
raise SystemExit(0)
PY
}

rc=0
validate "$template" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-issue-template: FAIL — $template violates the issue-brief contract" >&2
    exit 1
    ;;
  *)
    echo "check-issue-template: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# Negative control: the gate must refuse a template that lost a required field.
work="/tmp/issue-template-gate.$$.$(date +%s)"
mkdir -p "$work" || exit 2
grep -vE '^[[:space:]]*id:[[:space:]]*role[[:space:]]*$' "$template" > "$work/mutant.yml"
if ! grep -q 'id:' "$work/mutant.yml"; then
  rm -rf "$work"
  echo "check-issue-template: CANNOT-ASSESS — could not build the negative control" >&2
  exit 2
fi
mutant_out="$(validate "$work/mutant.yml" 2>&1)"
mutant_rc=$?
rm -rf "$work"

if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -q "required field id 'role' is missing"; then
  echo "  OK    negative control: dropping required field 'role' is refused and named"
else
  echo "check-issue-template: FAIL — negative control passed; removing a required field was not caught (the gate cannot fail)" >&2
  exit 1
fi

echo "check-issue-template: OK — brief contract enforced, negative control refused"
exit 0
