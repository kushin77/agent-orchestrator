#!/usr/bin/env bash
# check-paperclip-integration.sh — the paperclip-ing integration seam gate (M26, #370).
#
# ADR-0013 decides whether the operator surface for ai.purebliss.app embeds
# upstream Paperclip, forks it, or adopts its CLI over HTTP; and the seam doc
# freezes the heartbeat / ticket / budget contracts as JSON Schemas. A decided
# seam that nothing validates is a formality (no-false-green doctrine, GR-12), so
# this gate fails, by name, when any of it is missing:
#
#   * docs/decision-records/ADR-0013-*.md exists, carries every required
#     front-matter key, is `accepted`, declares the Status/Context/Decision/
#     Consequences sections, and its Decision names an integration mode;
#   * the ADR index carries a row for ADR-0013;
#   * docs/PAPERCLIP-ING-INTEGRATION.md exists and names all three contract kinds
#     (heartbeat, ticket, budget);
#   * each of the three schemas exists, parses as JSON, declares $schema/$id/
#     required, and declares the field names the seam doc names — so the doc and
#     the schemas cannot drift apart.
#
# It also runs its own negative control: it copies the ADR, strips the Decision
# section, asserts the mutation changed the text (and the sha256), and requires
# the validator to refuse the mutant. If the mutant passes, this gate reports
# FAIL — a check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-integration.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

adr="docs/decision-records/ADR-0013-paperclip-ing-integration.md"
adr_index="docs/decision-records/README.md"
doc="docs/PAPERCLIP-ING-INTEGRATION.md"
declare -a schemas=(
  "docs/contracts/paperclip/heartbeat.schema.json"
  "docs/contracts/paperclip/ticket.schema.json"
  "docs/contracts/paperclip/budget.schema.json"
)

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-integration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required_file in "$adr" "$adr_index" "$doc" "${schemas[@]}"; do
  if [ ! -f "$required_file" ]; then
    echo "check-paperclip-integration: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

# validate <adr> <doc> <index> <schema...> — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$@" <<'PY'
import json
import pathlib
import re
import sys

adr_path = pathlib.Path(sys.argv[1])
doc_path = pathlib.Path(sys.argv[2])
index_path = pathlib.Path(sys.argv[3])
schema_paths = [pathlib.Path(p) for p in sys.argv[4:]]

findings = []


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(f"{path}: cannot read ({exc})")
        return None


# --- ADR --------------------------------------------------------------------
adr = read(adr_path)
if adr is not None:
    # front-matter is the block between the first two '---' fences
    lines = adr.splitlines()
    if not lines or lines[0].strip() != "---":
        findings.append(f"{adr_path}: missing front-matter fence")
        fm_text = ""
    else:
        try:
            end = lines.index("---", 1)
        except ValueError:
            findings.append(f"{adr_path}: unterminated front-matter fence")
            fm_text = ""
        else:
            fm_text = "\n".join(lines[1:end])

    for key in ("id", "status", "date", "deciders", "req"):
        if not re.search(rf"^{key}\s*:", fm_text, re.MULTILINE):
            findings.append(f"{adr_path}: front-matter key '{key}' is missing")

    status = re.search(r"^status\s*:\s*(\S+)\s*$", fm_text, re.MULTILINE)
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
        modes = [m for m in ("embed", "fork", "CLI", "npx paperclipai")
                 if re.search(rf"\b{re.escape(m)}", text, re.IGNORECASE)]
        if not modes:
            findings.append(
                f"{adr_path}: Decision names no integration mode "
                "(expected one of: embed, fork, CLI)"
            )

# --- ADR index --------------------------------------------------------------
index = read(index_path)
if index is not None and "ADR-0013" not in index:
    findings.append(f"{index_path}: no index row for ADR-0013")

if index is not None and not re.search(r"ADR-0013-paperclip-ing-integration\.md", index):
    findings.append(f"{index_path}: index row for ADR-0013 does not link the file")

# --- seam doc ---------------------------------------------------------------
doc = read(doc_path)
if doc is not None:
    for kind in ("heartbeat", "ticket", "budget"):
        if kind not in doc.lower():
            findings.append(f"{doc_path}: does not name the '{kind}' contract")
    doc_text = doc
else:
    doc_text = ""

