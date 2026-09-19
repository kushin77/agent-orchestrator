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
# So this gate has four parts, and the SECOND is the point:
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
#   4. PRODUCED   -- the required context must have a PRODUCER (#1357). Parts 1-3
#                    prove the context is REQUIRED and that nothing can silently
#                    drop it; none of them says anything writes it. Measured
#                    2026-09-18: live protection required `ao/gate-of-record`,
#                    nothing on the path that evaluates a pull request posted it,
#                    every PR head read BLOCKED, merges proceeded through the
#                    admin bypass (`enforce_admins: false`), and BOTH policing
#                    gates were green. A required check with no producer is a
#                    bypass generator, not a control, so this part drives the
#                    same probe scripts/check-gate-status.sh runs for itself
#                    (`--producer-probe`) and refuses by name.
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

# Precondition: an authenticated `gh` is what the LIVE section (part 3) needs
# to read protection back. This is hoisted ABOVE the offline PROVOKED section
# (part 2) on purpose: a gate whose precondition is absent is CANNOT-ASSESS
# for the WHOLE gate, including its provocations — a provocation is judged
# only when the live half could run. Measured (#1313): on a host where `gh`
# is installed but unauthenticated, the PROVOKED section still ran and its
# rc-2-from-elsewhere was compared against the expected rc 1, reporting FAIL
# for a comparator this run was never able to reach.
# shellcheck source=scripts/lib/preconditions.sh
source "$root/scripts/lib/preconditions.sh"
require_gh_auth gh-unauthenticated

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

# The fixture builder mirrors GitHub's wire shape: boolean policy fields arrive
# as {"enabled": bool}; required_status_checks is its OWN nested shape (strict +
# contexts, plus server-generated fields the comparator ignores), never wrapped
# in "enabled" — wrapping it there would compare against a key the comparator
# never reads and hide real drift on that field.
build_live() {
python3 - "$1" "$2" <<'PY'
import json, sys
want = json.load(open('/tmp/cbp-declared.json'))['want']
overrides = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
live = {}
for key, value in want.items():
    if key == "required_status_checks":
        live[key] = None if value is None else {
            "url": "https://api.example/required_status_checks",
            "strict": value.get("strict", False),
            "contexts": list(value.get("contexts", [])),
            "contexts_url": "https://api.example/contexts",
            "checks": [{"context": c, "app_id": None} for c in value.get("contexts", [])],
        }
    else:
        live[key] = None if value is None else {"enabled": value}
live.update(overrides)
json.dump(live, open(sys.argv[1], "w"))
PY
}

# 2a. A live state that MATCHES the declaration must pass.
build_live "$work/match.json" "{}"
python3 "$COMPARE" /tmp/cbp-declared.json "$work/match.json" >"$work/match.log" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "check-branch-protection: FAIL — a MATCHING live state was reported as drift (rc=$rc)" >&2
  sed 's/^/    /' "$work/match.log" >&2
  fail=1
fi

# 2b. A live state whose protection has been REMOVED must be caught, by name.
#     This is the real-world failure: someone turns protection off.
build_live "$work/drift.json" '{"allow_force_pushes": {"enabled": true}, "required_linear_history": {"enabled": false}}'
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

# 2b2. If the declaration REQUIRES a status-check context, a live state that
#      has silently dropped that context (protection object still present,
#      but the gate it names is gone) must be caught by name too. This is the
#      #724 shape one layer down: a required-checks field that can drift
#      without the comparator ever noticing is an inert control.
declared_rsc="$(python3 -c "
import json
want = json.load(open('/tmp/cbp-declared.json'))['want']
print('yes' if want.get('required_status_checks') else 'no')
")"
if [ "$declared_rsc" = "yes" ]; then
  build_live "$work/rsc-drift.json" '{"required_status_checks": {"strict": false, "contexts": []}}'
  python3 "$COMPARE" /tmp/cbp-declared.json "$work/rsc-drift.json" >"$work/rsc-drift.log" 2>&1
  rc=$?
  if [ $rc -ne 1 ]; then
    echo "check-branch-protection: FAIL — a DROPPED required status context was NOT caught (rc=$rc, expected 1)" >&2
    sed 's/^/    /' "$work/rsc-drift.log" >&2
    fail=1
  elif ! grep -q "required_status_checks" "$work/rsc-drift.log"; then
    echo "check-branch-protection: FAIL — the dropped context drift was not named by field" >&2
    fail=1
  else
    echo "  OK  a required status context silently dropped is caught and named:"
    grep "DRIFT" "$work/rsc-drift.log" | sed 's/^/      /'
  fi
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

