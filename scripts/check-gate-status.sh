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
# So section 5 asks the producing question in TWO halves, and keeps them apart
# because they are answerable from different places (#1394):
#
#   * THE TREE -- does the context have a producer that is REACHED from the path
#     that MAKES the verdict (`infra/cloudbuild/*.yaml`)? Offline, deterministic,
#     and provable. No producer anywhere, or none on that path, is rc 1 BY NAME.
#   * THE LIVE STATE -- is that producer actually PRODUCING? The tree cannot
#     answer this even though it looks like it can: the only field it carries is
#     `infra/cloudbuild/verify-trigger.yaml`'s `disabled:`, and this repository
#     PINS that `true` by policy (scripts/check-cloudbuild.sh requires
#     `disabled: true` on the import stub, GR-5), so a probe that reads it as
#     live state names a condition the tree can never falsify. Measured
#     2026-09-19: the LIVE trigger is ENABLED and the context IS posted, while
#     that same probe answered `CANNOT-ASSESS REQUIRED-BUT-GATED-OFF` -- rc 2,
#     forever, on a healthy repository. A gate that reports a named condition
#     from a stale source of truth is not a control, so the declared value is now
#     an OBSERVATION and the verdict is read from `gh` -- the only half that
#     reality can contradict.
#
# The live half therefore answers one of three things, and never blurs them:
#
#   * the context IS being produced (observed on the commit under test or on a
#     recent commit) -> the producer exists AND is live -> rc 0;
#   * the source WAS READ and nothing is producing -> rc 1 BY NAME (a finding
#     about the repository, not an inability to assess);
#   * the source could NOT be read -> rc 2 CANNOT-ASSESS naming the source --
#     never a green, and never a false named verdict.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# A CHECK THAT CANNOT NAME ITS OWN REFUSAL (#1407)
# The description the required context carries was a FIXED string per rc, so every
# red PR page read `make verify: FAIL` and the failing check's name existed only
# inside the build log. Section 3c provokes the opposite: `post --detail <text>`
# must put the name on the PR page, and must NOT be able to put a PASS there -- a
# detail that can manufacture a green is the #739 class this repository measures
# rather than assumes.
#
# Usage: bash scripts/check-gate-status.sh
#        bash scripts/check-gate-status.sh --producer-probe [--root DIR]
#          answer ONLY the producing question over DIR and exit 0/1/2, so
#          scripts/check-branch-protection.sh drives THIS implementation rather
#          than a second copy of the rule. The live question is asked only when
#          DIR IS this repository's own tree -- a fixture is not the repository
#          the live state describes -- and a foreign tree gets the declared
#          verdict, which is what the four #1357 fixtures assert.
#        bash scripts/check-gate-status.sh --producer-probe --root DIR \
#             --live-state enabled|disabled|unreadable [--live-source S]
#          the provocation seat: stand a live answer in for a fixture. REFUSED
#          on this repository's own tree, so the gate can never be told that its
#          own producer is enabled.
set -u

# --- the probe seam, parsed BEFORE anything else -------------------------
# A provocation that re-implements the rule proves nothing about the rule that
# runs -- the lesson scripts/branch-protection-compare.py records for its
# comparator. So the producing question lives in ONE function here, section 5
# calls it for the repository, and every fixture in both gates calls the same
# function through `--producer-probe`.
probe_mode=0
root_override=""
live_state_override=""
live_source_override=""
while [ $# -gt 0 ]; do
  case "$1" in
    --producer-probe) probe_mode=1 ;;
    --root) root_override="${2:-}"; shift ;;
    # The PROVOCATION input for the live half (#1394). The live producer state
    # is the one input reality can contradict, so it cannot be asserted -- it is
    # READ, from `gh`, inside live_producer_state(). A fixture is not the
    # repository the live state describes, so its arms must be able to stand a
    # live answer in; this is that seat, and it is refused on this repository's
    # OWN tree (see the probe block), so no run of this gate can be told that
    # the producer is enabled.
    --live-state) live_state_override="${2:-}"; shift ;;
    --live-source) live_source_override="${2:-}"; shift ;;
    -h|--help) sed -n '2,87p' "$0"; exit 0 ;;
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

