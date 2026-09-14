#!/usr/bin/env bash
# check-routing-seam.sh — the routing-seam contract gate (issue #426, ADR-0015).
#
# A decided seam that nothing validates is a formality (no-false-green
# doctrine, GR-12), so this gate enforces the frozen routing contract under
# docs/contracts/routing/ and fails, BY NAME, on the two ways a seam stops being
# a seam:
#
#   * a field with TWO producers — a second authority, i.e. half-coupling
#     (ADR-0012's worst outcome): refused, naming the field and both producers;
#   * a field with NO producer — unowned: refused, naming the field.
#
# It gathers the field vocabulary from the frozen schemas (the request inputs
# and the decision outputs) plus the declared policy fields, then requires the
# ownership map to name EXACTLY ONE producer for every field, and requires every
# declared side to be one the contract knows. It also validates the committed
# example instances against the real schemas with the jsonschema library, and
# requires ADR-0015 (index row + front-matter + sections + a Decision that names
# its disposition) to be present, so the record and the contract cannot drift.
#
# It runs its own negative control: it copies the ownership map, gives one field
# two producers, and requires the validator to refuse it naming the field. If the
# mutant passes, this gate reports FAIL — a check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS (an absent or unreadable contract) must never be reported as a
# pass, and never aggregated into one.
#
# Offline and deterministic: stdlib + PyYAML-free JSON + jsonschema only. No
# network. Usage: bash scripts/check-routing-seam.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

contract_dir="docs/contracts/routing"
ownership="$contract_dir/ownership.json"
request_schema="$contract_dir/routing-request.schema.json"
decision_schema="$contract_dir/routing-decision.schema.json"
policy_fields="$contract_dir/policy-fields.json"
request_example="$contract_dir/routing-request.example.json"
decision_example="$contract_dir/routing-decision.example.json"
decision_escalated_example="$contract_dir/routing-decision.escalated.example.json"
adr_index="docs/decision-records/README.md"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-routing-seam: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# --- contract presence ------------------------------------------------------
# The contract itself is what is being assessed: if it is absent or unreadable we
# cannot assess the seam, which is CANNOT-ASSESS (2) — never a pass.
contract_files=(
  "$ownership" "$request_schema" "$decision_schema" "$policy_fields"
  "$request_example" "$decision_example" "$decision_escalated_example"
)
for f in "${contract_files[@]}"; do
  if [ ! -f "$f" ]; then
    echo "check-routing-seam: CANNOT-ASSESS — contract file $f is missing" >&2
    exit 2
  fi
done

# ADR-0015 is the record the contract freezes; its absence is a defect (NOT-OK),
# not an unassessable contract.
adr_path="$(ls docs/decision-records/ADR-0015-*.md 2>/dev/null | head -n 1)"
if [ -z "$adr_path" ]; then
  echo "check-routing-seam: FAIL — no docs/decision-records/ADR-0015-*.md found" >&2
  exit 1
fi

# validate <ownership> <req-schema> <dec-schema> <policy-fields> <req-ex> <dec-ex>
#          <dec-esc-ex> <adr> <index>  -> 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$@" <<'PY'
import json
import pathlib
import re
import sys

own_path = pathlib.Path(sys.argv[1])
req_path = pathlib.Path(sys.argv[2])
dec_path = pathlib.Path(sys.argv[3])
pol_path = pathlib.Path(sys.argv[4])
req_example = pathlib.Path(sys.argv[5])
dec_example = pathlib.Path(sys.argv[6])
dec_escalated_example = pathlib.Path(sys.argv[7])
adr_path = pathlib.Path(sys.argv[8])
index_path = pathlib.Path(sys.argv[9])

findings = []
cannot_assess = []


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        cannot_assess.append(f"{path}: unreadable ({exc})")
        return None


def load_json(path):
    text = read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        findings.append(f"{path}: invalid JSON ({exc})")
        return None


ownership = load_json(own_path)
request_schema = load_json(req_path)
decision_schema = load_json(dec_path)
policy_fields = load_json(pol_path)
request_instance = load_json(req_example)
decision_instance = load_json(dec_example)
decision_escalated_instance = load_json(dec_escalated_example)
adr = read_text(adr_path)
index = read_text(index_path)

