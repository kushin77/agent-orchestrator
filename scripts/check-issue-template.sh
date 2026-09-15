#!/usr/bin/env bash
# check-issue-template.sh — the issue-brief contract gate (issue #165, extended
# by issue #622).
#
# The fleet briefs every task on the GitHub issues board, and issue #165 makes
# that brief a governed artifact. Issue #622 extends the contract from one form
# to three, because a board with a single governed shape leaves every epic and
# every defect in an unvalidated free-text box.
#
#   * `.github/ISSUE_TEMPLATE/fleet-task.yml` — the dispatcher brief (#165).
#     Its required field ids and its FinOps vocabularies are UNCHANGED here.
#   * `.github/ISSUE_TEMPLATE/epic.yml` — the epic brief (#622): objective,
#     measured landscape, child list, verification contract.
#   * `.github/ISSUE_TEMPLATE/bug.yml` — the defect brief (#622): observed vs
#     expected, the reproduction command, the canonical severity, and the RCA
#     link into the ledger the defect feeds.
#
# Each form is validated BY NAME, so a deleted or gutted form fails loudly
# rather than quietly reducing the board to an unvalidated box. Beyond the
# per-form field contract, every form must really be a GitHub issue form:
# top-level name/description/body, `body` a list of elements whose `type` the
# board can render, and every non-markdown element carrying an `id` and an
# `attributes.label`.
#
# It also runs its own negative control, once per form: it drops one required
# field from a copy of that form and requires the validator to refuse it by
# name. The mutant is asserted to differ from the original (sha256), so a
# mutation that never landed cannot be reported as a passing control. If a
# mutant passes, this gate reports FAIL — a check that cannot fail is a
# formality (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-issue-template.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-issue-template: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# validate <path> <form-kind> — verify one issue form against its contract.
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$1" "$2" <<'PY'
import os
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-issue-template: CANNOT-ASSESS - PyYAML not installed ({exc})", file=sys.stderr)
    raise SystemExit(2)

# The element types a GitHub issue form can render. A body carrying anything
# else is not a form the board can show, so it is refused rather than ignored.
ELEMENT_TYPES = {"markdown", "input", "textarea", "dropdown", "checkboxes"}

# One contract per governed form, keyed by the form's kind. The validator is
# shared, so adding a fourth form is a row here and nothing else — and every
# row is validated by name.
#
#   CORE         the required field ids: each must exist and be required: true
#   ALSO         declared by the brief; presence enforced, requiredness is not
#   VOCAB        field id -> the exact canonical option set
#   MUST_MENTION field id -> a path its *guidance* must name, and which must
#                exist on disk, so a governed form points at a real artifact
CONTRACTS = {
    # The dispatcher brief (issue #165). UNCHANGED by #622: these are the fields
    # the dispatcher and the FinOps block read out of every brief.
    "fleet-task": (
        [
            "role",
            "epic",
            "lane",
            "model-tier",
            "thinking-effort",
            "verify-command",
            "chain-markers",
        ],
        # Declared by the issue's scope; presence is enforced, requiredness is not.
        ["pillar", "acceptance-criteria", "evidence"],
        # Canonical harvested vocabularies: tier names from the FinOps tier
        # policy and the role-effort ladder (flash/pro/auditor;
        # none/low/medium/high). A rename in either place is drift this gate
        # must catch.
        {
            "role": {"brain", "sister", "subagent"},
            "model-tier": {"flash", "pro", "auditor"},
            "thinking-effort": {"none", "low", "medium", "high"},
        },
        {},
    ),
    # The epic brief (issue #622) — the EPIC #616 shape.
    "epic": (
        ["objective", "measured-landscape", "children", "verification-contract"],
        ["purpose"],
        {},
        {},
    ),
    # The defect brief (issue #622).
    "bug": (
        ["observed", "expected", "reproduction", "severity", "rca"],
        [],
        # Harvested from the isolation triage model: every Severity in
        # guardrails/isolation/model.py maps to a triage action, so a defect
        # form that invents a rung cannot be triaged.
        {"severity": {"critical", "high", "medium", "low"}},
        # A defect's RCA link must point at the ledger it feeds.
        {"rca": "governance/lessons/"},
    ),
}

path = sys.argv[1]
kind = sys.argv[2]
if kind not in CONTRACTS:
    print(f"check-issue-template: CANNOT-ASSESS - no contract for form kind '{kind}'", file=sys.stderr)
    raise SystemExit(2)
CORE, ALSO, VOCAB, MUST_MENTION = CONTRACTS[kind]

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
for index, item in enumerate(body, start=1):
    if not isinstance(item, dict):
        findings.append(f"{path}: body[{index}] is not a mapping")
        continue
    element_type = item.get("type")
    if element_type not in ELEMENT_TYPES:
        findings.append(
            f"{path}: body[{index}] has type {element_type!r}, which no GitHub issue form renders"
        )
        continue
    if element_type == "markdown":
        if item.get("id"):
            findings.append(f"{path}: body[{index}] is a markdown block and must not carry an id")
        continue
    field_id = item.get("id")
    if not field_id:
        findings.append(
            f"{path}: body[{index}] ({element_type}) has no id, so the brief cannot be read from it"
        )
        continue
    attributes = item.get("attributes")
    if not isinstance(attributes, dict) or not attributes.get("label"):
        findings.append(f"{path}: field '{field_id}' has no attributes.label")
    elements[field_id] = item

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