# live_producer_state <root> <context> -- the ONE input reality can contradict:
# is the producer actually producing? Printed as "<state> <source>" on one line.
#
#   enabled    the required context was OBSERVED -- on the commit under test, or
#              on one of the most recent default-branch commits
#   disabled   the source WAS readable and no such observation exists: the
#              producer is genuinely not producing
#   unreadable the source could not be read at all; a caller must never turn this
#              into either verdict
#
# WHY THIS EXISTS, measured 2026-09-19 (#1394): the LIVE trigger is ENABLED and
# the context IS posted -- `gcloud builds triggers describe control-plane-verify`
# answers `control-plane-verify  infra/cloudbuild/verify.yaml` (an empty
# `disabled` field, i.e. enabled), and `ao/gate-of-record` is observed on
# 2adbe48b (twice), 83ff32db and 0dbb0e68 -- while the tree's own
# `infra/cloudbuild/verify-trigger.yaml` declares `disabled: true`, pinned there
# BY THIS REPOSITORY'S POLICY: scripts/check-cloudbuild.sh calls
# `check_trigger "$cb_dir/verify-trigger.yaml" _ENABLE_VERIFY`, which requires
# `disabled is not True -> "must ship disabled: true (GR-5)"`. So the tree
# question "is this path gated off?" can never be falsified from the tree, and a
# probe that reads it as LIVE state names a condition the world contradicts --
# rc 2 forever, on a repository that is healthy. The declared value stays an
# OBSERVATION; the verdict is taken here, from the thing that can be wrong.
live_producer_state() {
  lroot="$1"
  lctx="$2"
  if ! command -v gh >/dev/null 2>&1; then
    printf 'unreadable gh-not-installed\n'
    return 0
  fi
  if ! gh auth status >/dev/null 2>&1; then
    printf 'unreadable gh-unauthenticated\n'
    return 0
  fi
  lslug="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)"
  if [ -z "$lslug" ]; then
    printf 'unreadable gh-repo-unresolvable\n'
    return 0
  fi
  lwindow="${AO_GATE_STATUS_WINDOW:-20}"
  case "$lwindow" in ''|*[!0-9]*) lwindow=20 ;; esac
  [ "$lwindow" -gt 50 ] && lwindow=50
  # The commit under test is probed FIRST -- a head that carries the status is
  # found in one request, and the requirement is per-PR-head once protection is
  # applied (governance/platform/branch-protection.yaml).
  lhead="$(git -C "$lroot" rev-parse HEAD 2>/dev/null)"
  lrecent="$(gh api "repos/$lslug/commits?per_page=$lwindow" --jq '.[].sha' 2>/dev/null)"
  lread=0
  lfound=0
  for lc in $lhead $lrecent; do
    [ -n "$lc" ] || continue
    if ! lctxs="$(gh api "repos/$lslug/commits/$lc/statuses" \
                   --jq '[.[].context]|join(",")' 2>/dev/null)"; then
      continue
    fi
    lread=$((lread + 1))
    case ",$lctxs," in
      *",$lctx,"*) lfound=1; break ;;
    esac
  done
  if [ "$lfound" -eq 1 ]; then
    printf 'enabled gh-status-observed\n'
  elif [ "$lread" -gt 0 ]; then
    printf 'disabled gh-status-absent-in-last-%s-commits\n' "$lwindow"
  else
    printf 'unreadable gh-statuses-unreadable\n'
  fi
  return 0
}

# probe_line <required> <verdict> -- the machine-readable first line. `live=` is
# added by #1394 so the caller can act on the live state without re-reading it
# (and without the two halves of the rule being able to disagree).
probe_line() {
  printf 'probe: required=%s context=%s verdict=%s live=%s live_source=%s\n' \
    "$1" "$pctx" "$2" "${live_state:--}" "${live_source:--}"
}

