#!/usr/bin/env bash
# branch-protection.sh — make branch protection a DECLARED, REPRODUCIBLE control.
#
#   apply   PUT the declared policy; idempotent (a second run changes nothing)
#   verify  READ BACK the live protection and diff it against the declaration
#   show    print the declared policy and the live protection side by side
#
# Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# CANNOT-ASSESS IS THE IMPORTANT ONE. `verify` needs the GitHub API. When it
# cannot reach it, the correct answer is *"I could not check"* — never a pass.
# A protection check that fails open is worse than no check at all: it reports
# safety it did not observe. This is the same defect class as #739, where an
# unreadable HEAD read back as `healthy` and silently disabled drift detection.
#
# Why this exists (#803 P0-3): `master` was measured UNPROTECTED — a 404 — while
# `AGENTS.md` claimed it was "protected by convention". The protection was then
# applied by an operator API call, which is a GR-5 breach (infrastructure is
# declared, never clicked) and is not reproducible. This script is the repair:
# the declared policy in governance/platform/branch-protection.yaml is the source
# of truth, `apply` makes the live state match it, and `verify` proves it did.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

POLICY="governance/platform/branch-protection.yaml"

die() { printf 'branch-protection: %s\n' "$1" >&2; exit "${2:-1}"; }

[ -f "$POLICY" ] || die "CANNOT-ASSESS — declared policy is missing: $POLICY" 2
command -v python3 >/dev/null 2>&1 || die "CANNOT-ASSESS — python3 not found" 2
command -v gh      >/dev/null 2>&1 || die "CANNOT-ASSESS — gh not found" 2

# Read repo/branch out of the declaration so the policy is the only place they
# are written: two sources for one fact drift.
read -r REPO BRANCH <<EOF
$(python3 - "$POLICY" <<'PY'
import sys, re
text = open(sys.argv[1]).read()
def field(name):
    m = re.search(rf"^{name}:\s*(\S+)\s*$", text, re.M)
    return m.group(1).strip('"\'') if m else ""
print(field("repo"), field("branch"))
PY
)
EOF
[ -n "${REPO:-}" ] && [ -n "${BRANCH:-}" ] || die "CANNOT-ASSESS — policy names no repo/branch" 2
[ "$REPO" = "." ] && REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" || true

python3 - "$POLICY" >/tmp/bp-declared.json <<'PY'
import sys, json
try:
    import yaml
except ImportError:
    sys.exit(2)
policy = yaml.safe_load(open(sys.argv[1]))
want, has = policy.get("protection", {}), policy.get("has", {})
print(json.dumps({"want": want, "has": has}))
PY
[ -s /tmp/bp-declared.json ] || die "CANNOT-ASSESS — could not parse the declared policy (PyYAML?)" 2

# Compare the declared policy against a live-state document.
#
# $1 = declared json (want/has), $2 = live json. Exits 1 on any drift.
#
# The comparator lives in its OWN file so that scripts/check-branch-protection.sh
# can PROVOKE the same code path this verify uses. If the gate proved a copy of
# the logic instead, the proof would say nothing about the path that actually
# runs -- measured lesson: a harness that asserted its mutation was caught while
# grepping a string a *passing* run also printed certified nothing at all.
compare() {
  python3 scripts/branch-protection-compare.py "$1" "$2"
}

live_state() {
  gh api "repos/$REPO/branches/$BRANCH/protection" 2>/tmp/bp-err.txt
  local rc=$?
  if [ $rc -ne 0 ]; then
    if grep -q "Branch not protected" /tmp/bp-err.txt 2>/dev/null; then
      printf '{"__unprotected__": true}\n' > /tmp/bp-live.json
      return 0
    fi
    return 2
  fi
}

case "${1:-verify}" in
  show)
    echo "declared ($POLICY):"; python3 -m json.tool /tmp/bp-declared.json 2>/dev/null | head -30
    echo; echo "live ($REPO@$BRANCH):"
    live_state; gh api "repos/$REPO/branches/$BRANCH/protection" 2>/dev/null | python3 -m json.tool 2>/dev/null | head -30 || cat /tmp/bp-err.txt
    ;;

  apply)
    # The PUT body is derived from the declaration, so `apply` cannot drift from
    # what `verify` checks -- one source, two consumers.
    python3 - /tmp/bp-declared.json >/tmp/bp-put.json <<'PY'
import json, sys
want = json.load(open(sys.argv[1]))["want"]
body = {}
for k, v in want.items():
    body[k] = v
print(json.dumps(body))
PY
    gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" \
       -H "Accept: application/vnd.github+json" --input /tmp/bp-put.json >/dev/null 2>/tmp/bp-apply-err.txt \
      || die "apply FAILED — $(head -c 200 /tmp/bp-apply-err.txt)"
    echo "branch-protection: applied the declared policy to $REPO@$BRANCH"
    ;;

  verify)
    live_state
    rc=$?
    if [ $rc -eq 2 ]; then
      # Fail-closed: never report a protection we did not observe.
      echo "branch-protection: CANNOT-ASSESS — the GitHub API was unreachable; the live protection was NOT observed" >&2
      head -c 200 /tmp/bp-err.txt >&2; echo >&2
      exit 2
    fi
    if grep -q "__unprotected__" /tmp/bp-live.json 2>/dev/null; then
      echo "branch-protection: NOT-OK — $REPO@$BRANCH is NOT PROTECTED (the platform enforces nothing)" >&2
      echo "                  remedy: bash scripts/branch-protection.sh apply" >&2
      exit 1
    fi
    gh api "repos/$REPO/branches/$BRANCH/protection" > /tmp/bp-live.json 2>/dev/null || { echo "branch-protection: CANNOT-ASSESS" >&2; exit 2; }
    compare /tmp/bp-declared.json /tmp/bp-live.json
    ;;

  *) die "usage: $0 {apply|verify|show}" 1 ;;
esac
