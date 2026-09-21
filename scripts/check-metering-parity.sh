#!/usr/bin/env bash
# check-metering-parity.sh — the metering/budget boundary contract gate (#425).
#
# One metering/budget record shape is frozen across the repo boundary before our
# budget adapter's receipt shape hardens into a third variant. A decided shape
# that nothing validates is a formality (no-false-green doctrine, GR-12), so
# this gate enforces the frozen contract under docs/contracts/metering/ and
# fails, BY NAME, on every way the boundary stops holding:
#
#   * an UNMAPPED field — a canonical field with no row in field-map.json: the
#     shape exists on one side and is silently dropped on the other;
#   * a field with NO DECLARED PRODUCER — the "record shape exists but nothing
#     produces it" seam (kushin77/deepseek#115): the field is unowned;
#   * a field with TWO producers — a second authority (half-coupling);
#   * a field that exists on ONE SIDE only and is not named — one_sided unset;
#   * a metering CLAIM whose evidence field is empty — a saving or a cap that
#     cannot be measured must not be claimed (kushin77/deepseek#106).
#
# It derives the field vocabulary from the frozen schema, requires field-map.json
# to name a row for every field and ownership.json to name EXACTLY ONE producer
# for every field, validates the committed example instances against the real
# schema with the jsonschema library, and refuses any claim or receipt whose
# evidence reference is empty.
#
# It then proves it can fail: it builds mutants in a scratch directory and
# requires each refusal by name — an unmapped field (rc 1), a field with no
# producer (rc 1), a claim with empty evidence (rc 1) and an absent input
# (rc 2). A control that passes is reported FAIL. A gate that cannot fail is a
# formality (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS (an absent or unreadable contract) must never be reported as a
# pass, and never aggregated into one.
#
# Offline and deterministic: stdlib + jsonschema only, no network, no clock in
# the output. Usage: bash scripts/check-metering-parity.sh
#
# ---knowledge---
# module_id: scripts.check-metering-parity
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, declared-authority, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#106", "#115", "#425"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

contract_dir="docs/contracts/metering"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-metering-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# --- contract presence ------------------------------------------------------
# The contract itself is what is being assessed: if it is absent or unreadable
# we cannot assess the boundary, which is CANNOT-ASSESS (2) — never a pass.
for f in \
  "$contract_dir/metering-record.schema.json" \
  "$contract_dir/field-map.json" \
  "$contract_dir/ownership.json" \
  "$contract_dir/metering-record.example.json"; do
  if [ ! -f "$f" ]; then
    echo "check-metering-parity: CANNOT-ASSESS — contract file $f is missing" >&2
    exit 2
  fi
done

# --- the validator ----------------------------------------------------------
# validate <contract-dir>  -> 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$1" <<'PY'
import json
import pathlib
import sys

contract = pathlib.Path(sys.argv[1])
schema_path = contract / "metering-record.schema.json"
field_map_path = contract / "field-map.json"
ownership_path = contract / "ownership.json"

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


schema = load_json(schema_path)
field_map = load_json(field_map_path)
ownership = load_json(ownership_path)

# --- schema shape -----------------------------------------------------------
if isinstance(schema, dict):
    for key in ("$schema", "$id", "title", "type", "required", "properties",
                "additionalProperties"):
        if key not in schema:
            findings.append(f"{schema_path}: missing '{key}'")
    if schema.get("additionalProperties") is not False:
        findings.append(
            f"{schema_path}: 'additionalProperties' must be false "
            "(the record shape is closed, not open)"
        )
elif schema is not None:
    findings.append(f"{schema_path}: top level is not a JSON object")

# --- field vocabulary (walk the schema properties one level deep) -----------
def walk(props, prefix=""):
    out = []
    for name, spec in props.items():
        dotted = f"{prefix}{name}"
        if isinstance(spec, dict) and isinstance(spec.get("properties"), dict):
            out.extend(walk(spec["properties"], f"{dotted}."))
        else:
            out.append(dotted)
    return out


vocab = {}
if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
    for field in walk(schema["properties"]):
        vocab[field] = "record schema"
if not vocab:
    findings.append("the contract declares no fields — nothing to own")

# --- field map (fleet <-> peer): every field mapped, one-sided named --------
map_fields = {}
if isinstance(field_map, dict) and isinstance(field_map.get("fields"), dict):
    map_fields = field_map["fields"]