# producer_probe <root> [live-mode] -- the producing question, over one tree.
#   0 the REQUIRED context has a producer on every path that makes the verdict
#     (or nothing is REQUIRED, so no producer is owed)
#   1 REQUIRED-BUT-UNPRODUCED -- a producer path is absent, and the refusal names
#     the context and the missing producer
#   2 CANNOT-ASSESS -- the requirement or the verdict path could not be located,
#     the live producer state could not be read, or (live) nothing is producing
#
# `live-mode` is one of:
#   ask       read the live state from live_producer_state() -- the real answer
#   skip      ask nothing: the DECLARED state stands as the verdict. This is what
#             a foreign tree gets: a fixture is not the repository the live state
#             describes, so there is no live fact about it to read (#1394)
#   enabled|disabled|unreadable
#             the provocation seat -- a fixture's live answer, stood in
producer_probe() {
  r="${1:-}"
  lmode="${2:-skip}"
  if [ -z "$r" ] || [ ! -d "$r" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
    echo "check-gate-status: CANNOT-ASSESS — there is no tree to probe: '${r}'" >&2
    return 2
  fi
  pol="$r/governance/platform/branch-protection.yaml"
  if [ ! -f "$pol" ]; then
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
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
    printf 'probe: required=unknown context=unknown verdict=unreadable live=-\n'
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

  # THE LIVE HALF (#1394). Resolved here, once, from a source that is not the
  # tree, because the tree cannot answer this question and only pretends to.
  live_state=""
  live_source=""
  case "$lmode" in
    ask)
      lres="$(live_producer_state "$r" "$pctx")"
      live_state="${lres%% *}"
      live_source="${lres#* }" ;;
    skip) : ;;
    enabled|disabled|unreadable)
      live_state="$lmode"
      live_source="${3:-provocation-seam}" ;;
    *)
      probe_line unknown unreadable
      echo "check-gate-status: CANNOT-ASSESS — unknown live mode '$lmode'" >&2
      return 2 ;;
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
    probe_line 0 not-required
    echo "  OK  no status context is REQUIRED by governance/platform/branch-protection.yaml, so no producer is owed and no merge is deadlocked by its absence"
    return 0
  fi
  if [ "$vrc" -ne 0 ] || [ -z "$vlist" ]; then
    probe_line 1 unlocatable
    echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-UNLOCATABLE — '$pctx' is REQUIRED, but no path that evaluates a pull request could be located (no trigger under $fx_verdict_dir declares repositoryEventConfig.pullRequest with a filename, or the declarations could not be parsed), so the path that MAKES the PR verdict cannot be identified and no producer can be proven on it; that is never a pass" >&2
    return 2
  fi
  if [ -z "$sites" ]; then
    probe_line 1 unproduced
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
    probe_line 1 unproduced
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
    # The PR verdict path ships DECLARED flag-gated OFF. Whether that is the LIVE
    # truth is a different question, and only the live half can answer it: the
    # declaration is pinned by this repository's own policy (check-cloudbuild.sh
    # requires `disabled: true` on the import stub, GR-5), so it can never be
    # falsified from the tree, and reading it as live state is the #1394 defect.
    case "$live_state" in
      enabled)
        probe_line 1 produced
        echo "  OK  the REQUIRED context '$pctx' has a producer on the path that evaluates a pull request, and the LIVE producer state is '$live_source' — the context IS being produced"
        printf '      OBSERVATION, not a verdict: %s declares the PR verdict path flag-gated OFF.\n' "$gated_off"
        echo "      That declaration is pinned true by this repository's own policy (scripts/check-cloudbuild.sh, GR-5) and is therefore unfalsifiable from the tree; the verdict is taken from the live read-back instead (#1394)."
        return 0 ;;
      disabled)
        # The source WAS readable and it says nothing is producing. That is not an
        # inability to assess -- it is a FINDING, so it is rc 1 like every other
        # unproduced-context refusal, not rc 2. (rc 2 here would also be the wrong
        # shape for the skip budget: a skip means "this venue cannot answer", and
        # this venue just did.)
        probe_line 1 unobserved
        echo "check-gate-status: FAIL REQUIRED-BUT-UNOBSERVED — '$pctx' is REQUIRED and a producer exists on the PR verdict path, and the live producer state WAS read ('$live_source'), but the context is not observed on the commit under test nor on any of the most recent commits: the producer is genuinely not producing, so the requirement is not satisfiable, which is never a pass:" >&2
        printf '      declared flag-gated OFF as well: %s\n' $gated_off >&2
        return 1 ;;
      unreadable)
        probe_line 1 live-unreadable
        echo "check-gate-status: CANNOT-ASSESS LIVE-PRODUCER-UNREADABLE — '$pctx' is REQUIRED and a producer exists on the PR verdict path, but the live producer state could NOT be read from its source ('$live_source'): neither 'it is producing' nor 'it is gated off' can be claimed, because the source — not the tree — is what decides this, and an unreadable source is never a pass" >&2
        printf '      the declaration this leaves unresolved: %s\n' $gated_off >&2
        return 2 ;;
      *)
        probe_line 1 gated-off
        echo "check-gate-status: CANNOT-ASSESS REQUIRED-BUT-GATED-OFF — '$pctx' is REQUIRED and its producer exists on the PR path, but that trigger ships flag-gated OFF, so nothing produces the check until it is promoted out of band; the requirement is not satisfiable yet, which is never a pass:" >&2
        printf '      %s\n' $gated_off >&2
        echo "      (no live question was asked here — the tree under probe is not the repository the live state describes, so the declaration stands as the only available observation)" >&2
        return 2 ;;
    esac
  fi
  probe_line 1 produced
  echo "  OK  the REQUIRED context '$pctx' has a producer on the path that evaluates a pull request"
  return 0
}

