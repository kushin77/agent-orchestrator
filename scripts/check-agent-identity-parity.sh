#!/usr/bin/env bash
# check-agent-identity-parity.sh — one shared agent-identity schema, and a gate
# that fails on drift (issue #346).
#
# The drift this kills: shared-frontend's TeamAgent
# ({id,name,provider,modelTier,status,capabilities} — free-text capabilities, a
# two-tier ladder, status active|paused|retired) and agent-orchestrator's
# AgentProfile (no name/status/provider, a closed LOW/MED/HIGH/MAX ladder, a
# closed capabilityId set, status registered|active|paused|retired). The fix is
# ONE shared, versioned schema (registry/profiles/agent-identity.schema.json)
# that is the UNION of both, with a CLOSED vocabulary on every enum:
#
#   * modelTier  LOW|MED|HIGH|MAX        (superset of the two-tier notion)
#   * status     registered|active|paused|retired (the union of both repos)
#   * capabilities  a closed capabilityId list — FREE TEXT IS REFUSED
#
# There is NO hand-maintained snapshot anywhere. The two inputs are the repo's
# own declarations (agent-profile.schema.json + catalog.yaml) and the seed files
# themselves, which are PROJECTED to the identity view by the documented
# projection (id -> name, transport -> provider, defaultModelTier -> modelTier,
# capabilitySet -> capabilities, absent status -> registered) and validated
# against the shared schema. A vocabulary or required-field change on EITHER
# side of the contract fails this gate until it is reconciled in the shared
# schema — never forked.
#
# The gate runs its own self-mutating negative control on a scratch copy of the
# tree and requires the validator to REFUSE each mutant:
#
#   A  a required field stripped from a seed        -> NOT-OK, naming the field
#   B  a free-text capability added to a seed       -> NOT-OK, naming the field
#   C  the shared schema's closed enum widened      -> NOT-OK, schema drift
#   D  the shared schema hidden                     -> CANNOT-ASSESS, never OK
#   E  the pristine copy, after every mutation      -> still OK
#
# Each mutation must really change the file (sha256 compared), and the committed
# files must be byte-identical before and after the run, so the control cannot
# pass vacuously and cannot pass by damaging the tree.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28):
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS must never be reported as a
# pass, and a check that cannot fail is a formality (GR-12).
#
# Usage: bash scripts/check-agent-identity-parity.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

profiles="registry/profiles"
schema="$profiles/agent-identity.schema.json"
profile_schema="$profiles/agent-profile.schema.json"
catalog="$profiles/catalog.yaml"
seeds="$profiles/seeds"
cli="$profiles/parity/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-agent-identity-parity: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
for required in "$schema" "$profile_schema" "$catalog" "$cli"; do
  if [ ! -f "$required" ]; then
    echo "check-agent-identity-parity: CANNOT-ASSESS — $required is missing" >&2
    exit 2
  fi
done
if [ ! -d "$seeds" ]; then
  echo "check-agent-identity-parity: CANNOT-ASSESS — $seeds is missing" >&2
  exit 2
fi

validate() {
  python3 "$cli" --schema "$1" --profile-schema "$2" --catalog "$3" \
    --seeds-dir "$4"
}

sha() { sha256sum "$1" | cut -d' ' -f1; }

# --- 1. the committed tree --------------------------------------------------
echo "== shared identity schema parity + seed projection =="
out="$(validate "$schema" "$profile_schema" "$catalog" "$seeds")"
rc=$?
printf '%s\n' "$out"
case "$rc" in
  0) ;;
  2)
    echo "check-agent-identity-parity: CANNOT-ASSESS — the checker could not assess the tree (see above)" >&2
    exit 2 ;;
  *)
    echo "check-agent-identity-parity: NOT-OK — the committed tree does not satisfy the shared agent-identity schema" >&2
    exit 1 ;;
esac

# --- 2. the self-mutating negative control ----------------------------------
# A scratch dir WITHOUT a trailing run of `X`: this repo's docs-lint scans for
# unfinished markers and a mktemp X-suffix trips it, so this uses the convention
# the other gates use — an explicit /tmp name, with mkdir refusing loudly rather
# than silently reusing another run's tree.
scratch="/tmp/ao-agent-identity-parity.$(date +%s%N).$$"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-agent-identity-parity: CANNOT-ASSESS — cannot create scratch dir $scratch" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

control_failures=0

pristine="$scratch/pristine"
if ! mkdir -p "$pristine/seeds"; then
  echo "check-agent-identity-parity: CANNOT-ASSESS — cannot build the scratch tree" >&2
  exit 2
