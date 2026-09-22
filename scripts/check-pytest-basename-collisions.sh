#!/usr/bin/env bash
# check-pytest-basename-collisions.sh — guard against NEW pytest module
# basename collisions across the declared per-suite corpus (issue #1935).
#
# THE DEFECT THIS EXISTS FOR
#   `scripts/pytest-suites.txt` declares suites that are run only in isolation
#   (scripts/check-pytest-suites.sh, scripts/run-pytest-suites.sh) precisely
#   because sibling suites share test-module basenames (test_dispatch.py,
#   test_registry.py, ...) and sibling source modules share basenames too
#   (model.py x15 under governance/). That is documented, accepted, and NOT
#   fixable by adding __init__.py: several colliding directories
#   (gateway/sme-routing, governance/cto-overlay, governance/lane-record,
#   governance/lessons-sync, governance/onboarding/shared-frontend) have
#   hyphenated names and cannot be Python packages, and the repo's suites
#   deliberately rely on flat sys.path bootstrap (`from conftest import ...`,
#   pytest.ini's own header, issue #1212) which packaging would break anyway.
#
#   What IS a real gap: nothing stops the collision set from silently
#   growing. This check baselines the current duplicate basenames (both
#   test_*.py under each declared suite's tests/ dir, and top-level *.py
#   source modules under each suite dir) and fails if a NEW collision is
#   introduced beyond the recorded baseline, so a future PR that adds a
#   colliding basename gets caught instead of just silently degrading any
#   non-sanctioned bulk `pytest <dir> <dir>` invocation someone happens to run.
#
# WHAT THIS DOES NOT DO
#   It does not, and cannot, make `pytest gateway governance -q` (no
#   per-suite scoping) collect cleanly — that would require the architecture
#   change described above. Use `scripts/check-pytest-suites.sh` for the
#   sanctioned, gated pytest run.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

BASELINE="scripts/pytest-basename-collisions-baseline.txt"
CURRENT="$(mktemp)"
trap 'rm -f "$CURRENT"' EXIT

python3 - "$CURRENT" <<'PY'
import sys
from pathlib import Path

manifest = Path("scripts/pytest-suites.txt")
suites = [
    line.strip()
    for line in manifest.read_text().splitlines()
    if line.strip() and not line.strip().startswith("#")
]

basenames = {}  # basename -> sorted set of paths
for suite in suites:
    suite_dir = Path(suite)
    candidates = []
    tests_dir = suite_dir / "tests"
    if tests_dir.is_dir():
        candidates.extend(sorted(tests_dir.glob("test_*.py")))
    if suite_dir.is_dir():
        candidates.extend(
            p for p in sorted(suite_dir.glob("*.py")) if p.name != "__init__.py"
        )
    for p in candidates:
        basenames.setdefault(p.name, set()).add(str(p))

out = Path(sys.argv[1])
lines = []
for name in sorted(basenames):
    paths = basenames[name]
    if len(paths) > 1:
        lines.append(name + ": " + ", ".join(sorted(paths)))
out.write_text("\n".join(lines) + ("\n" if lines else ""))
PY

if [ ! -f "$BASELINE" ]; then
  echo "check-pytest-basename-collisions: FAIL — $BASELINE missing; run this" \
       "script's generator to seed it (see comment header)." >&2
  exit 1
fi

if ! diff -u "$BASELINE" "$CURRENT" > /tmp/pytest-basename-collisions.diff 2>&1; then
  NEW=$(grep -c '^+[^+]' /tmp/pytest-basename-collisions.diff || true)
  if [ "${NEW:-0}" -gt 0 ]; then
    echo "check-pytest-basename-collisions: FAIL — new pytest module basename" \
         "collision(s) introduced beyond the recorded baseline:" >&2
    grep '^+[^+]' /tmp/pytest-basename-collisions.diff >&2
    echo "If this addition is intentional, update $BASELINE." >&2
    exit 1
  fi
fi

echo "check-pytest-basename-collisions: OK — no new collisions beyond baseline"