own_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root="$own_root"
if [ -n "$root_override" ]; then
  root="$(cd "$root_override" 2>/dev/null && pwd)" || {
    echo "check-gate-status: CANNOT-ASSESS — --root is not a readable directory: $root_override" >&2
    exit 2
  }
fi
if [ "$probe_mode" -eq 1 ]; then
  # THE SAME-TREE RULE (#1394). The live producer state is a fact about THIS
  # repository, so the live question is asked only when the tree under probe IS
  # this repository's tree. A fixture is not: there is no trigger of its own and
  # no live poster, so asking about it would be a category error -- and it would
  # have silently INVERTED the four provocation arms in
  # scripts/check-branch-protection.sh, whose `gatedoff` fixture must keep
  # answering the declared-state verdict. So a foreign tree gets `skip`, and the
  # provocation seat (`--live-state`) supplies a live answer explicitly.
  if [ "$root" = "$own_root" ]; then
    if [ -n "$live_state_override" ]; then
      echo "check-gate-status: CANNOT-ASSESS — --live-state is REFUSED on this repository's OWN tree: the live producer state is READ here, never asserted (#1394)" >&2
      exit 2
    fi
    producer_probe "$root" ask
  else
    case "$live_state_override" in
      '') producer_probe "$root" skip ;;
      enabled|disabled|unreadable)
        producer_probe "$root" "$live_state_override" "$live_source_override" ;;
      *) echo "check-gate-status: CANNOT-ASSESS — --live-state must be enabled, disabled or unreadable (got '$live_state_override')" >&2
         exit 2 ;;
    esac
  fi
  exit $?
fi
if [ -n "$live_state_override" ]; then
  echo "check-gate-status: CANNOT-ASSESS — --live-state requires --producer-probe: the live state is read on this repository's own tree, never asserted" >&2
  exit 2
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

