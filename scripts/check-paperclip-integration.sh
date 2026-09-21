#!/usr/bin/env bash
# check-paperclip-integration.sh — the paperclip-ing integration seam gate (M26, #370; ticket contract v2, #400).
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
# Ticket contract v2 (#400, ADR-0014) — additive. The ticket is the single join
# node; v2 adds kind, a CLOSED facets set and authority{} (one writer per field).
# The gate now also:
#   * requires ADR-0014 to exist, be `accepted`, carry every required
#     front-matter key and the Status/Context/Decision/Consequences sections, and
#     be indexed in the ADR index;
#   * requires the ticket schema to be a valid v2 contract: an unchanged v1
#     `required` set (so a v1 document still validates), `kind` with the six-kind
#     enum, a closed `facets` set, an `authority{}` map that pins one writer per
#     field with `const`, and an additive `evidence.items` (v1 string OR v2
#     receipt);
#   * validates a canonical v2 fixture against the contract and REFUSES, naming
#     the field, a contract that (a) leaves a populated field with no declared
#     writer, (b) gives one field two writers, (c) carries an unknown facet, or
#     (d) lets a facet contradict its declared authority. Each refusal is
#     provoked in-memory by a self-mutation, so a check that cannot fail fails
#     this gate (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-integration.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

adr="docs/decision-records/ADR-0013-paperclip-ing-integration.md"
adr_v2="docs/decision-records/ADR-0014-ticket-single-join-node-contract-v2.md"
adr_index="docs/decision-records/README.md"
doc="docs/PAPERCLIP-ING-INTEGRATION.md"
ticket_schema="docs/contracts/paperclip/ticket.schema.json"
ticket_v2_fixture="docs/contracts/paperclip/ticket.example.json"
ticket_v1_fixture="docs/contracts/paperclip/ticket.example.v1.json"
declare -a schemas=(
  "docs/contracts/paperclip/heartbeat.schema.json"
  "docs/contracts/paperclip/ticket.schema.json"
  "docs/contracts/paperclip/budget.schema.json"
)

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-integration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required_file in "$adr" "$adr_v2" "$adr_index" "$doc" "$ticket_v2_fixture" \
                    "$ticket_v1_fixture" "${schemas[@]}"; do
  if [ ! -f "$required_file" ]; then
    echo "check-paperclip-integration: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

# validate <adr> <doc> <index> <adr-v2> <schema...> — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$@" <<'PY'
import json
import pathlib
import re
import sys

adr_path = pathlib.Path(sys.argv[1])
doc_path = pathlib.Path(sys.argv[2])
index_path = pathlib.Path(sys.argv[3])
adr_v2_path = pathlib.Path(sys.argv[4])
schema_paths = [pathlib.Path(p) for p in sys.argv[5:]]

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

# --- ADR-0014 (ticket as the single join node, contract v2) ------------------
adr_v2 = read(adr_v2_path)
if adr_v2 is not None:
    lines = adr_v2.splitlines()
    if not lines or lines[0].strip() != "---":
        findings.append(f"{adr_v2_path}: missing front-matter fence")
        fm_v2 = ""
    else:
        try:
            end = lines.index("---", 1)
        except ValueError:
            findings.append(f"{adr_v2_path}: unterminated front-matter fence")
            fm_v2 = ""
        else:
            fm_v2 = "\n".join(lines[1:end])
    for key in ("id", "status", "date", "deciders", "req"):
        if not re.search(rf"^{key}\s*:", fm_v2, re.MULTILINE):
            findings.append(f"{adr_v2_path}: front-matter key '{key}' is missing")
    status_v2 = re.search(r"^status\s*:\s*(\S+)\s*$", fm_v2, re.MULTILINE)
    if not status_v2:
        findings.append(f"{adr_v2_path}: front-matter 'status' has no value")
    elif status_v2.group(1) != "accepted":
        findings.append(f"{adr_v2_path}: status is '{status_v2.group(1)}', not 'accepted'")
    for section in ("## Status", "## Context", "## Decision", "## Consequences"):
        if not re.search(rf"^{re.escape(section)}\s*$", adr_v2, re.MULTILINE):
            findings.append(f"{adr_v2_path}: section '{section}' is missing")
    decision_v2 = re.search(r"^## Decision\s*$(.*?)(?=^## |\Z)", adr_v2, re.MULTILINE | re.DOTALL)
    if decision_v2 is None:
        findings.append(f"{adr_v2_path}: cannot read the Decision section")
    else:
        text_v2 = decision_v2.group(1)
        for token in ("join", "authority", "projection"):
            if not re.search(rf"{token}", text_v2, re.IGNORECASE):
                findings.append(f"{adr_v2_path}: Decision does not name the '{token}'")

if index is not None and "ADR-0014" not in index:
    findings.append(f"{index_path}: no index row for ADR-0014")

