#!/usr/bin/env bash
# check-gate-status.sh — prove the gate of record can become a REQUIRED check
# without GitHub Actions (ADR-0028, #803 P0-2).
#
# THE QUESTION THIS ANSWERS
# GitHub's *required status checks* are the only mechanism that makes a merge
# impossible without green evidence. Producing one needs a check-run or a commit
# status -- and GR-15 bans the Actions that would normally produce it. ADR-0028
# resolves that with a commit status posted by the code-native runner. This gate
# proves the mechanism is sound WITHOUT writing to the repository.
#
# WHY IT IS PROVOKED RATHER THAN ASSERTED
# The mapping rc -> status is the whole control. Three ways it can be a formality:
#
#   1. it always returns `success`            -> a red gate merges
#   2. CANNOT-ASSESS collapses into `success` -> a false green (the #739 class:
#      an unreadable HEAD that read back as `healthy`)
#   3. an unknown rc is silently defaulted    -> a control that cannot fail
#
# So the offline provocation asserts all three: every outcome maps, the three
# outcomes stay DISTINGUISHABLE, and an unknown outcome is REFUSED. It drives
# the same mapper (scripts/gate-status-map.py) the poster uses.
#
# The live half is a read-back, and an unobservable read-back is CANNOT-ASSESS
# (exit 2) -- never a pass.
#
# THE HALF THAT WAS MISSING (#1357, measured 2026-09-18)
# Everything above proves the poster's MACHINERY: the two names agree and the
# mapping is sound. None of it proves anything PRODUCES the status. So this gate
# was green on a repository whose required context had no producer at all: branch
# protection required `ao/gate-of-record`, no path that makes the gate-of-record
# verdict posted it, every PR head read BLOCKED, and merges went through only
# because `enforce_admins` is false -- an admin bypass. A required context whose
# producer does not exist is not a control; it is a bypass generator, and the
# gate that polices it must say so BY NAME instead of passing.
#
# So section 5 asks the producing question over the TREE alone (no network, so it
# is deterministic and sandbox-safe):
#
#   * the context is REQUIRED -- in the policy's
#     `protection.required_status_checks.contexts`, or named by ADR-0028's
#     `required_status_contexts` -- and
#   * no invocation of the poster exists on the path that MAKES the verdict
#     (`infra/cloudbuild/*.yaml`, the unattended runner that runs the gate of
#     record), or none exists anywhere at all.
#
# "Produced" is asserted at the two moments it can honestly be asserted: a
# producer path must EXIST and be reached from the verdict path (offline, and
# provable), and a status must be OBSERVED on the commit under test (the
# read-back). A REQUIRED context that is not observed is CANNOT-ASSESS naming it
# -- never the green note it used to be.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-status.sh
#        bash scripts/check-gate-status.sh --producer-probe [--root DIR]
#          answer ONLY the producing question over DIR and exit 0/1/2, so
#          scripts/check-branch-protection.sh drives THIS implementation rather
#          than a second copy of the rule.
set -u

# --- the probe seam, parsed BEFORE anything else -------------------------
# A provocation that re-implements the rule proves nothing about the rule that
# runs -- the lesson scripts/branch-protection-compare.py records for its
# comparator. So the producing question lives in ONE function here, section 5
# calls it for the repository, and every fixture in both gates calls the same
# function through `--producer-probe`.
probe_mode=0
root_override=""
while [ $# -gt 0 ]; do
  case "$1" in
    --producer-probe) probe_mode=1 ;;
    --root) root_override="${2:-}"; shift ;;
    -h|--help) sed -n '2,70p' "$0"; exit 0 ;;
    *) : ;;
  esac
  shift
done

# The poster's name and its posting verb, assembled from fragments so that this
# checker's own source cannot be its own finding -- and matched on a line whose
# COMMENT has been stripped, because a comment cannot run: a YAML note reading
# "post it with scripts/gate-status.sh" would otherwise certify a producer that
# does not exist, which is the direction that fails OPEN.
fx_name="gate-status"".sh"
fx_verb="po""st"
fx_name_re="${fx_name//./[.]}"
fx_verb_re="(^|[^A-Za-z0-9_])${fx_verb}([^A-Za-z0-9_]|\$)"
# ADR-0028's declared name, consulted only when the poster itself is absent and
# cannot be asked (a fixture, or a tree where the poster was deleted).
fx_fallback_ctx="ao/gate-of-record"
# The paths that MAKE the gate-of-record verdict unattended. This is the runner
# the repo's own headers name (ADR-0028: the code-native runner, no Actions) and
# the one place a producer can be reached for a commit nobody drives by hand.
fx_verdict_dir="infra/cloudbuild"