# 3c. DETAIL — the description must be able to say WHY the gate redded (#1407).
#
#    The description was a FIXED string per rc, so every red PR page read
#    `make verify: FAIL` and WHICH check failed was knowable only by opening the
#    build log: a required check that cannot name its own refusal. The poster now
#    takes `--detail <text>`, and the seam has TWO halves that must both hold:
#
#      * the detail REACHES the description -- and reaches the POSTED payload,
#        not only the dry-run text, because a seam that stops one step short is a
#        description the operator still never sees;
#      * the detail does NOT reach the OUTCOME. A detail able to turn a
#        CANNOT-ASSESS (rc 2) into a pass is exactly the #739 false-green class.
#
#    Every arm is offline. The posting arm shadows `gh` with a stub that records
#    its argv, so the POST path itself is measured without making a request.
if [ -f "$POSTER" ]; then
  det_sha=0000000000000000000000000000000000000000
  desc_of() { printf '%s\n' "$1" | sed -n 's/^  description = //p' | head -1; }
  state_of() { printf '%s\n' "$1" | sed -n 's/^  state       = //p' | head -1; }
  # The arm form below states the EXPECTATION beside the ACTUAL value, because an
  # arm that only reports "ok=NO" cannot be audited.
  darm() { # darm <label> <expect> <actual>
    _dlbl="$1"; _dexp="$2"; _dact="$3"
    if [ "$_dexp" = "$_dact" ]; then
      echo "  OK    $_dlbl"
      echo "        expect: $_dexp"
      echo "        actual: $_dact"
    else
      echo "check-gate-status: FAIL — the detail arm '$_dlbl' did not hold" >&2
      echo "        expect: $_dexp" >&2
      echo "        actual: $_dact" >&2
      fail=1
    fi
  }

  # (a) PARITY. With NO detail the description is byte-identical to today's. The
  #     three strings are pinned HERE as literals, so a drift in the mapper reds
  #     this gate instead of quietly matching itself in two places at once.
  declare -A TODAY=( [0]="make verify: PASS" [1]="make verify: FAIL" [2]="make verify: CANNOT-ASSESS (not a pass)" )
  for rc in 0 1 2; do
    det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc "$rc" 2>&1)"
    darm "rc $rc with no --detail keeps today's description byte-for-byte" \
      "${TODAY[$rc]}" "$(desc_of "$det_out")"
  done

  # (b) The detail REACHES the description, in front of an intact outcome string.
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 1 --detail 'check-reconcile' 2>&1)"
  darm "post --rc 1 --detail 'check-reconcile' renders a description naming it" \
    "make verify: FAIL -- check-reconcile" "$(desc_of "$det_out")"
  darm "and the outcome is still the rc's own (the detail is a suffix)" \
    "failure" "$(state_of "$det_out")"
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 1 --detail "$(printf 'check-a\n  check-b')" 2>&1)"
  darm "a detail carrying newlines is flattened to ONE description line" \
    "make verify: FAIL -- check-a check-b" "$(desc_of "$det_out")"

  # (c) THE #739 ARM: a detail can not turn rc 2 into a pass. A status page reads
  #     the STATE as well as the description, so both are asserted.
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 2 --detail 'success PASS green' 2>&1)"
  darm "a passing-sounding detail on rc 2 is STILL published as an error" \
    "error" "$(state_of "$det_out")"
  case "$det_out" in
    *"state       = success"*)
      echo "check-gate-status: FAIL — a detail turned CANNOT-ASSESS into a pass (the #739 class)" >&2
      fail=1 ;;
    *)
      echo "  OK    no state built for rc 2 is a pass, whatever the detail says" ;;
  esac
  case "$(desc_of "$det_out")" in
    "make verify: CANNOT-ASSESS (not a pass)"*)
      echo "  OK    and the description still leads with the CANNOT-ASSESS outcome" ;;
    *)
      echo "check-gate-status: FAIL — a detail displaced the CANNOT-ASSESS outcome string" >&2
      printf '        actual: %s\n' "$(desc_of "$det_out")" >&2
      fail=1 ;;
  esac
  # A detail is not a SOURCE of the outcome: offered with no --rc it must still be
  # CANNOT-ASSESS, never an outcome guessed from the text of the detail.
  bash "$POSTER" dry-run --sha "$det_sha" --detail 'check-reconcile' >/dev/null 2>&1
  darm "a --detail with no --rc is still CANNOT-ASSESS (a detail is not an outcome)" \
    2 "$?"

  # (d) BOUNDED. GitHub cuts a status description at 140 characters, so the poster
  #     must cut it deliberately: keep the outcome string, mark the cut. The API's
  #     own limit is pinned here as a literal -- it is an external fact, not a
  #     number this repository gets to choose.
  cut_mark="$(python3 -c 'print("\u2026", end="")')"
  det_out="$(bash "$POSTER" dry-run --sha "$det_sha" --rc 2 --detail "$(printf 'c%.0s' $(seq 1 500))" 2>&1)"
  det_desc="$(desc_of "$det_out")"
  darm "a 500-character detail still fits the API's 140-character description cap" \
    "<=140" "$([ "${#det_desc}" -le 140 ] && echo '<=140' || echo ">140 (${#det_desc})")"
  case "$det_desc" in
    "make verify: CANNOT-ASSESS (not a pass)"*"$cut_mark")
      echo "  OK    and the truncation is MARKED, and the outcome string is what survived it" ;;
    *)
      echo "check-gate-status: FAIL — a truncated description neither kept its outcome string nor said it was cut" >&2
      printf '        actual: %s\n' "$det_desc" >&2
      fail=1 ;;
  esac

  # (e) The POST path itself: the detail must reach the PAYLOAD, not just the
  #     dry-run text. `--rc 1` is deliberate -- the venue guard gates `success`
  #     only, so a red reaches publish_status with no live read at all.
  det_bin="$(mktemp -d /tmp/cgs-detail.XXXXXX)" || det_bin=""
  if [ -z "$det_bin" ]; then
    echo "check-gate-status: CANNOT-ASSESS — mktemp failed, so the posting arm could not be provoked" >&2
    exit 2
  fi
  mkdir -p "$det_bin/bin"
  cat > "$det_bin/bin/gh" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for `gh`: records its argv and answers success. The boundary