else:
    findings.append(f"{field_map_path}: no 'fields' object")

for field in sorted(vocab):
    row = map_fields.get(field)
    if not isinstance(row, dict):
        findings.append(
            f"field '{field}' is unmapped — no row in {field_map_path} "
            "(the shape exists on one side and is silently dropped on the other)"
        )
        continue
    fleet_field = row.get("fleet_field")
    peer_field = row.get("peer_field")
    one_sided = row.get("one_sided")
    if fleet_field is None and peer_field is None:
        findings.append(
            f"field '{field}' maps to neither side in {field_map_path} "
            "(present on one side only and not named)"
        )
    elif peer_field is None and one_sided != "fleet":
        findings.append(
            f"field '{field}' exists on the fleet side only and is not named "
            "(one_sided must be 'fleet')"
        )
    elif fleet_field is None and one_sided != "peer":
        findings.append(
            f"field '{field}' exists on the peer side only and is not named "
            "(one_sided must be 'peer')"
        )

for field in sorted(map_fields):
    if field not in vocab:
        findings.append(
            f"field '{field}' in {field_map_path} is not in the contract "
            "vocabulary (stale entry)"
        )

# --- ownership: exactly one producer, a declared side -----------------------
sides = {}
fields = {}
if isinstance(ownership, dict):
    if isinstance(ownership.get("sides"), dict):
        sides = ownership["sides"]
    else:
        findings.append(f"{ownership_path}: no 'sides' object")
    if isinstance(ownership.get("fields"), dict):
        fields = ownership["fields"]
    else:
        findings.append(f"{ownership_path}: no 'fields' object")
else:
    findings.append(f"{ownership_path}: top level is not a JSON object")

local_produced = 0
for field in sorted(vocab):
    entry = fields.get(field)
    if not isinstance(entry, dict):
        findings.append(
            f"field '{field}' has no declared producer (absent from "
            f"{ownership_path})"
        )
        continue
    producers = entry.get("producers")
    if not isinstance(producers, list) or not producers:
        findings.append(f"field '{field}' has no declared producer")
        continue
    if len(producers) > 1:
        names = ", ".join(str(p) for p in producers)
        findings.append(
            f"field '{field}' declares {len(producers)} producers ({names}) "
            "— one writer per field (half-coupling)"
        )
        continue
    only = producers[0]
    if sides and only not in sides:
        findings.append(
            f"field '{field}' names producer '{only}', which is not a declared "
            f"side ({', '.join(sorted(sides))})"
        )
        continue
    if str(only).startswith("fleet-"):
        local_produced += 1

for field in sorted(fields):
    if field not in vocab:
        findings.append(
            f"field '{field}' in {ownership_path} is not in the contract "
            "vocabulary (stale entry)"
        )

if vocab and local_produced == 0:
    findings.append(
        "no field is produced by a local fleet side — the shape exists but "
        "nothing produces it (kushin77/deepseek#115)"
    )

# --- truthfulness: a claim whose evidence is empty is not claimed -----------
def check_claims(instance, path):
    if not isinstance(instance, dict):
        return
    receipt = instance.get("receipt")
    if isinstance(receipt, dict):
        evidence = receipt.get("evidence")
        if not (isinstance(evidence, str) and evidence.strip()):
            findings.append(
                f"{path}: receipt for ticket {receipt.get('ticket')!r} carries an "
                "empty evidence reference — a receipt that cannot be evidenced "
                "is refused"
            )
    claims = instance.get("claims")
    if isinstance(claims, list):
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            evidence = claim.get("evidence")
            if not (isinstance(evidence, str) and evidence.strip()):
                findings.append(
                    f"{path}: claims[{i}] ({claim.get('kind')} "
                    f"{claim.get('metric')}) has an empty evidence field — a "
                    "saving or a cap that cannot be measured is not claimed"
                )


# --- examples validate against the schema -----------------------------------
try:
    import jsonschema
except ImportError:
    cannot_assess.append(
        "jsonschema is not importable — the contract instances cannot be validated"
    )
    jsonschema = None
