#!/usr/bin/env bash
# check-issue-template.sh — the issue-brief contract gate (issue #165, extended
# by issues #622 and #1533).
#
# The fleet briefs every task on the GitHub issues board, and issue #165 makes
# that brief a governed artifact. Issue #622 extends the contract from one form
# to three, because a board with a single governed shape leaves every epic and
# every defect in an unvalidated free-text box.
#
#   * `.github/ISSUE_TEMPLATE/fleet-task.yml` — the dispatcher brief (#165).
#     Its #165 field ids and its FinOps vocabularies are UNCHANGED here.
#   * `.github/ISSUE_TEMPLATE/epic.yml` — the epic brief (#622): objective,
#     measured landscape, child list, verification contract.
#   * `.github/ISSUE_TEMPLATE/bug.yml` — the defect brief (#622): observed vs
#     expected, the reproduction command, the canonical severity, and the RCA
#     link into the ledger the defect feeds.
#   * `.github/ISSUE_TEMPLATE/config.yml` — the door (#1533): blank issues OFF,
#     so the three forms above are the only way onto the board.
#   * `.github/PULL_REQUEST_TEMPLATE.md` — the merge contract's shape (#1533):
#     a `Closes #<n>` line, plus an Evidence block carrying an acceptance output
#     and a negative-control output.
#
# Issue #1533 extends the contract along the lifecycle axis every brief now
# carries: the epic edge, the tier the work starts at, the root cause, the
# closure acknowledgement, and the escalation path. Those five fields are the
# same on every form but one — the epic form owes all of them except the parent
# edge, because an epic is a chain head — and that exemption is ASSERTED against
# the declared contract rather than assumed, so a form that quietly drops a
# second field cannot pass by being approximately the right shape.
#
# Each form is validated BY NAME, so a deleted or gutted form fails loudly
# rather than quietly reducing the board to an unvalidated box. Beyond the
# per-form field contract, every form must really be a GitHub issue form:
# top-level name/description/body, `body` a list of elements whose `type` the
# board can render, every non-markdown element carrying an `id` and an
# `attributes.label`, and every `checkboxes` element carrying options — a
# checkbox with no options renders nothing to acknowledge (#1533).
#
# It also runs its own negative control for every requirement it declares — one
# per required form field, one per lifecycle field, and one per config and
# PR-template requirement: it mutates a copy and requires the validator to
# refuse the mutation by name. The mutant is asserted to differ from the
# original (sha256), so a mutation that never landed cannot be reported as a
# passing control. If a mutant passes, this gate reports FAIL — a check that
# cannot fail is a formality (GR-12).
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

# The lifecycle brief contract (issue #1533): the five field ids every governed
# form must carry, so a filed brief declares its epic edge, the tier it starts
# at, the root cause it is acting on, its acknowledgement of the closure
# contract, and where it escalates to. They are declared once here and applied
# per form below, so a sixth field is one string rather than three edits.
LIFECYCLE_FIELDS = (
    "parent_epic",
    "model_tier",
    "root_cause",
    "lifecycle_contract",
    "escalation",
)

# The epic form owes every lifecycle field EXCEPT the parent edge: an epic is a
# chain head, so it has no parent to name. This is the whole of the exemption.
EPIC_EXEMPT = {"parent_epic"}