# stubbed is the API, never the poster -- its parsing and decision all run.
printf '%s\n' "$*" >> "${STUB_ARGV:?}"
exit 0
STUB
  chmod +x "$det_bin/bin/gh"
  : > "$det_bin/argv.txt"
  STUB_ARGV="$det_bin/argv.txt" PATH="$det_bin/bin:$PATH" \
    bash "$POSTER" post --sha "$det_sha" --rc 1 --detail 'check-shell-patterns' \
    >"$det_bin/out.txt" 2>&1
  det_rc=$?
  darm "post --rc 1 --detail ... succeeds through the shadowed gh (no request made)" \
    0 "$det_rc"
  case "$(cat "$det_bin/argv.txt")" in
    *"description=make verify: FAIL -- check-shell-patterns"*)
      echo "  OK    and the detail reaches the POSTED payload: $(tr '\n' ' ' < "$det_bin/argv.txt")" ;;
    *)
      echo "check-gate-status: FAIL — the detail did not reach the posted payload" >&2
      printf '        argv: %s\n' "$(tr '\n' ' ' < "$det_bin/argv.txt")" >&2
      printf '        out:  %s\n' "$(tr '\n' ' ' < "$det_bin/out.txt")" >&2
      fail=1 ;;
  esac
  rm -rf "$det_bin"

  # (f) A verb that does not publish an outcome from --rc must REFUSE the detail
  #     rather than accept and drop it: a flag that is silently ignored leaves the
  #     producer believing it reached the PR page.
  bash "$POSTER" reconcile --sha "$det_sha" --detail 'check-reconcile' >/dev/null 2>&1
  darm "--detail on 'reconcile' is REFUSED, never silently dropped" 2 "$?"
fi

# 4. LIVE PRODUCER STATE — provoked (#1394).
#
#    The defect this provokes: the probe read the checked-in import stub's
#    `disabled:` as LIVE state, so it answered REQUIRED-BUT-GATED-OFF (rc 2) on a
#    repository whose trigger is ENABLED and whose context IS posted -- naming a
#    condition the world contradicts, forever, with no way for the tree to
#    falsify it (scripts/check-cloudbuild.sh PINS that field `true` by policy).
#
#    Four arms, and every one of them is stated as a PAIR -- the expectation
#    beside the line that was actually produced -- because an arm that only says
#    "ok=NO" cannot be audited. The negative control is the last one: the
#    provocation seat must be REFUSED on this repository's own tree, or the gate
#    could be told that the producer is enabled.
mk_live_fixture() { # mk_live_fixture <dir> <disabled: true|false>
  mkdir -p "$1/infra/cloudbuild" "$1/governance/platform" "$1/scripts"
  cat > "$1/governance/platform/branch-protection.yaml" <<'YAML'
required_status_contexts:
  - ao/gate-of-record
YAML
  printf 'repositoryEventConfig:\n  pullRequest:\n    branch: ^master$\nfilename: infra/cloudbuild/verify.yaml\ndisabled: %s\n' \
    "$2" > "$1/infra/cloudbuild/verify-trigger.yaml"
  cat > "$1/infra/cloudbuild/verify.yaml" <<'YAML'
steps:
  - name: verdict
    args: ["bash scripts/gate-status.sh post --sha 1 --rc 0"]
YAML
}