for field_id, needle in MUST_MENTION.items():
    item = elements.get(field_id)
    if item is None:
        continue
    attributes = item.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    # The field's guidance, not its label: a label that names the ledger while
    # the guidance points anywhere is exactly the drift this refuses.
    guidance = " ".join(
        str(attributes.get(key, "")) for key in ("description", "placeholder", "value")
    )
    if needle not in guidance:
        findings.append(f"{path}: field '{field_id}' guidance does not name '{needle}'")
    elif not os.path.isdir(needle):
        findings.append(f"{path}: field '{field_id}' points at '{needle}', which does not exist")

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(f"  OK    {path}: GitHub issue form, {kind} brief contract enforced")
raise SystemExit(0)
PY
}

# control <file> <kind> <field> — the negative control for one form: drop one
# required field from a copy of it and require the validator to refuse it, by
# name. 0 OK / 1 FAIL / 2 CANNOT-ASSESS.
control() {
  ctl_file="$1"
  ctl_kind="$2"
  ctl_field="$3"

  if ! grep -qE "^[[:space:]]*id:[[:space:]]*${ctl_field}[[:space:]]*$" "$ctl_file"; then
    echo "check-issue-template: CANNOT-ASSESS — $ctl_file has no 'id: $ctl_field' line for the control to drop" >&2
    return 2
  fi

  # A scratch tree whose name carries no trailing run of `X`: this repo's
  # docs-lint scans for unfinished markers and a `mktemp` placeholder suffix
  # trips it, so this follows the convention the sibling gates use — an explicit
  # /tmp name, with `mkdir` refusing loudly rather than silently reusing another
  # run's tree.
  ctl_work="/tmp/issue-template-gate.$ctl_kind.$ctl_field.$(date +%s%N).$$"
  if ! mkdir "$ctl_work" 2>/dev/null; then
    echo "check-issue-template: CANNOT-ASSESS — cannot create scratch dir $ctl_work" >&2
    return 2
  fi
  grep -vE "^[[:space:]]*id:[[:space:]]*${ctl_field}[[:space:]]*$" "$ctl_file" > "$ctl_work/mutant.yml"

  # A control whose mutation never landed proves nothing, so the mutant is
  # required to differ from the original before it is worth validating.
  ctl_before="$(sha256sum "$ctl_file" | cut -d' ' -f1)"
  ctl_after="$(sha256sum "$ctl_work/mutant.yml" | cut -d' ' -f1)"
  if [ "$ctl_before" = "$ctl_after" ]; then
    rm -rf "$ctl_work"
    echo "check-issue-template: FAIL — negative control for $ctl_file never landed: the mutant is byte-identical to the original" >&2
    return 1
  fi

  ctl_out="$(validate "$ctl_work/mutant.yml" "$ctl_kind" 2>&1)"
  ctl_rc=$?
  rm -rf "$ctl_work"

  if [ "$ctl_rc" -eq 2 ]; then
    echo "check-issue-template: CANNOT-ASSESS — the $ctl_kind control could not be assessed" >&2
    printf '%s\n' "$ctl_out" >&2
    return 2
  fi
  if [ "$ctl_rc" -ne 1 ]; then
    echo "check-issue-template: FAIL — negative control passed; $ctl_file without required field '$ctl_field' was not refused (the gate cannot fail)" >&2
    return 1
  fi
  if ! printf '%s\n' "$ctl_out" | grep -q "required field id '$ctl_field' is missing"; then
    echo "check-issue-template: FAIL — negative control for $ctl_file was refused, but the missing field '$ctl_field' was not named" >&2
    printf '%s\n' "$ctl_out" >&2
    return 1
  fi

  echo "  OK    negative control: $ctl_file without required field '$ctl_field' is refused and named"
  return 0
}

# Every governed form, by name, with the required field its negative control
# drops. Each control field is one the brief genuinely cannot do without, so a
# form that loses it is broken rather than merely different.
fail=0
while read -r file kind field; do
  if [ ! -f "$file" ]; then
    echo "check-issue-template: FAIL — $file is missing (the fleet has no $kind brief contract)" >&2
    fail=1
    continue
  fi

  rc=0
  validate "$file" "$kind" || rc=$?
  form_ok=0
  case "$rc" in
    0) form_ok=1 ;;
    1)
      echo "check-issue-template: FAIL — $file violates the $kind brief contract" >&2
      fail=1
      ;;
    *)
      echo "check-issue-template: CANNOT-ASSESS — validator returned $rc for $file" >&2
      exit 2
      ;;
  esac

  # A form that already failed needs no control: the gate has just been observed
  # failing on it — which is the proof the control exists to provide — and the
  # original the control would mutate is no longer intact.
  if [ "$form_ok" -eq 0 ]; then
    continue
  fi

  rc=0
  control "$file" "$kind" "$field" || rc=$?
  case "$rc" in
    0) : ;;
    1) fail=1 ;;
    *) exit 2 ;;
  esac
done <<'FORMS'
.github/ISSUE_TEMPLATE/fleet-task.yml fleet-task role
.github/ISSUE_TEMPLATE/epic.yml epic objective
.github/ISSUE_TEMPLATE/bug.yml bug severity
FORMS

if [ "$fail" -ne 0 ]; then
  echo "check-issue-template: FAIL — a governed issue form violates its brief contract" >&2
  exit 1
fi

echo "check-issue-template: OK — fleet-task, epic and bug brief contracts enforced; every negative control refused"
exit 0