# One contract per governed form, keyed by the form's kind. The validator is
# shared, so adding a fourth form is a row here and nothing else — and every
# row is validated by name.
#
#   CORE         the required field ids: each must exist and be required: true
#   ALSO         declared by the brief; presence enforced, requiredness is not
#   VOCAB        field id -> the exact canonical option set
#   MUST_MENTION field id -> a path its *guidance* must name, and which must
#                exist on disk, so a governed form points at a real artifact
#
# The #1533 lifecycle fields are spliced into each CORE from LIFECYCLE_FIELDS
# rather than retyped, so the three forms cannot drift apart from each other.
CONTRACTS = {
    # The dispatcher brief (issue #165). Its #165 field ids and its FinOps
    # vocabularies are UNCHANGED by #622/#1533: these are the fields the
    # dispatcher and the FinOps block read out of every brief.
    "fleet-task": (
        [
            "role",
            "epic",
            "lane",
            "model-tier",
            "thinking-effort",
            "verify-command",
            "chain-markers",
            *LIFECYCLE_FIELDS,
        ],
        # Declared by the issue's scope; presence is enforced, requiredness is not.
        ["pillar", "acceptance-criteria", "evidence"],
        # Canonical harvested vocabularies: tier names from the FinOps tier
        # policy and the role-effort ladder (flash/pro/auditor;
        # none/low/medium/high). A rename in either place is drift this gate
        # must catch. `model_tier` is the SEPARATE escalation ladder (#1533),
        # which is why this form carries both a dashed and an underscored tier
        # field — two vocabularies, not a duplicate.
        {
            "role": {"brain", "sister", "subagent"},
            "model-tier": {"flash", "pro", "auditor"},
            "thinking-effort": {"none", "low", "medium", "high"},
            "model_tier": {"L0", "L1", "L2"},
        },
        {},
    ),
    # The epic brief (issue #622) — the EPIC #616 shape. It owes every lifecycle
    # field but the parent edge (EPIC_EXEMPT, asserted below).
    "epic": (
        [
            "objective",
            "measured-landscape",
            "children",
            "verification-contract",
            *[field for field in LIFECYCLE_FIELDS if field not in EPIC_EXEMPT],
        ],
        ["purpose"],
        {"model_tier": {"L0", "L1", "L2"}},
        {},
    ),
    # The defect brief (issue #622).
    "bug": (
        [
            "observed",
            "expected",
            "reproduction",
            "severity",
            "rca",
            *LIFECYCLE_FIELDS,
        ],
        [],
        # Harvested from the isolation triage model: every Severity in
        # guardrails/isolation/model.py maps to a triage action, so a defect
        # form that invents a rung cannot be triaged.
        {
            "severity": {"critical", "high", "medium", "low"},
            "model_tier": {"L0", "L1", "L2"},
        },
        # A defect's RCA link must point at the ledger it feeds.
        {"rca": "governance/lessons/"},
    ),
}

# The exemption is asserted, not trusted: the epic form must omit exactly
# EPIC_EXEMPT and nothing more. A gate whose own declaration has drifted from
# the contract it describes is broken rather than merely red, so this is
# CANNOT-ASSESS (2) — never a pass.
_epic_omitted = set(LIFECYCLE_FIELDS) - set(CONTRACTS["epic"][0])
if _epic_omitted != EPIC_EXEMPT:
    print(
        "check-issue-template: CANNOT-ASSESS - the gate's own contract is inconsistent: "
        f"the epic form omits {sorted(_epic_omitted)}, expected {sorted(EPIC_EXEMPT)}",
        file=sys.stderr,
    )
    raise SystemExit(2)

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

# A `checkboxes` element renders its options from `attributes.options`, so one
# with no options is an acknowledgement nobody can give while still satisfying
# the required-field check above — the exact false-green this gate exists to
# refuse (#1533).
for field_id, item in elements.items():
    if item.get("type") != "checkboxes":
        continue
    attributes = item.get("attributes")
    options = attributes.get("options") if isinstance(attributes, dict) else None
    if not isinstance(options, list) or not options:
        findings.append(
            f"{path}: checkboxes field '{field_id}' has no options, so it renders nothing to acknowledge"
        )
        continue
    for index, option in enumerate(options, start=1):
        if not isinstance(option, dict) or not str(option.get("label", "")).strip():
            findings.append(f"{path}: checkboxes field '{field_id}' option {index} has no label")

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

