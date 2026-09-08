#!/usr/bin/env bash
# check-negative-controls.sh — guard negative-controls signal for the gate (issue #29).
#
# Consumes the guardrails/honesty model (issue #28) read-only: a guard that
# ships no negative control proves nothing, and a guard that cannot fail fails
# its own negative control (AO-GR-19). Two honesty sub-checks must both pass:
#
#   1. honesty negative honesty/manifest.negative.yaml
#        run the declared negative/positive/CANNOT-ASSESS controls of the
#        shipped guard fixtures; every control must meet its expected verdict.
#   2. honesty analyze honesty/fixtures/honest honesty/corpus/pass --strict
#        the shipped honest guards must clear the anti-formality scanner.
#
# NOTE ON INVOCATION: the honesty package lives at guardrails/honesty and is
# importable only from its parent, guardrails/ (there is no top-level `honesty`
# module and guardrails/ has no __init__.py). It must be run with the
# guardrails/ directory on sys.path, i.e. `cd guardrails && python3 -m honesty
# ...`. Running `python3 -m honesty` from inside guardrails/honesty fails with
# "No module named honesty" (the package is not nested under itself).
#
# Exit-code contract (guardrails/honesty tri-state, issue #28): 0/1/2.
#   * model unavailable (cannot import) -> CANNOT-ASSESS (exit 2)
#   * any control fails / formality     -> NOT-OK        (exit 1)
#   * all controls pass, no formalities -> OK            (exit 0)
#
# Usage: bash scripts/check-negative-controls.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

honesty() { # honesty <args...>  (run from guardrails/ so `python3 -m honesty` resolves)
  ( cd "$root/guardrails" && python3 -m honesty "$@" )
}

# Probe the model first: if the honesty package cannot even be imported, the
# negative-control guard cannot assess anything (exit 2 — never a pass).
if ! honesty status 0 >/dev/null 2>&1; then
  echo "check-negative-controls: CANNOT-ASSESS — honesty model unavailable (python3 -m honesty failed)" >&2
  exit 2
fi

fail=0

echo "== honesty negative controls =="
if honesty negative honesty/manifest.negative.yaml; then
  echo "negative controls: OK"
else
  echo "negative controls: FAIL — a guard failed its own control" >&2
  fail=$((fail + 1))
fi

echo "== honesty anti-formality scan (strict) =="
if honesty analyze honesty/fixtures/honest honesty/corpus/pass --strict; then
  echo "anti-formality scan: OK"
else
  echo "anti-formality scan: FAIL — a shipped honest guard tripped the scanner" >&2
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-negative-controls: FAIL" >&2
  exit 1
fi
echo "check-negative-controls: OK — all negative controls pass, shipped guards clean"
exit 0