fi
cp "$schema" "$pristine/agent-identity.schema.json"
cp "$profile_schema" "$pristine/agent-profile.schema.json"
cp "$catalog" "$pristine/catalog.yaml"
cp "$seeds"/*.yaml "$pristine/seeds/"

real_schema_sha="$(sha "$schema")"
real_profile_sha="$(sha "$profile_schema")"
real_catalog_sha="$(sha "$catalog")"

# mutate <file> <mode> [arg...] — mutate a scratch copy and require the file to
# actually change, so a no-op mutant cannot masquerade as a caught mutation.
mutate() {
  file="$1"
  shift
  before="$(sha "$file")"
  python3 - "$file" "$@" <<'PY'
import json
import sys

import yaml

path, mode = sys.argv[1], sys.argv[2]
is_yaml = path.endswith((".yaml", ".yml"))
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) if is_yaml else json.load(fh)

if mode == "strip":
    data.pop(sys.argv[3], None)
elif mode == "add-capability":
    data["capabilitySet"] = list(data.get("capabilitySet") or []) + [sys.argv[3]]
elif mode == "widen-enum":
    data["definitions"][sys.argv[3]]["enum"].append(sys.argv[4])
else:
    raise SystemExit("unknown mutation mode: %s" % mode)

with open(path, "w", encoding="utf-8") as fh:
    if is_yaml:
        yaml.safe_dump(data, fh, sort_keys=False)
    else:
        json.dump(data, fh, indent=2)
        fh.write("\n")
PY
  after="$(sha "$file")"
  if [ "$before" = "$after" ]; then
    echo "  control FAIL  the mutation on $(basename "$file") did not change the file" >&2
    control_failures=$((control_failures + 1))
  fi
}

# control <name> <want-rc> <want-grep> <tree>
control() {
  name="$1"
  want_rc="$2"
  want_grep="$3"
  tree="$4"
  out="$(validate "$tree/agent-identity.schema.json" \
      "$tree/agent-profile.schema.json" "$tree/catalog.yaml" "$tree/seeds")"
  got_rc=$?
  ok=1
  if [ "$got_rc" != "$want_rc" ]; then
    ok=0
  fi
  if [ -n "$want_grep" ] && ! printf '%s\n' "$out" | grep -qF -- "$want_grep"; then
    ok=0
  fi
  if [ "$ok" = 1 ]; then
    printf '  control OK    %-44s rc=%s\n' "$name" "$got_rc"
    return 0
  fi
  printf '  control FAIL  %-44s rc=%s (want %s)\n' "$name" "$got_rc" "$want_rc" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  control_failures=$((control_failures + 1))
  return 1
}

# E first, so the pristine copy is proven green before it is mutated.
control "E: pristine scratch copy is green" 0 "" "$pristine"

# A: strip a required field from a seed.
mut_a="$scratch/mut-a"
cp -r "$pristine" "$mut_a"
mutate "$mut_a/seeds/paperclip.1.0.0.yaml" strip owner
control "A: required field stripped from a seed" 1 "AI-MISSING-FIELD field=owner" "$mut_a"

# B: widen the capability list with free text.
mut_b="$scratch/mut-b"
cp -r "$pristine" "$mut_b"
mutate "$mut_b/seeds/paperclip.1.0.0.yaml" add-capability free-text-capability
control "B: free-text capability added to a seed" 1 "AI-OUT-OF-VOCAB field=capabilities" "$mut_b"

# C: widen a closed vocabulary in the SHARED schema.
mut_c="$scratch/mut-c"
cp -r "$pristine" "$mut_c"
mutate "$mut_c/agent-identity.schema.json" widen-enum capabilityId made-up-capability
control "C: shared schema's closed enum widened" 1 "AI-SCHEMA-DRIFT field=capabilityId" "$mut_c"

# D: hide the shared schema -> CANNOT-ASSESS, never OK.
mut_d="$scratch/mut-d"
cp -r "$pristine" "$mut_d"
before="$(sha "$mut_d/agent-identity.schema.json")"
rm -f "$mut_d/agent-identity.schema.json"
if sha "$mut_d/agent-identity.schema.json" 2>/dev/null | grep -q "$before"; then
  echo "  control FAIL  the schema was not actually removed" >&2
  control_failures=$((control_failures + 1))
fi
control "D: shared schema hidden" 2 "AI-CANNOT-ASSESS" "$mut_d"

# The committed tree must be byte-identical: the control works on a copy and
# must never touch — or repair — the real files.
if [ "$(sha "$schema")" != "$real_schema_sha" ] \
  || [ "$(sha "$profile_schema")" != "$real_profile_sha" ] \
  || [ "$(sha "$catalog")" != "$real_catalog_sha" ]; then
  echo "  control FAIL  the run modified a committed file" >&2
  control_failures=$((control_failures + 1))
else
  printf '  control OK    %-44s sha unchanged\n' "F: committed files untouched"
fi

if [ "$control_failures" -ne 0 ]; then
  echo "check-agent-identity-parity: NOT-OK — $control_failures negative control(s) failed; a check that cannot fail is a formality" >&2
  exit 1
fi

seed_count="$(find "$seeds" -maxdepth 1 -name '*.yaml' | wc -l)"
echo "check-agent-identity-parity: OK — the shared schema's closed vocabularies reconcile with agent-profile.schema.json and catalog.yaml, $seed_count seed(s) satisfy it as projected identities, and 6 negative control(s) held"
exit 0