# has_text <haystack> <needle> — bash-native containment, deliberately not a
# pipe into a quiet `grep`. A quiet grep exits on its first match, the producer
# then dies of SIGPIPE, and `set -o pipefail` promotes that 141 to the status of
# the whole pipeline — so a needle that IS present reads as ABSENT once the
# report passes the 64 KiB pipe buffer. That is the false verdict
# scripts/check-verdict-contains.sh exists to refuse.
has_text() {
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# validate_config <path> — the form selector must really disable blank issues,
# or the three forms above are optional rather than binding (#1533).
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate_config() {
  python3 - "$1" <<'PY'
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    print(f"check-issue-template: CANNOT-ASSESS - PyYAML not installed ({exc})", file=sys.stderr)
    raise SystemExit(2)

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
except OSError as exc:
    print(f"check-issue-template: FAIL - cannot read {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
except yaml.YAMLError as exc:
    print(f"check-issue-template: FAIL - {path} is not valid YAML: {exc}", file=sys.stderr)
    raise SystemExit(1)

findings = []
if not isinstance(config, dict):
    findings.append(f"{path}: top level is not a mapping (not an issue-form config)")
    config = {}

# `is not False` rather than a falsy test: an ABSENT key must not read as its own
# answer, and the string "false" must not read as the boolean false.
if config.get("blank_issues_enabled") is not False:
    findings.append(
        f"{path}: blank_issues_enabled is {config.get('blank_issues_enabled')!r}, not false "
        "- GitHub still offers a blank issue, which bypasses every governed form"
    )
if "contact_links" not in config:
    findings.append(f"{path}: contact_links is missing (declare it, even as an empty list)")

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(f"  OK    {path}: blank issues are disabled, so the governed forms are the only way in")
raise SystemExit(0)
PY
}

# control_config <file> — flip blank_issues_enabled to true in a copy and require
# the validator to refuse it BY NAME. 0 OK / 1 FAIL / 2 CANNOT-ASSESS.
control_config() {
  ctl_file="$1"

  if ! has_text "$(cat "$ctl_file")" "blank_issues_enabled: false"; then
    echo "check-issue-template: CANNOT-ASSESS — $ctl_file has no 'blank_issues_enabled: false' line for the control to flip" >&2
    return 2
  fi

  ctl_work="/tmp/issue-template-gate.config.$(date +%s%N).$$"
  if ! mkdir "$ctl_work" 2>/dev/null; then
    echo "check-issue-template: CANNOT-ASSESS — cannot create scratch dir $ctl_work" >&2
    return 2
  fi
  sed 's/^blank_issues_enabled: false$/blank_issues_enabled: true/' "$ctl_file" > "$ctl_work/mutant.yml"

  # A control whose mutation never landed proves nothing, so the mutant is
  # required to differ from the original before it is worth validating.
  ctl_before="$(sha256sum "$ctl_file" | cut -d' ' -f1)"
  ctl_after="$(sha256sum "$ctl_work/mutant.yml" | cut -d' ' -f1)"
  if [ "$ctl_before" = "$ctl_after" ]; then
    rm -rf "$ctl_work"
    echo "check-issue-template: FAIL — negative control for $ctl_file never landed: the mutant is byte-identical to the original" >&2
    return 1
  fi

  ctl_out="$(validate_config "$ctl_work/mutant.yml" 2>&1)"
  ctl_rc=$?
  rm -rf "$ctl_work"

  if [ "$ctl_rc" -eq 2 ]; then
    echo "check-issue-template: CANNOT-ASSESS — the config control could not be assessed" >&2
    printf '%s\n' "$ctl_out" >&2
    return 2
  fi
  if [ "$ctl_rc" -ne 1 ]; then
    echo "check-issue-template: FAIL — negative control passed; a config with blank issues ENABLED was not refused (the gate cannot fail)" >&2
    return 1
  fi
  if ! has_text "$ctl_out" "blank_issues_enabled"; then
    echo "check-issue-template: FAIL — the config control was refused, but blank_issues_enabled was not named" >&2
    printf '%s\n' "$ctl_out" >&2
    return 1
  fi

  echo "  OK    negative control: blank issues ENABLED is refused and named"
  return 0
}

# pr_shape <path> — the merge contract's own shape: the auto-close line and the
# two evidence sub-headings (#1533). 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
pr_shape() {
  python3 - "$1" <<'PY'
import re
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
except OSError as exc:
    print(f"check-issue-template: FAIL - cannot read {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)

# HTML comments are stripped first: a requirement merely NAMED inside the
# template's own guidance comment is not one the PR author is shown, and the
# controls below mutate exactly the shapes that remain.
stripped = re.sub(r"(?s)<!--.*?-->", "", text)

findings = []
if not re.search(r"(?m)^Closes[ \t]+#", stripped):
    findings.append(
        f"{path}: no line matching 'Closes #<n>' - a merged PR could not auto-close its issue"
    )
for heading in ("Acceptance output", "Negative-control output"):
    if not re.search(r"(?m)^###[ \t]+" + re.escape(heading) + r"[ \t]*$", stripped):
        findings.append(f"{path}: no '### {heading}' section - the evidence block is shape-only")

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(f"  OK    {path}: the 'Closes #<n>' line and both evidence sub-headings are present")
raise SystemExit(0)
PY
}

# control_pr <file> — one control per required shape: drop each from a copy and
# require the validator to refuse it BY NAME. A requirement nothing refuses is a
# comment, not a contract. 0 OK / 1 FAIL / 2 CANNOT-ASSESS.
control_pr() {
  pr_ctl_file="$1"
  pr_ctl_fail=0

  for pr_ctl_case in "Closes #<n>|Closes #<n>" "### Acceptance output|Acceptance output" "### Negative-control output|Negative-control output"; do
    pr_ctl_line="${pr_ctl_case%%|*}"
    pr_ctl_name="${pr_ctl_case##*|}"

    if ! grep -qxF -- "$pr_ctl_line" "$pr_ctl_file"; then
      echo "check-issue-template: CANNOT-ASSESS — $pr_ctl_file has no exact '$pr_ctl_line' line for the control to drop" >&2
      return 2
    fi

    pr_ctl_work="/tmp/issue-template-gate.pr.$(printf '%s' "$pr_ctl_name" | tr -c 'A-Za-z0-9' '_').$(date +%s%N).$$"
    if ! mkdir "$pr_ctl_work" 2>/dev/null; then
      echo "check-issue-template: CANNOT-ASSESS — cannot create scratch dir $pr_ctl_work" >&2
      return 2
    fi
    grep -vxF -- "$pr_ctl_line" "$pr_ctl_file" > "$pr_ctl_work/mutant.md"

    pr_ctl_before="$(sha256sum "$pr_ctl_file" | cut -d' ' -f1)"
    pr_ctl_after="$(sha256sum "$pr_ctl_work/mutant.md" | cut -d' ' -f1)"
    if [ "$pr_ctl_before" = "$pr_ctl_after" ]; then
      rm -rf "$pr_ctl_work"
      echo "check-issue-template: FAIL — negative control for '$pr_ctl_line' never landed: the mutant is byte-identical to the original" >&2
      return 1
    fi

    pr_ctl_out="$(pr_shape "$pr_ctl_work/mutant.md" 2>&1)"
    pr_ctl_rc=$?
    rm -rf "$pr_ctl_work"

    if [ "$pr_ctl_rc" -eq 2 ]; then
      echo "check-issue-template: CANNOT-ASSESS — the PR-template control for '$pr_ctl_line' could not be assessed" >&2
      printf '%s\n' "$pr_ctl_out" >&2
      return 2
    fi
    if [ "$pr_ctl_rc" -ne 1 ]; then
      echo "check-issue-template: FAIL — negative control passed; the PR template without '$pr_ctl_line' was not refused (the gate cannot fail)" >&2
      pr_ctl_fail=1
      continue
    fi
    if ! has_text "$pr_ctl_out" "$pr_ctl_name"; then
      echo "check-issue-template: FAIL — the PR-template control for '$pr_ctl_line' was refused, but '$pr_ctl_name' was not named" >&2
      printf '%s\n' "$pr_ctl_out" >&2
      pr_ctl_fail=1
      continue
    fi

    echo "  OK    negative control: the PR template without '$pr_ctl_line' is refused and named"
  done

  return "$pr_ctl_fail"
}

# Every governed form, by name, with the required fields its negative controls
# drop. Each control field is one the brief genuinely cannot do without, so a
# form that loses it is broken rather than merely different — and the lifecycle
# fields are each controlled because each was added by #1533, so an unrefused
# field would be one nobody notices the loss of.
fail=0
while read -r file kind fields; do
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

  # The comma list is split with `tr` BEFORE the loop (the shape
  # docs/SHELL-PATTERNS.md SP-3 prescribes), never by pointing IFS at a `read`.
  for field in $(printf '%s' "$fields" | tr ',' ' '); do
    rc=0
    control "$file" "$kind" "$field" || rc=$?
    case "$rc" in
      0) : ;;
      1) fail=1 ;;
      *) exit 2 ;;
    esac
  done