# --- schemas ----------------------------------------------------------------
# The field names the seam doc declares, per contract. The doc names these, so
# the schema must declare them too — doc and schema cannot drift apart.
EXPECTED = {
    "heartbeat": ["agent_id", "session_id", "tick", "cadence_seconds", "wake", "outcome", "ts"],
    "ticket": ["id", "owner", "status", "blocked_by", "goal", "evidence"],
    "budget": ["scope", "period", "cap", "spent", "currency", "hard_stop",
               "burn_rate_alert_pct", "receipt_ref"],
}

for schema_path in schema_paths:
    text = read(schema_path)
    if text is None:
        continue
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as exc:
        findings.append(f"{schema_path}: is not valid JSON ({exc})")
        continue
    if not isinstance(schema, dict):
        findings.append(f"{schema_path}: top level is not a JSON object")
        continue
    for key in ("$schema", "$id", "title", "type", "required", "properties"):
        if key not in schema:
            findings.append(f"{schema_path}: missing '{key}'")
    required = schema.get("required")
    if not isinstance(required, list):
        findings.append(f"{schema_path}: 'required' is not a list")
        required = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        findings.append(f"{schema_path}: 'properties' is not an object")
        properties = {}
    kind = next((k for k in EXPECTED if k in schema_path.name), None)
    if kind is None:
        findings.append(f"{schema_path}: does not name a known contract kind")
        continue
    for field in EXPECTED[kind]:
        if field not in properties:
            findings.append(f"{schema_path}: property '{field}' is missing")
        if field not in required:
            findings.append(f"{schema_path}: field '{field}' is not required")
        if doc_text and field not in doc_text:
            findings.append(
                f"{doc_path}: seam doc does not declare field '{field}' for {kind}"
            )

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)

print("  OK    ADR-0013 decision + index row + seam doc + 3 schemas all consistent")
raise SystemExit(0)
PY
}

# validate_full [adr] — run the validator over the real tree, or over a mutant
# ADR when a first argument is supplied. 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate_full() {
  if [ "$#" -gt 0 ]; then
    validate "$1" "$doc" "$adr_index" "${schemas[@]}"
  else
    validate "$adr" "$doc" "$adr_index" "${schemas[@]}"
  fi
}

rc=0
validate_full || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-paperclip-integration: FAIL — the integration seam is not declared in full" >&2
    exit 1
    ;;
  *)
    echo "check-paperclip-integration: CANNOT-ASSESS — validator returned $rc" >&2
    exit 2
    ;;
esac

# Negative control: strip the Decision section from a copy of the ADR and require
# the validator to refuse it, naming the missing section.
work="/tmp/ao370.$$.$(date +%s)"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-paperclip-integration: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

mutant="$work/ADR-0013-no-decision.md"
python3 - "$adr" "$mutant" <<'PY'
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
# drop the '## Decision' section heading through to the next H2
mutated = re.sub(r"^## Decision\s*$.*?(?=^## )", "", src, flags=re.MULTILINE | re.DOTALL)
open(sys.argv[2], "w", encoding="utf-8").write(mutated)
PY

mutant_changed="$(python3 - "$adr" "$mutant" <<'PY'
import hashlib
import sys

a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
ha, hb = hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()
print("CHANGED" if (a != b and ha != hb) else "SAME")
print(f"orig={ha}")
print(f"mutant={hb}")
PY
)"
if ! printf '%s\n' "$mutant_changed" | grep -q '^CHANGED$'; then
  echo "check-paperclip-integration: FAIL — negative-control mutation changed nothing" >&2
  printf '%s\n' "$mutant_changed" >&2
  exit 1
fi

mutant_out="$(validate_full "$mutant" 2>&1)"
mutant_rc=$?

if [ "$mutant_rc" -eq 1 ] && printf '%s\n' "$mutant_out" | grep -q "section '## Decision' is missing"; then
  printf '  OK    negative control: stripping the Decision section is refused and named\n'
  printf '%s\n' "$mutant_changed"
  echo "check-paperclip-integration: OK — ADR-0013 + seam doc + 3 schemas enforced, mutant refused"
  exit 0
fi

echo "check-paperclip-integration: FAIL — negative control passed; a stripped Decision was not caught (the gate cannot fail)" >&2
printf '%s\n' "$mutant_out" >&2
exit 1