if index is not None and not re.search(r"ADR-0014-ticket-single-join-node-contract-v2\.md", index):
    findings.append(f"{index_path}: index row for ADR-0014 does not link the file")

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

    # --- ticket contract v2 (ADR-0014, #400) --------------------------------
    if kind == "ticket":
        required_v1 = ["id", "owner", "status", "blocked_by", "goal", "evidence"]
        if required != required_v1:
            findings.append(
                f"{schema_path}: 'required' is {required}, not the v1 key set "
                f"{required_v1} — v2 is additive and a v1 document must still validate"
            )
        if schema.get("additionalProperties") is not False:
            findings.append(f"{schema_path}: 'additionalProperties' must be false")
        for added in ("kind", "facets", "authority"):
            if added not in properties:
                findings.append(f"{schema_path}: v2 field '{added}' is missing from properties")
            if doc_text and added not in doc_text:
                findings.append(
                    f"{doc_path}: seam doc does not declare v2 field '{added}' for ticket"
                )
        kind_enum = (properties.get("kind") or {}).get("enum")
        expected_kinds = ["task", "incident", "rca", "corrective-action",
                          "lesson", "suggestion"]
        if kind_enum != expected_kinds:
            findings.append(
                f"{schema_path}: 'kind' enum is {kind_enum}, expected {expected_kinds}"
            )
        facets_prop = properties.get("facets") or {}
        if facets_prop.get("additionalProperties") is not False:
            findings.append(
                f"{schema_path}: 'facets' must be closed (additionalProperties: false)"
            )
        facet_keys = sorted((facets_prop.get("properties") or {}).keys())
        expected_facets = ["budget", "lessons", "raid"]
        if facet_keys != expected_facets:
            findings.append(
                f"{schema_path}: 'facets' keys are {facet_keys}, expected the "
                f"closed set {expected_facets}"
            )
        authority_prop = properties.get("authority") or {}
        if authority_prop.get("additionalProperties") is not False:
            findings.append(
                f"{schema_path}: 'authority' must be closed (additionalProperties: false)"
            )
        auth_props = authority_prop.get("properties") or {}
        auth_fields = sorted(auth_props.keys())
        expected_auth = ["blocked_by", "facets.budget", "facets.lessons",
                         "facets.raid", "goal", "owner", "status"]
        if auth_fields != expected_auth:
            findings.append(
                f"{schema_path}: 'authority' fields are {auth_fields}, expected the "
                f"one-writer set {expected_auth}"
            )
        for afield, spec in auth_props.items():
            if not isinstance(spec, dict) or "const" not in spec:
                findings.append(
                    f"{schema_path}: authority field '{afield}' must pin its single "
                    "writer with 'const'"
                )
        ev_items = (properties.get("evidence") or {}).get("items") or {}
        branches = ev_items.get("anyOf")
        if not isinstance(branches, list):
            findings.append(
                f"{schema_path}: 'evidence.items' must offer the v1 string form and "
                "the v2 receipt form (anyOf)"
            )
        elif not any(isinstance(b, dict) and b.get("type") == "string" for b in branches):
            findings.append(
                f"{schema_path}: 'evidence.items' drops the v1 string form (v2 must be additive)"
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
    validate "$1" "$doc" "$adr_index" "$adr_v2" "${schemas[@]}"
  else
    validate "$adr" "$doc" "$adr_index" "$adr_v2" "${schemas[@]}"
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
work="/tmp/ao400-contract.$$.$(date +%s)"
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
else
  echo "check-paperclip-integration: FAIL — negative control passed; a stripped Decision was not caught (the gate cannot fail)" >&2
  printf '%s\n' "$mutant_out" >&2
  exit 1
fi

# Ticket contract v2 fixture: validate the canonical document against the v2
# contract, then provoke each of the four refusals in-memory (the mutations are
# deep copies — the fixture on disk is never written) and assert the gate names
# the offending field every time. A refusal that cannot fire fails this gate.
v2_out="$(python3 - "$ticket_schema" "$ticket_v2_fixture" "$ticket_v1_fixture" <<'PY'
import copy
import hashlib
import json
import pathlib
import sys

schema_path = pathlib.Path(sys.argv[1])
v2_path = pathlib.Path(sys.argv[2])
v1_path = pathlib.Path(sys.argv[3])


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


schema = load(schema_path)
v2 = load(v2_path)
v1 = load(v1_path)

# The frozen contract, derived from the schema itself (single source of truth).
canonical = {k: v.get("const")
             for k, v in schema["properties"]["authority"]["properties"].items()}
facet_set = sorted(schema["properties"]["facets"]["properties"].keys())


def populated(value):
    if value is None:
        return False
    if isinstance(value, (str, list, dict)) and len(value) == 0:
        return False
    return True


def check(doc):
    """Return the contract violations in `doc`; empty == contract-valid."""
    found = []
    facets = doc.get("facets") or {}
    if not isinstance(facets, dict):
        found.append("ticket fixture: 'facets' is not an object")
        facets = {}
    # (c) unknown facet — the set is closed.
    for name in sorted(facets):
        if name not in facet_set:
            found.append(
                f"ticket fixture: unknown facet '{name}' "
                f"(closed set: {', '.join(facet_set)})"
            )
    authority = doc.get("authority")
    if not isinstance(authority, dict):
        found.append(
            "ticket fixture: 'authority' is missing or not an object "
            "(the v2 contract requires it)"
        )
        authority = {}
    # (b) a field with two writers.
    for field, writer in authority.items():
        if isinstance(writer, list) and len(writer) != 1:
            found.append(
                f"ticket fixture: field '{field}' has {len(writer)} writers "
                "(authority must name exactly one)"
            )
    # (a) a populated field with no declared writer; (d) a facet contradicting
    # its declared authority.
    for field, producer in canonical.items():
        if field.startswith("facets."):
            present = field.split(".", 1)[1] in facets
        else:
            present = field in doc and populated(doc[field])
        if not present:
            continue
        if field not in authority:
            found.append(
                f"ticket fixture: field '{field}' is populated but has no "
                "declared writer in authority{}"
            )
            continue
        writer = authority[field]
        if isinstance(writer, list):
            writer = writer[0] if len(writer) == 1 else None
        if writer is not None and writer != producer:
            label = (f"facet '{field.split('.', 1)[1]}'"
                     if field.startswith("facets.") else f"field '{field}'")
            found.append(
                f"ticket fixture: {label} contradicts its declared authority "
                f"(expected '{producer}', got '{writer}')"
            )
    for field in sorted(authority):
        if field not in canonical:
            found.append(f"ticket fixture: authority names unknown field '{field}'")
    return found


problems = []

# 0. The canonical v2 fixture must satisfy the contract. If it does not, report
#    and stop: the in-memory controls below assume a contract-valid base.
base = check(v2)
if base:
    for finding in base:
        print(f"  FAIL  canonical v2 fixture violates its own contract: {finding}")
    raise SystemExit(1)

# 1. The v1 fixture must be a genuine v1 document (exactly the six v1 keys).
v1_keys = sorted(v1.keys())
if v1_keys != ["blocked_by", "evidence", "goal", "id", "owner", "status"]:
    problems.append(f"v1 fixture keys are {v1_keys}, not the v1 key set")


# 2. Provoke each refusal; each must be caught and name the offending field.
def mut_no_writer(doc):
    del doc["authority"]["status"]
    return doc


def mut_two_writers(doc):
    doc["authority"]["owner"] = ["governance/isolation", "somewhere/else"]
    return doc


def mut_unknown_facet(doc):
    doc["facets"]["mystery"] = {"x": 1}
    return doc


def mut_facet_contradiction(doc):
    doc["authority"]["facets.lessons"] = "somewhere/else"
    return doc


mutations = [
    ("a populated field with no declared writer", mut_no_writer,
     "field 'status' is populated but has no declared writer"),
    ("a field with two writers", mut_two_writers,
     "field 'owner' has 2 writers"),
    ("an unknown facet", mut_unknown_facet, "unknown facet 'mystery'"),
    ("a facet contradicting its declared authority", mut_facet_contradiction,
     "facet 'lessons' contradicts its declared authority"),
]

before = hashlib.sha256(v2_path.read_bytes()).hexdigest()
for name, mutate, expected in mutations:
    mutant = copy.deepcopy(v2)
    mutate(mutant)
    found = check(mutant)
    if not found:
        problems.append(f"mutation NOT caught ({name}): the gate cannot fail")
    elif not any(expected in f for f in found):
        problems.append(
            f"mutation caught but not named as expected ({name}): "
            f"wanted '{expected}', got {found}"
        )
    else:
        print(f"  OK    refusal fires and names it: {name}")
after = hashlib.sha256(v2_path.read_bytes()).hexdigest()
if before != after:
    problems.append("the on-disk fixture changed during the in-memory controls")

if problems:
    for problem in problems:
        print(f"  FAIL  {problem}")
    raise SystemExit(1)
print(f"  OK    v2 contract valid; 4 refusals provoked (fixture sha256 {before[:12]} unchanged)")
raise SystemExit(0)
PY
)"
v2_rc=$?
printf '%s\n' "$v2_out"
if [ "$v2_rc" -ne 0 ]; then
  echo "check-paperclip-integration: FAIL — the v2 contract check refused (rc=$v2_rc)" >&2
  exit 1
fi

echo "check-paperclip-integration: OK — ADR-0013 + ADR-0014 + seam doc + 3 schemas + ticket contract v2 enforced, all mutants refused"
exit 0