done <<'FORMS'
.github/ISSUE_TEMPLATE/fleet-task.yml fleet-task role,parent_epic,model_tier,root_cause,lifecycle_contract,escalation
.github/ISSUE_TEMPLATE/epic.yml epic objective,model_tier,root_cause,lifecycle_contract,escalation
.github/ISSUE_TEMPLATE/bug.yml bug severity,parent_epic,model_tier,root_cause,lifecycle_contract,escalation
FORMS

# config.yml — the form selector. Without it GitHub still offers a blank issue,
# which routes around every required field above (#1533).
config_file=".github/ISSUE_TEMPLATE/config.yml"
if [ ! -f "$config_file" ]; then
  echo "check-issue-template: FAIL — $config_file is missing (blank issues are not disabled)" >&2
  fail=1
else
  rc=0
  validate_config "$config_file" || rc=$?
  case "$rc" in
    0) : ;;
    1)
      echo "check-issue-template: FAIL — $config_file does not disable blank issues" >&2
      fail=1
      ;;
    *) exit 2 ;;
  esac
  if [ "$rc" -eq 0 ]; then
    rc=0
    control_config "$config_file" || rc=$?
    case "$rc" in
      0) : ;;
      1) fail=1 ;;
      *) exit 2 ;;
    esac
  fi
fi

# PULL_REQUEST_TEMPLATE.md — the merge contract's shape: the auto-close line and
# the two evidence sub-headings, each with its own control (#1533).
pr_file=".github/PULL_REQUEST_TEMPLATE.md"
if [ ! -f "$pr_file" ]; then
  echo "check-issue-template: FAIL — $pr_file is missing (the merge contract has no shape)" >&2
  fail=1
else
  rc=0
  pr_shape "$pr_file" || rc=$?
  case "$rc" in
    0) : ;;
    1)
      echo "check-issue-template: FAIL — $pr_file does not carry the merge-contract shape" >&2
      fail=1
      ;;
    *) exit 2 ;;
  esac
  if [ "$rc" -eq 0 ]; then
    rc=0
    control_pr "$pr_file" || rc=$?
    case "$rc" in
      0) : ;;
      1) fail=1 ;;
      *) exit 2 ;;
    esac
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "check-issue-template: FAIL — a governed brief artifact violates its contract" >&2
  exit 1
fi

echo "check-issue-template: OK — the fleet-task, epic and bug brief contracts, the blank-issue door and the PR-template shape are enforced; every negative control refused"
exit 0
