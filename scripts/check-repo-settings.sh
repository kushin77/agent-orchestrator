#!/usr/bin/env bash
# check-repo-settings.sh — the platform-level enforcement of the merge-message
# policy (issue #1138, parent #803).
#
# The defect this exists for. Every squash merge by a session using `gh pr
# merge --squash` was producing a master commit whose ticket trailer was
# buried mid-body, because the live repo setting was
# `squash_merge_commit_message=COMMIT_MESSAGES` — concatenating every
# per-commit message instead of using the PR body — so `check-isolation-
# landed` went red on a fresh set of commits after every wave (#1119 #1044
# #1121 #1126 #1103, four more baselined in #1130). The setting was fixed by
# an OPERATOR API call, which is a GR-5 breach (infrastructure is declared,
# never clicked) and not reproducible: a fork, a restore or a second repo
# diverges silently, exactly the branch-protection defect (#803 P0-3) one
# level up.
#
# WHAT THIS GATE PROVES, AND THE HALF THAT MATTERS MOST
# A check that a setting is correct is easy to write and easy to write WRONG:
# if the comparator cannot fail, the check is a formality (GR-12), and if it
# cannot reach the API and reports success, it fails OPEN — reporting safety
# it did not observe (the #739 class of defect).
#
# So this gate has three parts, mirroring scripts/check-branch-protection.sh,
# and the SECOND is the point:
#
#   1. STRUCTURAL -- the declaration exists and names the fields it declares.
#   2. PROVOKED   -- the comparator is driven with a live-state fixture that
#                    MATCHES the declaration (must exit 0) and one that
#                    DRIFTS (must exit 1, naming the field). This runs
#                    OFFLINE and deterministically, and it exercises the SAME
#                    comparator scripts/repo-settings.sh verify uses -- a
#                    provocation that drives a different code path proves
#                    nothing about the path that actually runs.
#   3. LIVE       -- the real settings are read back. Offline, the verdict is
#                    CANNOT-ASSESS (2), NEVER a pass.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-repo-settings.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

POLICY="governance/platform/repo-settings.yaml"
COMPARE="scripts/repo-settings-compare.py"
fail=0

