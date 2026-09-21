#!/usr/bin/env bash
# check-python-syntax.sh — every tracked Python file must compile (gate of record).
#
# Gap this closes, hit for real on 2026-09-13: `fleet/terminal.py` was committed
# with a syntax error and `make verify` reported "PASS (19 of 19)". The gate
# compiled shell (bash -n), parsed YAML, JSON and docs — but never compiled the
# Python the fleet actually runs. A syntax-broken terminal means the launcher,
# the control plane and every subprocess invocation of it fail, while the gate of
# record stays green: the exact false-green this repo's doctrine forbids (GR-12).
#
# Scope: every `*.py` tracked outside `vendor/` (a pinned submodule is not ours).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-python-syntax.sh
#
# ---knowledge---
# module_id: scripts.check-python-syntax
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, no-false-green]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-python-syntax: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

mapfile -t files < <(git ls-files '*.py' | grep -v '^vendor/' || true)
if [ "${#files[@]}" -eq 0 ]; then
  echo "check-python-syntax: CANNOT-ASSESS — no tracked Python files found" >&2
  exit 2
fi

failed=0
for file in "${files[@]}"; do
  if ! python3 -m py_compile "$file" 2>/tmp/check-python-syntax.$$.err; then
    printf '  FAIL  %s\n' "$file" >&2
    sed 's/^/        /' /tmp/check-python-syntax.$$.err >&2
    failed=$((failed + 1))
  fi
done
rm -f /tmp/check-python-syntax.$$.err

if [ "$failed" -gt 0 ]; then
  echo "check-python-syntax: FAIL — $failed file(s) do not compile" >&2
  exit 1
fi

# Non-vacuous: a broken file must be detected, or the check is a formality.
probe="$(mktemp --suffix=.py)"
trap 'rm -f "$probe"' EXIT
printf 'def broken(:\n' > "$probe"
if python3 -m py_compile "$probe" >/dev/null 2>&1; then
  echo "check-python-syntax: FAIL — a deliberately broken file compiled (vacuous check)" >&2
  exit 1
fi

echo "check-python-syntax: OK (${#files[@]} tracked file(s) compile)"
exit 0
