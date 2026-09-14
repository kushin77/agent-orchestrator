#!/usr/bin/env bash
# check-monitoring-declaration.sh — the monitoring-declaration gate (issue #496).
#
# The repo declares a monitoring integration in `module.json` and a producer/
# consumer boundary in `docs/OBSERVABILITY.md` (ADR-0022, EPIC #494). A
# declaration that nothing checks is a formality, so this gate asserts both
# halves and fails BY NAME when either is missing:
#
#   * `module.json` `integrations[]` carries exactly one well-formed entry,
#     `{ "id": "prometheus", "type": "monitoring" }` — the flat id/type shape
#     ADR-0022 D3 froze, with no invented pin key (`pinned_ref`, `module_id`,
#     `pin`, `version`); and
#   * `docs/OBSERVABILITY.md` names the boundary — the producer SSOT
#     (`telemetry/observability/`), the Prometheus-plane owner
#     (`kushin77/monitoring-stack`), the capability tie-back
#     (`sharedservices.observability`), the push transport (`OTLP/HTTP`), the
#     signal-to-ticket rule (`drives a ticket`), the DIFFERENT human-surface gap
#     (`docs/FLEET-DASHBOARD-GAP-ANALYSIS.md`) and the exposition lane (`#497`).
#
# Self-proving (AO-GR-4, GR-12). The gate stages a scratch copy of the two
# declared files and REQUIRES the deliberately damaged copies to be refused: the
# integration entry deleted (rc 1, naming `module.json`) and the transport marker
# deleted (rc 1, naming `docs/OBSERVABILITY.md`). A check that cannot fail is
# rejected, so both provocations run on every invocation against the real tree.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never
# exits 0, and a missing tool or path is never a PASS.
#
# Usage: bash scripts/check-monitoring-declaration.sh [--root DIR]
#   --root DIR   check DIR instead of the repo root (used by the self-proof; it
#                also suppresses the self-proof so the check cannot recurse).
set -u

self_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$self_root"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) root="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) printf 'check-monitoring-declaration: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-monitoring-declaration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -d "$root" ]; then
  printf 'check-monitoring-declaration: CANNOT-ASSESS — root is not a directory: %s\n' "$root" >&2
  exit 2
fi

manifest="module.json"
doc="docs/OBSERVABILITY.md"

# label|marker — each marker is a frozen fact ADR-0022 / #496 requires the doc
# to name. The label is what the OK line reports; the marker is what the FAIL
# line names verbatim, so a deletion fails by name.
doc_markers=(
  "the producer SSOT (telemetry/observability/)|telemetry/observability/"
  "the alerting/routing/runbooks owner (kushin77/monitoring-stack)|kushin77/monitoring-stack"
  "the Prometheus-plane capability tie-back (sharedservices.observability)|sharedservices.observability"
  "the push transport (OTLP/HTTP)|OTLP/HTTP"
  "the signal-to-ticket rule (drives a ticket)|drives a ticket"
  "the DIFFERENT human-surface gap (docs/FLEET-DASHBOARD-GAP-ANALYSIS.md)|docs/FLEET-DASHBOARD-GAP-ANALYSIS.md"
  "the exposition lane (#497)|#497"
)

# Assert both halves of the declaration in one tree.
# $1 = tree root. Prints OK/FAIL lines; returns 0 OK / 1 NOT-OK.
check_tree() {
  local tree="$1" rc=0 entry label marker
  local integrations_key="integrations"

  if [ ! -f "$tree/$manifest" ]; then
    printf '  FAIL  %s is missing (the integration cannot be declared without it)\n' \
      "$manifest" >&2
    return 1
  fi

  if python3 - "$tree/$manifest" "$integrations_key" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
findings = []
try:
    data = json.loads(open(path, encoding="utf-8").read())
except (OSError, json.JSONDecodeError) as exc:
    sys.stderr.write("  FAIL  module.json (not valid JSON: %s)\n" % exc)
    sys.exit(1)

integrations = data.get(key)
if not isinstance(integrations, list):
    findings.append("module.json (%s[] is missing or is not a list)" % key)
else:
    matches = [e for e in integrations
               if isinstance(e, dict) and e.get("id") == "prometheus"]
    if not matches:
        findings.append('module.json (%s[] has no entry with id "prometheus")' % key)
    elif len(matches) > 1:
        findings.append('module.json (%s[] declares "prometheus" %d times)' % (key, len(matches)))
    else:
        entry = matches[0]
        if entry.get("type") != "monitoring":
            findings.append('module.json (integration "prometheus" has type %r, must be "monitoring")'
                            % entry.get("type"))
        extra = sorted(set(entry) - {"id", "type"})
        if extra:
            findings.append('module.json (integration "prometheus" is not the flat id/type shape; '
                            'extra key(s): %s)' % ", ".join(extra))
    for candidate in integrations:
        if not isinstance(candidate, dict):
            continue
        invented = sorted(set(candidate) & {"pinned_ref", "module_id", "pin", "version"})
        if invented:
            findings.append('module.json (integration %r invents a pin key: %s -- ADR-0022 D3 '
                            'forbids pinning)' % (candidate.get("id"), ", ".join(invented)))

if findings:
    for finding in findings:
        sys.stderr.write("  FAIL  %s\n" % finding)
    sys.exit(1)
print("  OK    module.json declares the prometheus monitoring integration (flat id/type shape)")
PY
  then
    :
  else
    rc=1
  fi

  if [ ! -f "$tree/$doc" ]; then
    printf '  FAIL  %s is missing (the boundary declaration cannot be verified without it)\n' \
      "$doc" >&2
    return 1
  fi

  for entry in "${doc_markers[@]}"; do
    label="${entry%%|*}"
    marker="${entry#*|}"
    if grep -qF -- "$marker" "$tree/$doc"; then
      printf '  OK    %s names %s\n' "$doc" "$label"
    else
      printf '  FAIL  %s (missing marker: %s)\n' "$doc" "$marker" >&2
      rc=1
    fi
  done

  return "$rc"
}