command -v python3 >/dev/null 2>&1 || { echo "check-repo-settings: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

# Precondition: an authenticated `gh` is what the LIVE section (part 3) needs
# to read settings back. Hoisted ABOVE the offline PROVOKED section (part 2)
# on purpose — see scripts/check-branch-protection.sh for the mirrored rule
# and the measured defect (#1313): on a host where `gh` is installed but
# unauthenticated, the PROVOKED section ran anyway and reported FAIL for a
# comparator this run was never able to reach.
# shellcheck source=scripts/lib/preconditions.sh
source "$root/scripts/lib/preconditions.sh"
require_gh_auth gh-unauthenticated

# 1. STRUCTURAL -- the declaration is present and actually declares settings.
[ -f "$POLICY" ] || { echo "check-repo-settings: FAIL — the declared policy is missing: $POLICY" >&2; exit 1; }
[ -f "$COMPARE" ] || { echo "check-repo-settings: FAIL — the comparator is missing: $COMPARE" >&2; exit 1; }

python3 - "$POLICY" >/tmp/crs-declared.json <<'PY'
import json, sys
try:
    import yaml
except ImportError:
    print("check-repo-settings: CANNOT-ASSESS — PyYAML missing", file=sys.stderr)
    sys.exit(2)
policy = yaml.safe_load(open(sys.argv[1]))
print(json.dumps({"want": policy.get("settings", {})}))
PY
[ -s /tmp/crs-declared.json ] || exit 2

# The two fields that make the squash commit carry its trailer. If the
# declaration stops declaring one of them, the policy has been silently
# weakened -- so their PRESENCE in the declaration is asserted by name, not
# merely that a file exists.
for required in squash_merge_commit_title squash_merge_commit_message; do
  if ! python3 -c "
import json,sys
want=json.load(open('/tmp/crs-declared.json'))['want']
sys.exit(0 if '$required' in want else 1)
" ; then
    echo "check-repo-settings: FAIL — the declaration no longer declares '$required'" >&2
    fail=1
  fi
done

# 2. PROVOKED -- the comparator must be able to fail, and must not fail
# spuriously.
work="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="crs-"))')" || exit 2
trap 'rm -rf "$work"' EXIT

# 2a. A live state that MATCHES the declaration must pass.
python3 - "$work/match.json" <<'PY'
import json, sys
want = json.load(open('/tmp/crs-declared.json'))['want']
json.dump(want, open(sys.argv[1], "w"))
PY
python3 "$COMPARE" /tmp/crs-declared.json "$work/match.json" >"$work/match.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "check-repo-settings: FAIL — a MATCHING live state was reported as drift (rc=$rc)" >&2
  sed 's/^/    /' "$work/match.log" >&2
  fail=1
fi

# 2b. A live state that REVERTS to the concatenated-message shape must be
#     caught, by name. This is the real-world failure this gate exists for.
python3 - "$work/drift.json" <<'PY'
import json, sys
want = json.load(open('/tmp/crs-declared.json'))['want']
live = dict(want)
live["squash_merge_commit_message"] = "COMMIT_MESSAGES"
json.dump(live, open(sys.argv[1], "w"))
PY
python3 "$COMPARE" /tmp/crs-declared.json "$work/drift.json" >"$work/drift.log" 2>&1
rc=$?
if [ $rc -ne 1 ]; then
  echo "check-repo-settings: FAIL — a REVERTED message policy was NOT caught (rc=$rc, expected 1)" >&2
  sed 's/^/    /' "$work/drift.log" >&2
  fail=1
elif ! grep -q "squash_merge_commit_message" "$work/drift.log"; then
  echo "check-repo-settings: FAIL — drift was caught but the FIELD was not named" >&2
  sed 's/^/    /' "$work/drift.log" >&2
  fail=1
else
  echo "  OK  the comparator CATCHES a reverted message policy and names the field:"
  grep "DRIFT" "$work/drift.log" | sed 's/^/      /'
fi

# 2c. A malformed/empty live document must be caught, not silently pass.
printf '{}\n' > "$work/empty.json"
python3 "$COMPARE" /tmp/crs-declared.json "$work/empty.json" >"$work/empty.log" 2>&1
rc=$?
if [ $rc -ne 1 ]; then
  echo "check-repo-settings: FAIL — an EMPTY live document was NOT caught (rc=$rc, expected 1)" >&2
  fail=1
else
  echo "  OK  an empty live document is caught (every declared field reads as missing)"
fi

# 3. LIVE -- read back the real settings.
#
# Offline (or otherwise unobservable) is CANNOT-ASSESS, and the gate must
# EXIT 2 for it -- never a pass for a read-back that did not happen (the
# #739 fail-open class this repo has been fixing all week).
cannot_assess=0
bash scripts/repo-settings.sh verify >"$work/live.log" 2>&1
rc=$?
case "$rc" in
  0) echo "  OK  the LIVE settings match the declaration"
     grep -E "^  OK" "$work/live.log" | sed 's/^/      /' ;;
  2) echo "  CANNOT-ASSESS  the live settings were NOT observed — NOT a pass, and this gate exits 2"
     grep -v "^$" "$work/live.log" | head -3 | sed 's/^/      /'
     cannot_assess=1 ;;
  *) echo "check-repo-settings: FAIL — the LIVE settings have drifted from the declaration" >&2
     sed 's/^/    /' "$work/live.log" >&2
     fail=1 ;;
esac

# Order matters: a real, provoked failure outranks an unobserved live state,
# so a gate that found a genuine defect still reports NOT-OK (1) rather than
# the softer 2 — but only once the precondition at the top of this file has
# already confirmed the provocations ran for real. When `gh` is unauthenticated
# this gate never reaches here at all (rc 2, by name, above); it does not fall
# through to this ordering.
if [ "$fail" -ne 0 ]; then
  echo "check-repo-settings: NOT-OK — the declaration or its enforcement is defective"
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-repo-settings: CANNOT-ASSESS — declared and provoked, but the LIVE settings were NOT observed"
  exit 2
fi
echo "check-repo-settings: OK — declared, provoked and read back"
exit 0
