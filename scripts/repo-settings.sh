#!/usr/bin/env bash
# repo-settings.sh — make the repo merge-message policy a DECLARED,
# REPRODUCIBLE control (issue #1138, parent #803).
#
#   apply   PATCH the declared fields; idempotent (a second run changes nothing)
#   verify  READ BACK the live settings and diff them against the declaration
#
# Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# CANNOT-ASSESS IS THE IMPORTANT ONE, same doctrine as branch-protection.sh:
# `verify` needs the GitHub API. When it cannot reach it, the correct answer is
# *"I could not check"* — never a pass. A policy check that fails open is worse
# than no check at all: it reports safety it did not observe (the #739 class
# of defect: an unreadable HEAD read back as `healthy`).
#
# Why this exists: `squash_merge_commit_message=COMMIT_MESSAGES` was live on
# this repo, concatenating every per-commit message onto the squash commit
# instead of using the PR body — burying the ticket trailer mid-body and
# redding `check-isolation-landed` on every wave (#1119 #1044 #1121 #1126
# #1103, four more baselined in #1130). The setting was fixed by an operator
# API call 2026-09-17, which is a GR-5 breach (infrastructure is declared,
# never clicked) and not reproducible. This script is the repair: the declared
# policy in governance/platform/repo-settings.yaml is the source of truth,
# `apply` makes the live state match it, and `verify` proves it did.
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

POLICY="governance/platform/repo-settings.yaml"

die() { printf 'repo-settings: %s\n' "$1" >&2; exit "${2:-1}"; }

[ -f "$POLICY" ] || die "CANNOT-ASSESS — declared policy is missing: $POLICY" 2
command -v python3 >/dev/null 2>&1 || die "CANNOT-ASSESS — python3 not found" 2
command -v gh      >/dev/null 2>&1 || die "CANNOT-ASSESS — gh not found" 2

# Read repo out of the declaration so it is the only place it is written: two
# sources for one fact drift.
REPO="$(python3 - "$POLICY" <<'PY'
import sys, re
text = open(sys.argv[1]).read()
m = re.search(r"^repo:\s*(\S+)\s*$", text, re.M)
print(m.group(1).strip('"\'') if m else "")
PY
)"
[ -n "${REPO:-}" ] || die "CANNOT-ASSESS — policy names no repo" 2

python3 - "$POLICY" >/tmp/rs-declared.json <<'PY'
import sys, json
try:
    import yaml
except ImportError:
    sys.exit(2)
policy = yaml.safe_load(open(sys.argv[1]))
want = policy.get("settings", {})
print(json.dumps({"want": want}))
PY
[ -s /tmp/rs-declared.json ] || die "CANNOT-ASSESS — could not parse the declared policy (PyYAML?)" 2

# Compare the declared policy against a live-state document.
#
# The comparator lives in its OWN file so that scripts/check-repo-settings.sh
# can PROVOKE the same code path this verify uses — a provocation that drives
# a different code path proves nothing about the path that actually runs.
compare() {
  python3 scripts/repo-settings-compare.py "$1" "$2"
}

live_state() {
  gh api "repos/$REPO" 2>/tmp/rs-err.txt > /tmp/rs-live-raw.json
  local rc=$?
  if [ $rc -ne 0 ]; then
    return 2
  fi
  python3 - /tmp/rs-live-raw.json /tmp/rs-declared.json >/tmp/rs-live.json <<'PY'
import json, sys
raw = json.load(open(sys.argv[1]))
want = json.load(open(sys.argv[2]))["want"]
# Only the declared fields, as scalars — the repos API already returns them flat.
print(json.dumps({k: raw.get(k) for k in want}))
PY
}

case "${1:-verify}" in
  show)
    echo "declared ($POLICY):"; python3 -m json.tool /tmp/rs-declared.json 2>/dev/null
    echo; echo "live ($REPO):"
    live_state && python3 -m json.tool /tmp/rs-live.json 2>/dev/null || cat /tmp/rs-err.txt
    ;;

  apply)
    # The PATCH body is derived from the declaration, so `apply` cannot drift
    # from what `verify` checks — one source, two consumers. Idempotent: PATCH
    # of fields already at their declared value changes nothing.
    python3 - /tmp/rs-declared.json >/tmp/rs-patch.json <<'PY'
import json, sys
want = json.load(open(sys.argv[1]))["want"]
print(json.dumps(want))
PY
    gh api -X PATCH "repos/$REPO" \
       -H "Accept: application/vnd.github+json" --input /tmp/rs-patch.json >/dev/null 2>/tmp/rs-apply-err.txt \
      || die "apply FAILED — $(head -c 200 /tmp/rs-apply-err.txt)"
    echo "repo-settings: applied the declared policy to $REPO"
    ;;

  verify)
    live_state
    rc=$?
    if [ $rc -eq 2 ]; then
      # Fail-closed: never report settings we did not observe.
      echo "repo-settings: CANNOT-ASSESS — the GitHub API was unreachable; the live settings were NOT observed" >&2
      head -c 200 /tmp/rs-err.txt >&2; echo >&2
      exit 2
    fi
    compare /tmp/rs-declared.json /tmp/rs-live.json
    ;;

  *) die "usage: $0 {apply|verify|show}" 1 ;;
esac