# A TEST or a DOC is not a producer. A test that calls the poster proves the
# poster CAN be called; it says nothing about whether anything calls it when a
# verdict is made. Counting one would certify the defect away, so this is the
# vacuity guard the fixtures provoke.
#
# Neither is a CHECKER, and neither is the poster itself. A `check-*.sh` that
# plants a fixture naming the poster would otherwise certify its own premise:
# measured while writing this, the policing gates listed THEMSELVES among the
# "producers elsewhere", which is both false and -- because it can never be empty
# again -- a branch of this rule that could no longer fire. `scripts/
# check-shell-patterns.sh` records the same trap for its own source, one level
# over; the fix there is to fragment the shape, and the fix here is to say plainly
# that a checker is not a producer. The poster is excluded for the same reason in
# the other direction: counting a producer as its own producer is circular.
is_producer_path() {
  case "$1" in
    */tests/*|*/test/*|*/docs/*|*/.board/*|*/vendor/*|*/.research/*|*/.git/*) return 1 ;;
  esac
  case "$1" in
    *.md) return 1 ;;
  esac
  case "${1##*/}" in
    test_*|*_test.py|*_test.sh) return 1 ;;
    check-*.sh|check-*.py|gate-status.sh) return 1 ;;
  esac
  return 0
}

# producer_in <file> -- 0 when a NON-COMMENT line invokes the poster.
producer_in() {
  awk -v name="$fx_name_re" -v verb="$fx_verb_re" '
    { line = $0; sub(/#.*/, "", line)
      if (line ~ name && line ~ verb) { found = 1; exit } }
    END { exit(found ? 0 : 1) }' "$1"
}

# verdict_paths <root> <verdict-dir> -- the build config(s) the repo ITSELF names as
# the path that evaluates a PULL REQUEST, one per line as "<path> <enabled|disabled>".
#
# This is the honest definition of "where the PR verdict is made", and it is
# DERIVED rather than invented: a required status check gates a pull request, so
# the path that must produce it is the one a `repositoryEventConfig.pullRequest`
# trigger names in its `filename:`. Two consequences that matter. A build config
# that runs `make verify` for another reason (a rollout promotion) is NOT a PR
# verdict path and is not asked for a producer -- a rule that demanded one would
# red on a healthy repository, and a gate that fires on its own over-broad match
# trains the operator to ignore it. And a trigger that ships flag-gated OFF is
# reported as such rather than silently accepted: a required check whose only
# path is OFF produces nothing, which is the #724 class this repo has already
# been bitten by once.
verdict_paths() {
  python3 - "$1" "$2" <<'PY'
import glob
import os
import sys

try:
    import yaml
except ImportError:
    sys.exit(2)

root, vdir_name = sys.argv[1], sys.argv[2]
vdir = os.path.join(root, vdir_name)
found = False
for trig in sorted(glob.glob(os.path.join(vdir, "*.yaml")) + glob.glob(os.path.join(vdir, "*.yml"))):
    try:
        doc = yaml.safe_load(open(trig)) or {}
    except OSError:
        continue
    except yaml.YAMLError:
        sys.exit(2)
    if not isinstance(doc, dict):
        continue
    event = doc.get("repositoryEventConfig") or {}
    if not event.get("pullRequest"):
        continue
    name = doc.get("filename") or ""
    if not name:
        sys.exit(2)
    found = True
    print("%s %s" % (name, "disabled" if doc.get("disabled") else "enabled"))
if not found:
    sys.exit(3)
PY
}

# reaches_producer <root> <build-config> -- 0 when that config, or a shell script
# its own steps name, invokes the poster. One hop on purpose: a producer two
# scripts deep is not provably reachable from the path that makes the verdict, and
# this gate refuses rather than guessing in the direction that passes.
reaches_producer() {
  rp="$1/$2"
  [ -f "$rp" ] || return 1
  producer_in "$rp" && return 0
  for tok in $(grep -oE '[A-Za-z0-9_./-]+[.]sh' "$rp" 2>/dev/null); do
    case "$tok" in
      /*) cand="$tok" ;;
      *) cand="$1/${tok#./}" ;;
    esac
    [ -f "$cand" ] || continue
    producer_in "$cand" && return 0
  done
  return 1
}

# producer_probe <root> -- the producing question, over one tree.
#   0 the REQUIRED context has a producer on every path that makes the verdict
#     (or nothing is REQUIRED, so no producer is owed)
#   1 REQUIRED-BUT-UNPRODUCED -- a producer path is absent, and the refusal names
#     the context and the missing producer
#   2 CANNOT-ASSESS -- the requirement or the verdict path could not be located
# The first line is machine-readable so a caller can read the answer without
# re-deriving it; the lines after it are the human refusal.
producer_probe() {
  r="${1:-}"
  if [ -z "$r" ] || [ ! -d "$r" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable\n'
    echo "check-gate-status: CANNOT-ASSESS — there is no tree to probe: '${r}'" >&2
    return 2
  fi
  pol="$r/governance/platform/branch-protection.yaml"
  if [ ! -f "$pol" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable\n'
    echo "check-gate-status: CANNOT-ASSESS — the declared policy is missing: $pol, so what is REQUIRED cannot be read" >&2
    return 2
  fi
  # Both declarations are read: `protection.required_status_checks` is what
  # `branch-protection.sh apply` PUTs to GitHub, and `required_status_contexts`
  # is ADR-0028's name for the same control. The UNION is the honest input --
  # requiring it in either place makes it required in fact.
  required_ctxs="$(python3 - "$pol" <<'PY'
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
)" || required_ctxs=""
  if [ -z "$required_ctxs" ] && ! grep -q 'required_status_checks' "$pol"; then
    printf 'probe: required=unknown context=unknown verdict=unreadable\n'
    echo "check-gate-status: CANNOT-ASSESS — $pol could not be parsed, so what is REQUIRED cannot be read (PyYAML missing or the file is unreadable)" >&2
    return 2
  fi

  # The context is taken from the POSTER itself, so the probe cannot be pointed
  # at a different name than the one that is posted; ADR-0028's declared name is
  # the fallback only when the poster is not there to be asked.
  pctx=""
  if [ -f "$r/scripts/$fx_name" ]; then
    pctx="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' "$r/scripts/$fx_name" 2>/dev/null | sed 's/.*:-//;s/}"//')"
  fi
  [ -n "$pctx" ] || pctx="$fx_fallback_ctx"

  required=0
  case " $required_ctxs " in
    *" $pctx "*) required=1 ;;
  esac

  # The producers that exist anywhere in the tree, and the paths the repo names
  # as the ones that make the PR verdict. Both are computed from the tree, so
  # neither can be told a story.
  sites="$(grep -rl --binary-files=without-match \
             --exclude-dir=.git --exclude-dir=vendor --exclude-dir=.research \
             --exclude-dir=.verify --exclude-dir=.board --exclude-dir=node_modules \
             -e "$fx_name_re" "$r" 2>/dev/null | while read -r f; do
               is_producer_path "$f" && producer_in "$f" && printf '%s\n' "${f#"$r"/}"
             done)"
  vlist="$(verdict_paths "$r" "$fx_verdict_dir")"
  vrc=$?

  if [ "$required" -eq 0 ]; then
    printf 'probe: required=0 context=%s verdict=not-required\n' "$pctx"
    echo "  OK  no status context is REQUIRED by governance/platform/branch-protection.yaml, so no producer is owed and no merge is deadlocked by its absence"
    return 0
  fi
  if [ "$vrc" -ne 0 ] || [ -z "$vlist" ]; then
    printf 'probe: required=1 context=%s verdict=unlocatable\n' "$pctx"
    echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-UNLOCATABLE — '$pctx' is REQUIRED, but no path that evaluates a pull request could be located (no trigger under $fx_verdict_dir declares repositoryEventConfig.pullRequest with a filename, or the declarations could not be parsed), so the path that MAKES the PR verdict cannot be identified and no producer can be proven on it; that is never a pass" >&2
    return 2
  fi
  if [ -z "$sites" ]; then
    printf 'probe: required=1 context=%s verdict=unproduced\n' "$pctx"
    echo "check-gate-status: FAIL REQUIRED-BUT-UNPRODUCED — the REQUIRED context '$pctx' has NO PRODUCER: no non-test, non-doc invocation of 'bash scripts/$fx_name $fx_verb' exists anywhere in this tree" >&2
    printf '    the PR verdict is made by: %s\n' "$(printf '%s' "$vlist" | cut -d' ' -f1 | tr '\n' ' ')" >&2
    return 1
  fi
  unproduced=""
  gated_off=""
  while read -r vp vstate; do
    [ -n "$vp" ] || continue
    if [ "$vstate" = "disabled" ]; then
      gated_off="$gated_off$vp
"
    fi
    reaches_producer "$r" "$vp" || unproduced="$unproduced$vp
"
  done <<< "$vlist"
  if [ -n "$unproduced" ]; then
    printf 'probe: required=1 context=%s verdict=unproduced\n' "$pctx"
    echo "check-gate-status: FAIL REQUIRED-BUT-UNPRODUCED — the context '$pctx' is REQUIRED by governance/platform/branch-protection.yaml, but nothing posts it on the path that evaluates a pull request:" >&2
    echo "    required context : $pctx" >&2
    echo "    missing producer : no invocation of 'bash scripts/$fx_name $fx_verb' in" >&2
    printf '      %s\n' $unproduced >&2
    echo "    producers elsewhere in this tree (NOT on that path, so they cannot satisfy the check for a PR head):" >&2
    printf '      %s\n' $sites >&2
    if [ -n "$gated_off" ]; then
      echo "    and the path is flag-gated OFF as well, so it would produce nothing even with a producer:" >&2
      printf '      %s\n' $gated_off >&2
    fi
    echo "    a required check that nothing produces where the PR verdict is made is not a control: every PR head stays BLOCKED and a merge can only proceed through the admin bypass (enforce_admins=false)" >&2
    return 1
  fi
  if [ -n "$gated_off" ]; then
    printf 'probe: required=1 context=%s verdict=gated-off\n' "$pctx"
    echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-GATED-OFF — '$pctx' is REQUIRED and its producer exists on the PR path, but that trigger ships flag-gated OFF, so nothing produces the check until it is promoted out of band; the requirement is not satisfiable yet, which is never a pass:" >&2
    printf '      %s\n' $gated_off >&2
    return 2
  fi
  printf 'probe: required=1 context=%s verdict=produced\n' "$pctx"
  echo "  OK  the REQUIRED context '$pctx' has a producer on the path that evaluates a pull request"
  return 0
}

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "$root_override" ]; then
  root="$(cd "$root_override" 2>/dev/null && pwd)" || {
    echo "check-gate-status: CANNOT-ASSESS — --root is not a readable directory: $root_override" >&2
    exit 2
  }
fi
if [ "$probe_mode" -eq 1 ]; then
  producer_probe "$root"
  exit $?
fi
cd "$root" || exit 2

MAPPER="scripts/gate-status-map.py"
POSTER="scripts/gate-status.sh"
POLICY="governance/platform/branch-protection.yaml"
fail=0
cannot_assess=0

command -v python3 >/dev/null 2>&1 || { echo "check-gate-status: CANNOT-ASSESS — python3 not found" >&2; exit 2; }

# 1. STRUCTURAL — both halves exist, and the context has ONE name.
[ -f "$MAPPER" ] || { echo "check-gate-status: FAIL — mapper missing: $MAPPER" >&2; exit 1; }
[ -f "$POSTER" ] || { echo "check-gate-status: FAIL — poster missing: $POSTER" >&2; exit 1; }

context_poster="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' "$POSTER" 2>/dev/null | sed 's/.*:-//;s/}"//')"
if [ -z "$context_poster" ]; then
  echo "check-gate-status: FAIL — the poster declares no context name" >&2
  fail=1
fi
# A SECOND, differing context name is how a required check becomes unsatisfiable:
# branch protection would require one name while the poster writes another, and
# nothing would ever satisfy it.
#
# The policy is PARSED, not grepped. A grep for `required_status_contexts:` finds
# the KEY, not the list under it, and comparing a key against a context name
# reports a false drift -- measured here: the first version of this check failed
# with "policy: required_status_contexts:" against "poster: ao/gate-of-record",
# which compares nothing to nothing. A control that fires on its own extraction
# bug trains the operator to ignore it.
declared_context="$(python3 - "$POLICY" <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    policy = yaml.safe_load(open(sys.argv[1])) or {}
except OSError:
    sys.exit(0)
contexts = policy.get("required_status_contexts") or []
if isinstance(contexts, str):
    contexts = [contexts]
print(" ".join(str(c) for c in contexts))
PY
)"
if [ -n "$declared_context" ] && [ -n "$context_poster" ]; then
  if ! printf '%s' "$declared_context" | grep -qw "$context_poster"; then
    echo "check-gate-status: FAIL — the policy declares a context the poster does not write" >&2
    echo "    policy declares: $declared_context" >&2
    echo "    poster writes:   $context_poster" >&2
    fail=1
  else
    echo "  OK  the policy and the poster name the SAME context: $context_poster"
  fi
fi

# 2. PROVOKED — the mapping is exhaustive, distinguishable, and refuses the unknown.
if ! python3 "$MAPPER" --self-test; then
  echo "check-gate-status: FAIL — the rc -> status mapping is defective (see above)" >&2
  fail=1
fi

# 2b. The three outcomes must map to three DIFFERENT states. Stated separately
#     from the self-test so that a regression names itself twice, not once.
declare -A WANT=( [0]=success [1]=failure [2]=error )
for rc in 0 1 2; do
  got="$(python3 "$MAPPER" "$rc" 2>/dev/null)"
  if [ "$got" != "${WANT[$rc]}" ]; then
    echo "check-gate-status: FAIL — rc $rc mapped to '$got', expected '${WANT[$rc]}'" >&2
    fail=1
  fi
done

# 2c. An unknown outcome is REFUSED (exit 2), never defaulted to a state.
python3 "$MAPPER" 3 >/dev/null 2>&1
rc=$?
if [ $rc -ne 2 ]; then
  echo "check-gate-status: FAIL — an unknown gate outcome was NOT refused (rc=$rc, expected 2)" >&2
  fail=1
else
  echo "  OK  an unknown gate outcome is REFUSED, not defaulted to a status"
fi

# 2d. CANNOT-ASSESS is published as `error`. Asserted by name: this is the exact
#     collapse that would turn an unassessable gate into a green merge.
ca="$(python3 "$MAPPER" 2 2>/dev/null)"
if [ "$ca" = "success" ]; then
  echo "check-gate-status: FAIL — CANNOT-ASSESS maps to 'success': a false green" >&2
  fail=1
else
  echo "  OK  CANNOT-ASSESS is published as '$ca' (never a pass)"
fi

# 3. DRY-RUN — the poster builds the right request for each outcome, WITHOUT
#    writing to the repository. This is what makes the gate safe to run inside
#    `make verify`: proving the poster must not post on every gate run.
if [ -f "$POSTER" ]; then
  for rc in 0 1 2; do
    out="$(bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc "$rc" 2>&1)"
    if ! printf '%s' "$out" | grep -q "state       = ${WANT[$rc]}"; then
      echo "check-gate-status: FAIL — dry-run for rc $rc did not build state=${WANT[$rc]}" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      fail=1
    fi
  done
  echo "  OK  the poster builds the correct request for all three outcomes (dry-run, nothing written)"

  # 3b. The poster must REFUSE an unknown outcome too, not just the mapper.
  bash "$POSTER" dry-run --sha 0000000000000000000000000000000000000000 --rc 7 >/dev/null 2>&1
  rc=$?
  if [ $rc -ne 2 ]; then
    echo "check-gate-status: FAIL — the poster did not refuse an unknown rc (rc=$rc, expected 2)" >&2
    fail=1
  fi
fi

# 5. PRODUCER — the REQUIRED context must have a PRODUCER (#1357).
#
#    Everything above proves the poster's machinery; this proves the poster is
#    reached when a verdict is made. Measured 2026-09-18 while this section was
#    written: live protection required `ao/gate-of-record`, no path that makes
#    the verdict posted it, and this gate was GREEN -- the inertness was printed
#    as a note ("expected until the poster runs in the runner") and then
#    discarded. The question is asked over the TREE only, so it needs no network
#    and cannot be excused by one; the read-back below is the half that needs the
#    API, and a REQUIRED context that is not observed there is CANNOT-ASSESS.
#
#    Both refusals are provoked offline, in scripts/check-branch-protection.sh --
#    which drives THIS same function through `--producer-probe` -- so each half
#    of the rule is shown to be able to fail, and the negative control (nothing
#    REQUIRED) is shown NOT to fire.
producer_now=0
producer_out="$(producer_probe "$root" 2>&1)"
prc=$?
case "$producer_out" in
  *"required=1"*) producer_now=1 ;;
esac
case "$prc" in
  0) printf '%s\n' "$producer_out" ;;
  1) printf '%s\n' "$producer_out" >&2; fail=1 ;;
  2) printf '%s\n' "$producer_out" >&2; cannot_assess=1 ;;
  *) printf '%s\n' "$producer_out" >&2
     echo "check-gate-status: FAIL — the producer probe returned an unexpected code $prc" >&2
     fail=1 ;;
esac

# 6. LIVE read-back — is this commit actually carrying the status? Offline is
#    CANNOT-ASSESS (2), never a pass.
#
#    Read back the commit the ADR's mechanism was proven on. Absence is NOT a
#    failure here: a commit that predates the poster is legitimately ungated, and
#    the context is not yet required (that is a separate, deliberate step --
#    requiring a context nothing posts would deadlock every merge, which is
#    exactly the #724 defect: an inert control with a gate that could not see it).
live_rc=0
if ! command -v gh >/dev/null 2>&1; then
  echo "  CANNOT-ASSESS gh-unauthenticated: gh is not installed — install gh and run 'gh auth login'"
  live_rc=2
elif ! gh auth status >/dev/null 2>&1; then
  echo "  CANNOT-ASSESS gh-unauthenticated: gh is installed but not authenticated — run 'gh auth login'"
  live_rc=2
elif bash "$POSTER" show --sha "$(git rev-parse HEAD)" >/tmp/cgs-live.log 2>&1; then
  echo "  OK  the LIVE read-back found $context_poster on this commit"
else
  rc=$?
  case "$rc" in
    1) # Absence is only "expected" when nothing REQUIRES the context. Once it is
       # required, an unobserved status means this commit cannot satisfy the
       # control at all, and reporting that as a pass is precisely the formality
       # #1357 is about -- so it is CANNOT-ASSESS, naming the context.
       if [ "$producer_now" -eq 1 ]; then
         echo "  CANNOT-ASSESS REQUIRED-BUT-UNOBSERVED — branch protection REQUIRES '$context_poster' but no status is present on $(git rev-parse --short HEAD), so this commit cannot satisfy it; the required check has no observed producer here, and a merge proceeds only through the admin bypass (enforce_admins=false)"
         live_rc=2
       else
         echo "  note  no $context_poster status on HEAD — the context is not REQUIRED, so no merge is deadlocked by its absence"
       fi ;;
    2) echo "  CANNOT-ASSESS  the status could NOT be read back (API unreachable) — not a pass"
       live_rc=2 ;;
    *) echo "  note  read-back returned $rc" ;;
  esac
fi

if [ "$fail" -ne 0 ]; then
  echo "check-gate-status: NOT-OK — a REQUIRED context has no producer, or the mapping or the poster is defective" >&2
  exit 1
fi
if [ "$cannot_assess" -ne 0 ]; then
  echo "check-gate-status: CANNOT-ASSESS — the mapping and the poster are sound, but the producing path or the LIVE status was NOT observed (see above)" >&2
  exit 2
fi
if [ "$live_rc" -eq 2 ]; then
  echo "check-gate-status: CANNOT-ASSESS — mapping and poster are sound, but the LIVE status was NOT observed"
  exit 2
fi
echo "check-gate-status: OK — the mapping is exhaustive and provoked, the REQUIRED context has a producer where the verdict is made, and the poster builds without writing"
exit 0
