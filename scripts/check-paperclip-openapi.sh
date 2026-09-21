#!/usr/bin/env bash
# check-paperclip-openapi.sh — the HTTP-surface gate for the paperclip seam
# (issue #413, ADR-0013 / ADR-0016).
#
# The seam projects the fleet's state over paperclip's HTTP surface: GET
# /api/health, GET /api/openapi.json, the company-scoped route shape
# /api/companies/{companyId}/... and the mapped error taxonomy. A projection
# that nothing validates is a formality (no-false-green doctrine, GR-12), so this
# gate fails, by name, when the surface stops telling the truth:
#
#   * the OpenAPI document must be EMITTED from the frozen contracts in
#     docs/contracts/paperclip/ (ticket v2 incl. facets/authority, heartbeat,
#     budget) and the adapter's own to_dict shapes — a drifted schema is
#     refused naming the contract file it drifted from;
#   * the committed artifact integrations/paperclip/api/openapi.json must equal a
#     fresh emission, byte for byte (the document is deterministic, so a diff is
#     a real change);
#   * every status in the error taxonomy (400/401/403/404/409/422/503) must be
#     present and carried by a route — a missing entry is refused by name;
#   * {companyId} must map onto the fleet's own tenancy through the ONE declared
#     mapping — an undeclared mapping is refused by name;
#   * /api/health must name real dependencies and must NOT report ok while a
#     dependency it names is missing — a green lie is refused by name.
#
# Run with no arguments this gate provokes all four failures in process and
# asserts each is refused BY NAME, greps the refusals independently of the
# checker's own verdict, re-emits the document and compares it to the committed
# artifact, and runs the boundary suite in isolation. If a provoked failure
# passes, the gate prints FAIL and exits non-zero.
#
# Exit-code contract (guardrails/honesty tri-state, issue #28):
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never exits 0.
#
# Usage:
#   bash scripts/check-paperclip-openapi.sh
#   bash scripts/check-paperclip-openapi.sh --no-controls
#   bash scripts/check-paperclip-openapi.sh --root DIR
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
run_controls=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      root="$(cd "${2:-}" 2>/dev/null && pwd)" || { echo "check-paperclip-openapi: CANNOT-ASSESS — bad --root" >&2; exit 2; }
      shift 2
      ;;
    --no-controls) run_controls=0; shift ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) printf 'check-paperclip-openapi: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

cd "$root" || { echo "check-paperclip-openapi: CANNOT-ASSESS — cannot enter $root" >&2; exit 2; }

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-openapi: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

api_dir="integrations/paperclip/api"
artifact="$api_dir/openapi.json"
if [ ! -d "$api_dir" ]; then
  echo "check-paperclip-openapi: FAIL — $api_dir is missing (the surface has no home)" >&2
  exit 1
fi
if [ ! -f "$artifact" ]; then
  echo "check-paperclip-openapi: FAIL — $artifact is missing (the emitted document is not committed)" >&2
  exit 1
fi

scratch="/tmp/ao413.openapi.$(date +%s%N)."
mkdir "$scratch" || { echo "check-paperclip-openapi: CANNOT-ASSESS — cannot create scratch" >&2; exit 2; }
trap 'rm -rf "$scratch"' EXIT

# --- 1. the document matches its sources and the committed artifact ----------
echo "== document (emitted from the frozen contracts) =="
check_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$api_dir/cli.py" --root "$root" check 2>&1)"
check_rc=$?
printf '%s\n' "$check_out"
if [ "$check_rc" -ne 0 ]; then
  echo "check-paperclip-openapi: FAIL — the document does not match its sources" >&2
  exit 1
fi

# --- 2. the committed artifact equals a fresh emission (deterministic) -------
echo "== committed artifact (byte-identical re-emission) =="
env PYTHONDONTWRITEBYTECODE=1 python3 "$api_dir/cli.py" --root "$root" emit > "$scratch/fresh.json" 2>"$scratch/emit.err"
emit_rc=$?
if [ "$emit_rc" -ne 0 ]; then
  sed -n '1,20p' "$scratch/emit.err" >&2
  echo "check-paperclip-openapi: FAIL — the document could not be emitted" >&2
  exit 1
fi
if ! cmp -s "$scratch/fresh.json" "$artifact"; then
  echo "check-paperclip-openapi: FAIL — $artifact is stale or hand-edited (it differs from a fresh emission)" >&2
  exit 1
fi
echo "  OK    $artifact is byte-identical to a fresh emission ($(wc -c < "$artifact") bytes)"

if [ "$run_controls" -eq 0 ]; then
  echo "check-paperclip-openapi: OK (structure only — controls NOT run)"
  exit 0
fi

# --- 3. every provoked failure refused, by name ------------------------------
echo "== controls (provoked) =="
controls_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$api_dir/cli.py" --root "$root" controls 2>&1)"
controls_rc=$?
printf '%s\n' "$controls_out"
if [ "$controls_rc" -ne 0 ]; then
  echo "check-paperclip-openapi: FAIL — a provoked failure was not refused" >&2
  exit 1
fi

# Assert each refusal INDEPENDENTLY of the run's own verdict: if the in-process
# checker were weakened to always report success, these lines would be absent.
expectations=(
  "refused -> contract-drift:"
  "refused -> taxonomy-422:"
  "refused -> company-mapping:"
  "refused -> health-lying-ok:"
)
for expect in "${expectations[@]}"; do
  if ! printf '%s\n' "$controls_out" | grep -qF "$expect"; then
    echo "check-paperclip-openapi: FAIL — no control was refused with '$expect'" >&2
    exit 1
  fi
done
if ! printf '%s\n' "$controls_out" | grep -qF "drifts from the frozen contract"; then
  echo "check-paperclip-openapi: FAIL — the contract-drift refusal named no contract file" >&2
  exit 1
fi
if ! printf '%s\n' "$controls_out" | grep -qF "company mapping is undeclared"; then
  echo "check-paperclip-openapi: FAIL — the undeclared company mapping was not named" >&2
  exit 1
fi
echo "  OK    all four provoked failures refused by name (contract drift, missing taxonomy entry, undeclared company mapping, health green lie)"

# --- 4. the surface suite, run in isolation ----------------------------------
echo "== pytest ($api_dir/tests) =="
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q "$api_dir/tests" 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -4
if [ "$suite_rc" -ne 0 ]; then
  printf '%s\n' "$suite_out" | tail -20 >&2
  echo "check-paperclip-openapi: FAIL — the surface suite is red (pytest exit $suite_rc)" >&2
  exit 1
fi

echo "check-paperclip-openapi: OK — paperclip HTTP surface verified"
exit 0