arm() { # arm <label> <want-rc> <want-needle> <forbid-needle> <out> <rc>
  _lbl="$1"; _wantrc="$2"; _need="$3"; _forbid="$4"; _out="$5"; _rc="$6"
  _ok=1
  [ "$_rc" = "$_wantrc" ] || _ok=0
  case "$_out" in *"$_need"*) : ;; *) _ok=0 ;; esac
  case "$_forbid" in
    '') : ;;
    *) case "$_out" in *"$_forbid"*) _ok=0 ;; esac ;;
  esac
  if [ "$_ok" -eq 1 ]; then
    echo "  OK    $_lbl"
    echo "        expect: rc=$_wantrc and '$_need'${_forbid:+ and NOT '$_forbid'}"
    echo "        actual: $(printf '%s' "$_out" | grep -E '^probe:' | head -1)  rc=$_rc"
  else
    echo "check-gate-status: FAIL — provoked arm '$_lbl' did not hold" >&2
    echo "        expect: rc=$_wantrc and '$_need'${_forbid:+ and NOT '$_forbid'}" >&2
    echo "        actual: $(printf '%s' "$_out" | grep -E '^probe:' | head -1)  rc=$_rc" >&2
    printf '%s\n' "$_out" | sed 's/^/        /' >&2
    fail=1
  fi
}

live_fx="$(mktemp -d "${TMPDIR:-/tmp}/cgs-live.XXXXXX")" || live_fx=""
if [ -z "$live_fx" ]; then
  echo "check-gate-status: CANNOT-ASSESS — mktemp failed, so the live arms could not be provoked" >&2
  exit 2
fi
trap 'rm -rf "$live_fx"' EXIT
mk_live_fixture "$live_fx/off" true

# 4a. THE DEFECT: a DECLARED-off path whose live producer IS enabled and posting
#     must not carry the gated-off verdict, and must assess.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state enabled --live-source fixture-status-observed 2>&1)"; rc=$?
arm "a DECLARED-off PR path with a LIVE, enabled, posting producer is PRODUCED" \
    0 'verdict=produced' 'REQUIRED-BUT-GATED-OFF' "$out" "$rc"

# 4b. The other way: with the source READABLE and nothing producing, it must still
#     refuse, by name, and must NOT go green -- and it is a FINDING (rc 1), not an
#     inability to assess (rc 2), because this venue just answered the question.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state disabled --live-source fixture-none-observed 2>&1)"; rc=$?
arm "a producer that is genuinely not producing is REFUSED by name" \
    1 'REQUIRED-BUT-UNOBSERVED' 'verdict=produced' "$out" "$rc"

# 4c. An UNREADABLE source is CANNOT-ASSESS naming the source — never a green, and
#     never the false named verdict from the stale declaration.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" \
        --live-state unreadable --live-source fixture-gh-not-installed 2>&1)"; rc=$?
arm "an UNREADABLE live source is CANNOT-ASSESS naming the source" \
    2 'LIVE-PRODUCER-UNREADABLE' 'verdict=produced' "$out" "$rc"
case "$out" in
  *fixture-gh-not-installed*) echo "  OK    and the refusal NAMES the source it could not read" ;;
  *) echo "check-gate-status: FAIL — the unreadable refusal did not name its source" >&2; fail=1 ;;
esac