# --- schema shape -----------------------------------------------------------
def check_schema(schema, path, label):
    if schema is None:
        return
    if not isinstance(schema, dict):
        findings.append(f"{path}: top level is not a JSON object")
        return
    for key in ("$schema", "$id", "title", "type", "required", "properties",
                "additionalProperties"):
        if key not in schema:
            findings.append(f"{path} ({label} schema): missing '{key}'")
    if schema.get("additionalProperties") is not False:
        findings.append(
            f"{path} ({label} schema): 'additionalProperties' must be false "
            "(the seam is closed, not open)"
        )
    if not isinstance(schema.get("properties"), dict):
        findings.append(f"{path} ({label} schema): 'properties' is not an object")
    if not isinstance(schema.get("required"), list):
        findings.append(f"{path} ({label} schema): 'required' is not a list")


check_schema(request_schema, req_path, "request")
check_schema(decision_schema, dec_path, "decision")

# --- field vocabulary -------------------------------------------------------
# Every field the contract declares, keyed <layer>.<field>. Request + decision
# come from the schemas; the policy fields come from policy-fields.json.
vocab = {}
if isinstance(request_schema, dict) and isinstance(request_schema.get("properties"), dict):
    for field in request_schema["properties"]:
        vocab[f"request.{field}"] = "request schema"
if isinstance(decision_schema, dict) and isinstance(decision_schema.get("properties"), dict):
    for field in decision_schema["properties"]:
        vocab[f"decision.{field}"] = "decision schema"
if isinstance(policy_fields, dict) and isinstance(policy_fields.get("policy_fields"), dict):
    for field in policy_fields["policy_fields"]:
        vocab[f"policy.{field}"] = "policy-fields vocabulary"
else:
    findings.append(f"{pol_path}: no 'policy_fields' object")

if not vocab:
    findings.append("the contract declares no fields — nothing to own")

# --- one writer per field ---------------------------------------------------
sides = {}
fields = {}
if isinstance(ownership, dict):
    if isinstance(ownership.get("sides"), dict):
        sides = ownership["sides"]
    else:
        findings.append(f"{own_path}: no 'sides' object")
    if isinstance(ownership.get("fields"), dict):
        fields = ownership["fields"]
    else:
        findings.append(f"{own_path}: no 'fields' object")
else:
    findings.append(f"{own_path}: top level is not a JSON object")

for key in sorted(vocab):
    entry = fields.get(key)
    if not isinstance(entry, dict):
        findings.append(
            f"field '{key}' has no declared producer (absent from {own_path})"
        )
        continue
    producers = entry.get("producers")
    if not isinstance(producers, list) or not producers:
        findings.append(f"field '{key}' has no declared producer")
        continue
    if len(producers) > 1:
        names = ", ".join(str(p) for p in producers)
        findings.append(
            f"field '{key}' declares {len(producers)} producers ({names}) "
            "— one writer per field (half-coupling)"
        )
        continue
    only = producers[0]
    if sides and only not in sides:
        findings.append(
            f"field '{key}' names producer '{only}', which is not a declared side "
            f"({', '.join(sorted(sides))})"
        )

for key in sorted(fields):
    if key not in vocab:
        findings.append(
            f"field '{key}' in the ownership map is not in the contract vocabulary "
            "(stale entry)"
        )

# --- example instances vs the real schemas ----------------------------------
try:
    import jsonschema
except ImportError:
    cannot_assess.append(
        "jsonschema is not importable — the contract instances cannot be validated"
    )
else:
    for path, instance, schema, label in (
        (req_example, request_instance, request_schema, "request"),
        (dec_example, decision_instance, decision_schema, "decision"),
        (dec_escalated_example, decision_escalated_instance, decision_schema, "decision"),
    ):
        if instance is None or schema is None:
            continue
        try:
            jsonschema.validate(instance, schema)
        except jsonschema.ValidationError as exc:
            findings.append(
                f"{path}: does not validate against the {label} schema ({exc.message})"
            )
        except jsonschema.SchemaError as exc:
            findings.append(f"{schema.get('$id', label)}: not a usable schema ({exc.message})")

