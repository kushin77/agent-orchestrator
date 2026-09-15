#!/usr/bin/env bash
# check-branch-protection.sh — the platform-level enforcement of the gate of record.
#
# The defect this exists for (#803, P0-3). Measured 2026-09-15:
#
#     gh api repos/kushin77/agent-orchestrator/branches/master/protection
#     {"message":"Branch not protected", ..., "status":404}
#
# while `AGENTS.md` stated "`master` is protected by convention" and "Branch
# protection as code ships with issue #6". `master` was OPEN, and no tooling
# existed. The product has a gate of record that no platform control requires
# anyone to run -- the same "declared but not enforced" defect this repo's own
# governance had been cataloguing at the fleet level, one level up at the
# platform.
#
# WHAT THIS GATE PROVES, AND THE HALF THAT MATTERS MOST
# A check that a branch is protected is easy to write and easy to write *wrong*:
# if the comparator cannot fail, the check is a formality (GR-12), and if it
# cannot reach the API and reports success, it fails OPEN -- reporting safety it
# did not observe (the #739 class of defect).
#
# So this gate has three parts, and the SECOND is the point:
#
#   1. STRUCTURAL -- the declaration exists and names the fields it protects.
#   2. PROVOKED   -- the comparator is driven with a live-state fixture that
#                    MATCHES the declaration (must exit 0) and one that DRIFTS
#                    (must exit 1, naming the field). This runs OFFLINE and
#                    deterministically, and it exercises the SAME comparator
#                    scripts/branch-protection.sh verify uses -- a provocation
#                    that drives a different code path proves nothing about the
#                    path that actually runs.
#   3. LIVE       -- the real protection is read back. Offline, the verdict is
#                    CANNOT-ASSESS (2), NEVER a pass.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-branch-protection.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

POLICY="governance/platform/branch-protection.yaml"
COMPARE="scripts/branch-protection-compare.py"
fail=0