# 4d. NEGATIVE CONTROL — the seat must be REFUSED on this repository's own tree.
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$root" \
        --live-state enabled 2>&1)"; rc=$?
arm "telling this repository that its producer is enabled is REFUSED" \
    2 'REFUSED on this repository' 'verdict=produced' "$out" "$rc"

# 4e. No live question on a FOREIGN tree: the declared state stands (this is the
#     shape scripts/check-branch-protection.sh's fixtures depend on).
out="$(bash scripts/check-gate-status.sh --producer-probe --root "$live_fx/off" 2>&1)"; rc=$?
arm "a foreign tree gets the DECLARED verdict, not a live one" \
    2 'REQUIRED-BUT-GATED-OFF' '' "$out" "$rc"

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
producer_out="$(producer_probe "$root" ask 2>&1)"
prc=$?
case "$producer_out" in
  *"required=1"*) producer_now=1 ;;
esac
probe_live="-"
producer_live_source="-"
# shellcheck disable=SC2034
live_field="$(printf '%s' "$producer_out" | grep -oE 'live=[^ ]+' | head -1)"
live_src_field="$(printf '%s' "$producer_out" | grep -oE 'live_source=[^ ]+' | head -1)"
live_field="${live_field#live=}"
live_src_field="${live_src_field#live_source=}"
case "$live_field" in
  enabled|disabled|unreadable) probe_live="$live_field" ;;
  *) probe_live="-" ;;
esac
case "$live_src_field" in
  ''|'-') producer_live_source="-" ;;
  *) producer_live_source="$live_src_field" ;;
esac
case "$prc" in
  0) printf '%s\n' "$producer_out" ;;
  1) printf '%s\n' "$producer_out" >&2; fail=1 ;;
  2) printf '%s\n' "$producer_out" >&2; cannot_assess=1 ;;
  *) printf '%s\n' "$producer_out" >&2
     echo "check-gate-status: FAIL — the producer probe returned an unexpected code $prc" >&2
     fail=1 ;;
esac

# 6. LIVE read-back — the half that reality can contradict.
#
#    #1394 replaced the per-HEAD question with a question about the MECHANISM,
#    because the per-head form made this check unable to reach OK on the
#    repository it polices: the required context is posted on the commits a
#    verdict was made for, not on every commit, so "is it on HEAD?" answers
#    "no" for most heads while the producer is demonstrably working. The verdict
#    is now read from the live producer state (section 5's probe already read it
#    -- `live=` carries it, so the two halves cannot disagree):
#
#      enabled    the context IS being produced -> this half passes
#      disabled   the source WAS read and nothing is producing -> CANNOT-ASSESS
#      unreadable the source could NOT be read -> CANNOT-ASSESS naming it
#
#    An unobserved context when nothing is REQUIRED is not a failure at all --
#    no merge is deadlocked by the absence of a check nobody requires.
live_rc=0
if [ "$producer_now" -eq 0 ]; then
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "  note  no $context_poster status observed — the context is not REQUIRED, so no merge is deadlocked by its absence"
  fi
else
  case "$probe_live" in
    enabled)
      echo "  OK  the LIVE producer state was OBSERVED on the repository ($producer_live_source): branch protection REQUIRES '$context_poster' and the context IS being produced, so the read-back half is satisfied by the mechanism rather than by one head" ;;
    disabled)
      echo "  FAIL REQUIRED-BUT-UNOBSERVED — branch protection REQUIRES '$context_poster' and the live source WAS read ($producer_live_source), but nothing is producing the context: the requirement cannot be satisfied, and a merge would proceed only through the admin bypass (enforce_admins=false)"
      fail=1 ;;
    unreadable)
      echo "  CANNOT-ASSESS LIVE-PRODUCER-UNREADABLE — the live producer state could not be read from its source ($producer_live_source), so neither 'produced' nor 'gated off' can be claimed for the REQUIRED context '$context_poster': an unreadable source is never a pass"
      live_rc=2 ;;
    *)
      echo "  CANNOT-ASSESS  the live producer state was not resolved by the producer probe — not a pass"
      live_rc=2 ;;
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