# --- ADR-0015 ---------------------------------------------------------------
if adr is not None:
    lines = adr.splitlines()
    if not lines or lines[0].strip() != "---":
        findings.append(f"{adr_path}: missing front-matter fence")
        front_matter = ""
    else:
        try:
            end = lines.index("---", 1)
        except ValueError:
            findings.append(f"{adr_path}: unterminated front-matter fence")
            front_matter = ""
        else:
            front_matter = "\n".join(lines[1:end])

    for key in ("id", "status", "date", "deciders", "req"):
        if not re.search(rf"^{key}\s*:", front_matter, re.MULTILINE):
            findings.append(f"{adr_path}: front-matter key '{key}' is missing")

    status = re.search(r"^status\s*:\s*(\S+)\s*$", front_matter, re.MULTILINE)
    if not status:
        findings.append(f"{adr_path}: front-matter 'status' has no value")
    elif status.group(1) != "accepted":
        findings.append(f"{adr_path}: status is '{status.group(1)}', not 'accepted'")

    for section in ("## Status", "## Context", "## Decision", "## Consequences"):
        if not re.search(rf"^{re.escape(section)}\s*$", adr, re.MULTILINE):
            findings.append(f"{adr_path}: section '{section}' is missing")

    decision = re.search(r"^## Decision\s*$(.*?)(?=^## |\Z)", adr, re.MULTILINE | re.DOTALL)
    if decision is None:
        findings.append(f"{adr_path}: cannot read the Decision section")
    else:
        text = decision.group(1)
        dispositions = [d for d in ("library we consume", "policy source we map",
                                    "not adopted") if re.search(re.escape(d), text, re.I)]
        if not dispositions:
            findings.append(
                f"{adr_path}: Decision names no disposition "
                "(expected one of: library we consume / policy source we map / not adopted)"
            )
        if not re.search(r"runtime", text, re.I):
            findings.append(f"{adr_path}: Decision does not address consuming the runtime")
        if not re.search(r"reject|do not consume|not consume", text, re.I):
            findings.append(
                f"{adr_path}: Decision neither chooses nor rejects consuming the runtime"
            )

if index is not None:
    if "ADR-0015" not in index:
        findings.append(f"{index_path}: no index row for ADR-0015")
    if not re.search(r"ADR-0015-routing-seam-single-authority\.md", index):
        findings.append(f"{index_path}: index row for ADR-0015 does not link the file")

# --- verdict ----------------------------------------------------------------
if cannot_assess:
    for finding in cannot_assess:
        print(f"  CANNOT-ASSESS  {finding}", file=sys.stderr)
    raise SystemExit(2)

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print(
    f"  OK    one writer for each of {len(vocab)} routing field(s); "
    "examples validate; ADR-0015 + index row consistent"
)
raise SystemExit(0)
PY
}

rc=0
validate "$ownership" "$request_schema" "$decision_schema" "$policy_fields" \
  "$request_example" "$decision_example" "$decision_escalated_example" \
  "$adr_path" "$adr_index" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-routing-seam: FAIL — the routing seam is not declared in full" >&2
    exit 1
    ;;
  2)
    echo "check-routing-seam: CANNOT-ASSESS — the contract could not be read" >&2
    exit 2
    ;;
  *)
    echo "check-routing-seam: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# --- internal negative control ----------------------------------------------
# Give one field two producers and require the validator to refuse it, naming
# the field. A gate that cannot fail is a formality (GR-12).
work="/tmp/ao426-routing-seam.$$.$(date +%s)"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-routing-seam: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

mutant="$work/ownership-mutant.json"
if ! python3 - "$ownership" "$mutant" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
# The seam-breaking mutation: a decision field with a second producer.
data["fields"]["decision.tier"]["producers"] = ["caller", "authority"]
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
then
  echo "check-routing-seam: CANNOT-ASSESS — could not build the negative control" >&2
  exit 2
fi

if ! python3 - "$ownership" "$mutant" <<'PY'
import hashlib
import sys

a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
if a == b or hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest():
    raise SystemExit(1)
PY
then
  echo "check-routing-seam: FAIL — negative-control mutation changed nothing" >&2
  exit 1
fi

mutant_out="$(validate "$mutant" "$request_schema" "$decision_schema" "$policy_fields" \
  "$request_example" "$decision_example" "$decision_escalated_example" \
  "$adr_path" "$adr_index" 2>&1)"
mutant_rc=$?

if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -q "field 'decision.tier' declares 2 producers"; then
  echo "  OK    negative control: a field with two producers is refused and named"
  echo "check-routing-seam: OK — one writer per routing field, refused by name when broken"
  exit 0
fi

echo "check-routing-seam: FAIL — negative control passed; a second producer was not refused by name" >&2
printf '%s\n' "$mutant_out" >&2
exit 1