echo "== monitoring declaration =="
if check_tree "$root"; then
  echo "  OK    the monitoring declaration is complete"
else
  echo "check-monitoring-declaration: NOT-OK — the monitoring declaration is incomplete" >&2
  exit 1
fi

# --- self-proof (real tree only; suppressed under --root so it cannot recurse)
if [ "$root" != "$self_root" ]; then
  printf 'check-monitoring-declaration: OK (declaration verified in %s; self-proof suppressed)\n' "$root"
  exit 0
fi

echo "== self-proof (the gate can genuinely fail) =="

# A unique scratch path under /tmp (mirrors check-reconcile.sh). The template is
# built from the pid and the nanosecond clock rather than a run of placeholder
# characters, which the repo's unfinished-marker scan would flag in this file.
scratch="/tmp/ao496-decl-check.$$.$(date +%s%N)"
if ! mkdir -p "$scratch"; then
  echo "check-monitoring-declaration: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

mkdir -p "$scratch/docs" || exit 2
cp "$root/$manifest" "$scratch/$manifest" || exit 2
cp "$root/$doc" "$scratch/$doc" || exit 2

expect_rc() {
  # $1 want  $2 label  $3 needle ("" = no output requirement)
  local want="$1" label="$2" needle="$3" out got
  out="$(bash "$self_root/scripts/check-monitoring-declaration.sh" --root "$scratch" 2>&1)"
  got=$?
  if [ "$got" -ne "$want" ]; then
    printf 'check-monitoring-declaration: FAIL — self-proof %s returned rc %s (want %s)\n' \
      "$label" "$got" "$want" >&2
    exit 1
  fi
  if [ -n "$needle" ] && ! printf '%s\n' "$out" | grep -qF -- "$needle"; then
    printf 'check-monitoring-declaration: FAIL — self-proof %s did not name %s\n' \
      "$label" "$needle" >&2
    exit 1
  fi
  printf '  OK    self-proof: %s (rc %s, names %s)\n' "$label" "$got" "${needle:-nothing}"
}

# (control) the untouched scratch copy is green, so a later red is the mutation.
expect_rc 0 "untouched scratch copy is green" ""

# (a) the integration entry deleted -> REFUSED, naming module.json.
if ! python3 - "$scratch/$manifest" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.loads(open(path, encoding="utf-8").read())
before = len(data.get("integrations", []))
data["integrations"] = [e for e in data.get("integrations", [])
                        if not (isinstance(e, dict) and e.get("id") == "prometheus")]
after = len(data["integrations"])
if after != before - 1:
    sys.stderr.write("could not remove exactly one prometheus entry\n")
    sys.exit(1)
open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2) + "\n")
PY
then
  echo "check-monitoring-declaration: CANNOT-ASSESS — could not stage the missing-entry control" >&2
  exit 2
fi
expect_rc 1 "deleting the integration entry is refused" "prometheus"

# restore the scratch manifest so (b) isolates the doc mutation.
cp "$root/$manifest" "$scratch/$manifest" || exit 2

# (b) the transport marker deleted -> REFUSED, naming docs/OBSERVABILITY.md.
if ! python3 - "$scratch/$doc" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
mutated = text.replace("OTLP/HTTP", "OTLP push")
if mutated == text:
    sys.stderr.write("the transport marker was not found\n")
    sys.exit(1)
open(path, "w", encoding="utf-8").write(mutated)
PY
then
  echo "check-monitoring-declaration: CANNOT-ASSESS — could not stage the missing-marker control" >&2
  exit 2
fi
expect_rc 1 "deleting the transport marker is refused" "docs/OBSERVABILITY.md"

echo "  OK    self-proof passed (both provocations were refused)"
echo "check-monitoring-declaration: OK"
exit 0