command -v python3 >/dev/null 2>&1 || { echo "check-branch-protection: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

# 1. STRUCTURAL -- the declaration is present and actually declares protection.
[ -f "$POLICY" ] || { echo "check-branch-protection: FAIL — the declared policy is missing: $POLICY" >&2; exit 1; }
[ -f "$COMPARE" ] || { echo "check-branch-protection: FAIL — the comparator is missing: $COMPARE" >&2; exit 1; }

python3 - "$POLICY" >/tmp/cbp-declared.json <<'PY'
import json, sys
try:
    import yaml
except ImportError:
    print("check-branch-protection: CANNOT-ASSESS — PyYAML missing", file=sys.stderr)
    sys.exit(2)
policy = yaml.safe_load(open(sys.argv[1]))
print(json.dumps({"want": policy.get("protection", {}), "has": policy.get("has", {})}))
PY
[ -s /tmp/cbp-declared.json ] || exit 2

# The three controls that make `master` un-rewritable. If the declaration stops
# declaring one of them, protection has been silently weakened -- so their
# PRESENCE in the declaration is asserted by name, not merely that a file exists.
for required in required_linear_history allow_force_pushes allow_deletions; do
  if ! python3 -c "
import json,sys
want=json.load(open('/tmp/cbp-declared.json'))['want']
sys.exit(0 if '$required' in want else 1)
" ; then
    echo "check-branch-protection: FAIL — the declaration no longer protects '$required'" >&2
    fail=1
  fi
done

# 2. PROVOKED -- the comparator must be able to fail, and must not fail spuriously.
# A scratch directory for the provoked fixtures.
#
# NOT the standard `mktemp -d` template, which every future lane will reach for.
# `scripts/check-docs.sh` scans *.sh/*.py/*.go for a marker alternation whose
# last branch is three X characters followed by a TRAILING word boundary and no
# leading one, so it matches the tail of any run of six X characters. `mktemp`
# requires at least three consecutive X characters, so its standard template is
# unsatisfiable under that scanner: the scanner is right that the file contains
# the sequence, and what it found is not an unfinished marker at all. Measured
# here: this lane failed `docs-lint` with "FAIL ./scripts/check-branch-protection.sh
# (unfinished marker)" for exactly that line -- and then again for the comment
# that explains it, which is why this text spells the template out in words.
# Python's tempfile needs no such placeholder.
work="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="cbp-"))')" || exit 2
trap 'rm -rf "$work"' EXIT

# 2a. A live state that MATCHES the declaration must pass.
python3 - "$work/match.json" <<'PY'
import json, sys
want = json.load(open('/tmp/cbp-declared.json'))['want']
live = {}
for key, value in want.items():
    live[key] = None if value is None else {"enabled": value}
json.dump(live, open(sys.argv[1], "w"))
PY
python3 "$COMPARE" /tmp/cbp-declared.json "$work/match.json" >"$work/match.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "check-branch-protection: FAIL — a MATCHING live state was reported as drift (rc=$rc)" >&2
  sed 's/^/    /' "$work/match.log" >&2
  fail=1
fi

# 2b. A live state whose protection has been REMOVED must be caught, by name.
#     This is the real-world failure: someone turns protection off.
python3 - "$work/drift.json" <<'PY'
import json, sys
want = json.load(open('/tmp/cbp-declared.json'))['want']
live = {}
for key, value in want.items():
    live[key] = None if value is None else {"enabled": value}
# the provocation: force-pushes allowed again, linear history off.
live["allow_force_pushes"] = {"enabled": True}
live["required_linear_history"] = {"enabled": False}
json.dump(live, open(sys.argv[1], "w"))
PY
python3 "$COMPARE" /tmp/cbp-declared.json "$work/drift.json" >"$work/drift.log" 2>&1
rc=$?
if [ $rc -ne 1 ]; then
  echo "check-branch-protection: FAIL — REMOVED protection was NOT caught (rc=$rc, expected 1)" >&2
  sed 's/^/    /' "$work/drift.log" >&2
  fail=1
elif ! grep -q "allow_force_pushes" "$work/drift.log"; then
  echo "check-branch-protection: FAIL — drift was caught but the FIELD was not named" >&2
  sed 's/^/    /' "$work/drift.log" >&2
  fail=1
else
  echo "  OK  the comparator CATCHES removed protection and names the field:"
  grep "DRIFT" "$work/drift.log" | sed 's/^/      /'
fi

# 2c. A wholly UNPROTECTED branch must be caught -- the exact state measured.
printf '{"__unprotected__": true}\n' > "$work/unprotected.json"
python3 "$COMPARE" /tmp/cbp-declared.json "$work/unprotected.json" >"$work/unp.log" 2>&1
rc=$?
if [ $rc -ne 1 ]; then
  echo "check-branch-protection: FAIL — an UNPROTECTED branch was NOT caught (rc=$rc, expected 1)" >&2
  fail=1
else
  echo "  OK  an UNPROTECTED branch is caught (the 404 state that shipped)"
fi

# 3. LIVE -- read back the real protection.
#
# Offline (or otherwise unobservable) is CANNOT-ASSESS, and the gate must EXIT 2
# for it. This is deliberate and was found by mutation, not by review: the first
# version of this gate printed "CANNOT-ASSESS — not a pass" and then fell through
# to `OK — declared, provoked and read back` with exit 0. It said the right thing
# and did the opposite, reporting a read-back that had not happened -- the exact
# fail-open this repo has been fixing all week (#739: an unreadable HEAD read
# back as `healthy`). Saying you could not check is only honest if the exit code
# agrees.
cannot_assess=0
bash scripts/branch-protection.sh verify >"$work/live.log" 2>&1
rc=$?
case "$rc" in
  0) echo "  OK  the LIVE protection matches the declaration"
     grep -E "^  OK" "$work/live.log" | sed 's/^/      /' ;;
  2) echo "  CANNOT-ASSESS  the live protection was NOT observed — NOT a pass, and this gate exits 2"
     grep -v "^$" "$work/live.log" | head -3 | sed 's/^/      /'
     cannot_assess=1 ;;
  *) echo "check-branch-protection: FAIL — the LIVE protection has drifted from the declaration" >&2
     sed 's/^/    /' "$work/live.log" >&2
     fail=1 ;;
esac

# Order matters: a real, provoked failure outranks an unobserved live state, so a
# gate that found a genuine defect still reports NOT-OK (1) rather than the
# softer 2.
if [ "$fail" -ne 0 ]; then
  echo "check-branch-protection: NOT-OK — the declaration or its enforcement is defective"
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-branch-protection: CANNOT-ASSESS — declared and provoked, but the LIVE protection was NOT observed"
  exit 2
fi
echo "check-branch-protection: OK — declared, provoked and read back"
exit 0