# 4. PRODUCED -- a REQUIRED status context must have a producer (#1357).
#
#    Parts 1-3 prove the context is required and that the declaration cannot
#    silently lose it. None of them proves anything PRODUCES it, which is how a
#    required check becomes an inert control whose own gates are green. This part
#    asks the producing question, and it asks it through the same implementation
#    scripts/check-gate-status.sh runs for itself (`--producer-probe`): a second,
#    hand-rolled copy of the rule here would prove nothing about the rule that
#    actually polices the context.
#
# 4a. PROVOKED -- every half must be able to fail, and the not-REQUIRED case must
#     produce NO finding at all. A rule that fires on every tree is as useless as
#     one that never fires, so both directions are planted below.
work_probe() { # work_probe <dir> -- sets p_rc and p_out
  p_out="$(bash scripts/check-gate-status.sh --producer-probe --root "$1" 2>&1)"
  p_rc=$?
}

holds() { # holds <text> <needle> -- bash-native containment: it cannot kill a producer
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# The fixture plants a policy plus the trigger/build-config pair the rule derives
# the pull-request verdict path from, so the provocation exercises the derivation
# rather than being handed its answer.
mk_verdict_fixture() { # <dir> <contexts-list> <disabled> <producer-step>
  mkdir -p "$1/governance/platform" "$1/infra/cloudbuild" "$1/scripts"
  {
    printf 'schema: ao.branch-protection/v1\n'
    printf 'protection:\n'
    printf '  required_status_checks:\n'
    printf '    strict: false\n'
    printf '    contexts: %s\n' "$2"
    printf '  allow_force_pushes: false\n'
  } > "$1/governance/platform/branch-protection.yaml"
  {
    printf 'name: fixture-pr-verify\n'
    printf 'disabled: %s\n' "$3"
    printf 'repositoryEventConfig:\n'
    printf '  pullRequest:\n'
    printf '    branch: ^master$\n'
    printf 'filename: infra/cloudbuild/verify.yaml\n'
  } > "$1/infra/cloudbuild/pr-trigger.yaml"
  {
    printf 'steps:\n'
    printf '  - id: verify\n'
    printf '    entrypoint: bash\n'
    printf '    args:\n'
    printf '      - -lc\n'
    printf '      - |\n'
    printf '        make verify\n'
    if [ -n "$4" ]; then printf '        %s\n' "$4"; fi
  } > "$1/infra/cloudbuild/verify.yaml"
  printf '# fixture stand-in for the poster\nCONTEXT="${AO_GATE_CONTEXT:-ao/gate-of-record}"\n' > "$1/scripts/gate-status.sh"
}

expect_probe() { # expect_probe <label> <dir> <want-rc> <needle>
  work_probe "$2"
  if [ "$p_rc" -ne "$3" ]; then
    echo "check-branch-protection: FAIL — $1: producer probe rc=$p_rc, expected $3" >&2
    printf '%s\n' "$p_out" | sed 's/^/    /' >&2
    fail=1
    return 1
  fi
  if ! holds "$p_out" "$4"; then
    echo "check-branch-protection: FAIL — $1: refused without naming '$4'" >&2
    printf '%s\n' "$p_out" | sed 's/^/    /' >&2
    fail=1
    return 1
  fi
  echo "  OK  $1 (rc $3)"
  return 0
}

expect_no_finding() { # expect_no_finding <label> <dir> <forbidden-needle>
  work_probe "$2"
  if [ "$p_rc" -ne 0 ]; then
    echo "check-branch-protection: FAIL — $1: nothing is REQUIRED, so the probe must pass (rc=$p_rc, expected 0)" >&2
    printf '%s\n' "$p_out" | sed 's/^/    /' >&2
    fail=1
    return 1
  fi
  if holds "$p_out" "$3"; then
    echo "check-branch-protection: FAIL — $1: the rule FIRED on a tree where nothing is required (it matches everything)" >&2
    printf '%s\n' "$p_out" | sed 's/^/    /' >&2
    fail=1
    return 1
  fi
  echo "  OK  $1 (rc 0, no finding)"
  return 0
}

# The measured defect: REQUIRED, and nothing posts it on the PR verdict path.
mk_verdict_fixture "$work/inert" '[ao/gate-of-record]' true ''
expect_probe "the INERT state is refused BY NAME" "$work/inert" 1 'REQUIRED-BUT-UNPRODUCED'
expect_probe "and the refusal names the CONTEXT" "$work/inert" 1 'ao/gate-of-record'

# The healthy state must pass, or the rule is a red on a working repository.
mk_verdict_fixture "$work/healthy" '[ao/gate-of-record]' false 'bash scripts/gate-status.sh post --sha 1 --rc 0'
expect_no_finding "a HEALTHY state (producer on the PR verdict path) passes" "$work/healthy" 'REQUIRED-BUT-UNPRODUCED'

# A producer OFF the PR path is the shape the real repository has: the landing
# driver posts, and a PR head is still ungated, so it must NOT satisfy the rule.
mk_verdict_fixture "$work/offpath" '[ao/gate-of-record]' false ''
mkdir -p "$work/offpath/governance/landing"
printf 'argv=["bash","scripts/gate-status.sh","post"]\n' > "$work/offpath/governance/landing/ports.py"
expect_probe "a producer only OFF the PR verdict path is refused" "$work/offpath" 1 'REQUIRED-BUT-UNPRODUCED'

# A TEST is not a producer: it proves the poster CAN be called, not that anything
# calls it when a verdict is made. Counting one would certify the defect away.
mk_verdict_fixture "$work/testonly" '[ao/gate-of-record]' false ''
mkdir -p "$work/testonly/governance/landing/tests"
printf 'argv=("bash", "scripts/gate-status.sh", "post")\n' > "$work/testonly/governance/landing/tests/conftest.py"
expect_probe "a TEST calling the poster is NOT counted as a producer" "$work/testonly" 1 'REQUIRED-BUT-UNPRODUCED'

# A disabled (flag-gated OFF) PR path produces nothing, so a producer on it is not
# yet satisfiable: rc 2, named, NEVER 0.
mk_verdict_fixture "$work/gatedoff" '[ao/gate-of-record]' true 'bash scripts/gate-status.sh post --sha 1 --rc 0'
expect_probe "a producer on a flag-gated-OFF PR path is CANNOT-ASSESS, not a pass" "$work/gatedoff" 2 'REQUIRED-BUT-GATED-OFF'

# With no PR verdict path declared at all, the producing question cannot be
# answered: rc 2, named, NEVER 0.
mk_verdict_fixture "$work/nolocate" '[ao/gate-of-record]' false 'bash scripts/gate-status.sh post --sha 1 --rc 0'
rm -f "$work/nolocate/infra/cloudbuild/pr-trigger.yaml"
expect_probe "no declared PR verdict path is CANNOT-ASSESS, not a pass" "$work/nolocate" 2 'REQUIRED-BUT-UNLOCATABLE'

# THE NEGATIVE CONTROL. If nothing is REQUIRED no producer is owed, and the rule
# must produce no finding at all -- otherwise it is a rule that matches
# everything and would be ignored.
mk_verdict_fixture "$work/unrequired" '[]' false ''
expect_no_finding "nothing REQUIRED -> the producer rule does not fire" "$work/unrequired" 'REQUIRED-BUT-UNPRODUCED'

# 4b. LIVE -- the same question, asked of this repository.
#
#     The DECLARATION is the input: section 3 has just proven the live protection
#     matches it, so a context required here is required live. The probe still
#     reads its answer from the tree rather than being told one.
required_contexts="$(python3 - "$POLICY" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(2)
try:
    policy = yaml.safe_load(open(sys.argv[1])) or {}
except OSError:
    sys.exit(2)
protection = policy.get("protection") or {}
checks = protection.get("required_status_checks") or {}
contexts = list(checks.get("contexts") or [])
named = policy.get("required_status_contexts") or []
if isinstance(named, str):
    named = [named]
for c in named:
    if c not in contexts:
        contexts.append(c)
print(" ".join(str(c) for c in contexts))
PY
)"
if [ -n "$required_contexts" ]; then
  work_probe "$root"
  case "$p_rc" in
    0) echo "  OK  every REQUIRED context has a producer on the path that evaluates a pull request"
       printf '%s\n' "$p_out" | grep -E '^  OK' | sed 's/^/      /' ;;
    1) echo "check-branch-protection: NOT-OK — a REQUIRED status context has NO PRODUCER on the path that evaluates a pull request; a required-but-unproduced check is a bypass generator, not a control" >&2
       printf '%s\n' "$p_out" | sed 's/^/    /' >&2
       fail=1 ;;
    2) echo "  CANNOT-ASSESS  the PRODUCER of a REQUIRED context could NOT be assessed — NOT a pass, and this gate exits 2"
       printf '%s\n' "$p_out" | sed 's/^/      /'
       cannot_assess=1 ;;
    *) echo "check-branch-protection: FAIL — the producer probe returned an unexpected code $p_rc" >&2
       fail=1 ;;
  esac
fi

# Order matters: a real, provoked failure outranks an unobserved live state, so a
# gate that found a genuine defect still reports NOT-OK (1) rather than the
# softer 2 — but only once the precondition at the top of this file has
# already confirmed the provocations ran for real. When `gh` is unauthenticated
# this gate never reaches here at all (rc 2, by name, above); it does not fall
# through to this ordering.
if [ "$fail" -ne 0 ]; then
  echo "check-branch-protection: NOT-OK — the declaration, its enforcement, or the producer of the required context is defective"
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-branch-protection: CANNOT-ASSESS — declared and provoked, but the LIVE protection was NOT observed"
  exit 2
fi
echo "check-branch-protection: OK — declared, provoked, produced and read back"
exit 0