else:
    examples = sorted(contract.glob("*.example.json"))
    if not examples:
        findings.append(f"{contract}: no *.example.json instance to validate")
    for path in examples:
        instance = load_json(path)
        if instance is None:
            continue
        check_claims(instance, path)
        if schema is None:
            continue
        try:
            jsonschema.validate(instance, schema)
        except jsonschema.ValidationError as exc:
            findings.append(
                f"{path}: does not validate against the record schema "
                f"({exc.message})"
            )
        except jsonschema.SchemaError as exc:
            findings.append(f"{schema_path}: not a usable schema ({exc.message})")

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
    f"  OK    one writer for each of {len(vocab)} metering/budget field(s); "
    "every field mapped across the boundary; examples validate; claims evidenced"
)
raise SystemExit(0)
PY
}

rc=0
validate "$contract_dir" || rc=$?
case "$rc" in
  0) : ;;
  1)
    echo "check-metering-parity: FAIL — the metering/budget boundary is not frozen in full" >&2
    exit 1
    ;;
  *)
    echo "check-metering-parity: CANNOT-ASSESS — the contract could not be read" >&2
    exit 2
    ;;
esac

# --- negative controls ------------------------------------------------------
# Each control mutates a copy of the contract and requires the validator to
# refuse by NAME with the right exit code. If any control passes, this gate
# reports FAIL: a gate that cannot fail is a formality (GR-12).
work="/tmp/ao425-metering-gate.$(date +%s%N).$$"
if ! mkdir -p "$work"; then
  echo "check-metering-parity: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

base="$work/base"
if ! cp -R "$contract_dir" "$base"; then
  echo "check-metering-parity: CANNOT-ASSESS — cannot copy the contract for controls" >&2
  exit 2
fi

mutate() {
  # mutate <control> <out-dir> — copy base, apply one named mutation.
  local control="$1" out="$2"
  cp -R "$base" "$out" || return 2
  python3 - "$control" "$out" <<'PY'
import json
import pathlib
import sys

control = sys.argv[1]
out = pathlib.Path(sys.argv[2])
field_map = out / "field-map.json"
ownership = out / "ownership.json"
example = out / "metering-record.example.json"

if control == "unmapped":
    data = json.loads(field_map.read_text(encoding="utf-8"))
    # Drop one row: the field now exists on the schema side only, unnamed.
    del data["fields"]["quantity.cacheHit"]
    field_map.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif control == "no-producer":
    data = json.loads(ownership.read_text(encoding="utf-8"))
    data["fields"]["quantity.costUsd"]["producers"] = []
    ownership.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif control == "empty-evidence":
    data = json.loads(example.read_text(encoding="utf-8"))
    for claim in data.get("claims", []):
        claim["evidence"] = "   "
    example.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif control == "missing-input":
    (out / "ownership.json").unlink()
else:
    raise SystemExit(2)
PY
}

expect_refusal() {
  # expect_refusal <control> <want-rc> <want-substring>
  local control="$1" want_rc="$2" want_text="$3"
  local out="$work/$control"
  if ! mutate "$control" "$out"; then
    echo "check-metering-parity: CANNOT-ASSESS — could not build control '$control'" >&2
    return 2
  fi
  local got_rc=0 out_text
  out_text="$(validate "$out" 2>&1)" || got_rc=$?
  if [ "$got_rc" -ne "$want_rc" ]; then
    echo "check-metering-parity: FAIL — control '$control' returned rc=$got_rc, expected $want_rc" >&2
    printf '%s\n' "$out_text" >&2
    return 1
  fi
  if ! printf '%s\n' "$out_text" | grep -qF "$want_text"; then
    echo "check-metering-parity: FAIL — control '$control' did not name '$want_text'" >&2
    printf '%s\n' "$out_text" >&2
    return 1
  fi
  printf '  OK    control %-14s rc=%s  names: %s\n' "$control" "$got_rc" "$want_text"
  return 0
}

control_rc=0
expect_refusal unmapped 1 "is unmapped" || control_rc=1
expect_refusal no-producer 1 "field 'quantity.costUsd' has no declared producer" || control_rc=1
expect_refusal empty-evidence 1 "empty evidence field" || control_rc=1
expect_refusal missing-input 2 "unreadable" || control_rc=1

if [ "$control_rc" -ne 0 ]; then
  echo "check-metering-parity: FAIL — a negative control passed; the boundary is not enforced" >&2
  exit 1
fi

echo "check-metering-parity: OK — one writer per metering/budget field, mapped across the boundary, claims evidenced, controls refuse by name"
exit 0
