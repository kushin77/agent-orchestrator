#!/usr/bin/env bash
# check-paperclip-auth.sh — the cross-boundary auth gate for the paperclip seam
# (issue #412, ADR-0013 / ADR-0012).
#
# The seam doc (docs/PAPERCLIP-ING-INTEGRATION.md §5) names mismatch 11 — the two
# auth models do not meet natively — as one-time adoption work. The boundary auth
# seam in integrations/paperclip/auth/ is where they meet. A seam that nothing
# validates is a formality (no-false-green doctrine, GR-12), so this gate fails,
# by name, when the boundary stops refusing what it must:
#
#   * the agent identity must be read from the FLEET'S OWN registry seeds
#     (registry/profiles/seeds/*.yaml) — a second identity store is refused;
#   * every negative control must be provoked and refused, with its own wire
#     code: 401 expired / 401 unknown / 401 missing Authorization / 403
#     cross-company / 403 permission_denied (never 404) / 409 replayed run id;
#   * the boundary suite under integrations/paperclip/auth/tests/ must pass in
#     isolation (this gate is not part of the declared pytest manifest — wiring
#     the check into make verify is owned by the gate-wiring lane, #420 — so the
#     suite is run here rather than assumed).
#
# The controls run with an ephemeral in-process signing key when
# PAPERCLIP_AUTH_KEY is unset, so the gate needs no secret material, and no
# refusal ever echoes a credential (GR-6).
#
# Exit-code contract (guardrails/honesty tri-state, issue #28):
#   0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never exits 0.
#
# Usage:
#   bash scripts/check-paperclip-auth.sh
#   bash scripts/check-paperclip-auth.sh --no-controls
#   bash scripts/check-paperclip-auth.sh --root DIR
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
run_controls=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      root="$(cd "${2:-}" 2>/dev/null && pwd)" || { echo "check-paperclip-auth: CANNOT-ASSESS — bad --root" >&2; exit 2; }
      shift 2
      ;;
    --no-controls) run_controls=0; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) printf 'check-paperclip-auth: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

cd "$root" || { echo "check-paperclip-auth: CANNOT-ASSESS — cannot enter $root" >&2; exit 2; }

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-auth: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

auth_dir="integrations/paperclip/auth"
if [ ! -d "$auth_dir" ]; then
  echo "check-paperclip-auth: FAIL — $auth_dir is missing (the boundary auth seam has no home)" >&2
  exit 1
fi

# --- 1. the identity source of truth is the fleet's own registry -------------
echo "== identity source =="
source_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 - "$root" <<'PY' 2>&1
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))

from integrations.paperclip.auth import registry as registry_mod

agents = registry_mod.registered_agents(root)
missing = {"paperclip", "orchestrator"} - set(agents)
if missing:
    print("the registry the seam reads is missing %s" % sorted(missing))
    raise SystemExit("registry is not the fleet's own seed set")
if agents["paperclip"].owner == "":
    raise SystemExit("a registered agent carries no owner (no company scope)")
print("  OK    %d registered agent(s) read from registry/profiles/seeds (no second store)"
      % len(agents))
PY
)"
source_rc=$?
printf '%s\n' "$source_out"
if [ "$source_rc" -ne 0 ]; then
  echo "check-paperclip-auth: FAIL — the auth seam does not read the fleet registry" >&2
  exit 1
fi

if [ "$run_controls" -eq 0 ]; then
  # The vocabulary is still asserted: a --no-controls run must not be mistaken
  # for a proven seam.
  echo "check-paperclip-auth: OK (structure only — controls NOT run)"
  exit 0
fi

# --- 2. every negative control provoked and refused by name -------------------
echo "== controls (provoked) =="
controls_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$auth_dir/cli.py" --root . controls 2>&1)"
controls_rc=$?
printf '%s\n' "$controls_out"
if [ "$controls_rc" -ne 0 ]; then
  echo "check-paperclip-auth: FAIL — the controls run did not refuse every control" >&2
  exit 1
fi

# Assert each refusal INDEPENDENTLY of the run's own verdict: if the in-process
# checker were weakened to always report success, these lines would be absent.
expectations=(
  "refused -> 401 token_expired"
  "refused -> 401 invalid_token"
  "refused -> 403 cross_tenant"
  "refused -> 409 replayed_run_id"
  "refused -> 401 unauthorized"
  "refused -> 403 permission_denied"
)
for expect in "${expectations[@]}"; do
  if ! printf '%s\n' "$controls_out" | grep -qF "$expect"; then
    echo "check-paperclip-auth: FAIL — no control was refused with '$expect'" >&2
    exit 1
  fi
done
if ! printf '%s\n' "$controls_out" | grep -qF "denial distinguished from a missing route"; then
  echo "check-paperclip-auth: FAIL — the 403-not-404 distinction was not proven" >&2
  exit 1
fi
echo "  OK    all six refusals provoked by name (401 expired/unknown/missing, 403 cross-company/permission, 409 replay)"

# --- 3. the boundary suite, run in isolation ---------------------------------
echo "== pytest (integrations/paperclip/auth/tests) =="
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  -q "$auth_dir/tests" 2>&1)"
suite_rc=$?
printf '%s\n' "$suite_out" | tail -4
if [ "$suite_rc" -ne 0 ]; then
  printf '%s\n' "$suite_out" | tail -20 >&2
  echo "check-paperclip-auth: FAIL — the boundary suite is red (pytest exit $suite_rc)" >&2
  exit 1
fi

echo "check-paperclip-auth: OK — cross-boundary auth seam verified"
exit 0
