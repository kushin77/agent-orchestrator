#!/usr/bin/env bash
# check-lane-base-drift.sh — does a lane's work overlap what MASTER changed since
# the lane forked? (issue #2046)
#
# THE DEFECT THIS EXISTS FOR
#   Measured 2026-09-22: a full lane was implemented, verified and turned into a
#   public PR (#2045) for a red origin/master had ALREADY fixed. The red was real
#   at the lane's base `90e172fc`; master had fixed it in `a661d5ae` by rewriting
#   the same clause in the same three files the lane was rewriting. Nothing said
#   so until merge time (`gh pr merge` -> `mergeable=false state=dirty`, then a
#   rebase conflict in `governance/modules/vendoring.py` and `controls.yaml`).
#   The command that would have caught it before a line was written:
#
#       git diff --name-only <my-base>..origin/master
#
#   `scripts/check-lane-collision.sh` (#740) does NOT cover this: it checks
#   pairwise file-disjointness WITHIN a ready wave, against each child's own
#   `Files:` declaration, at dispatch time. It never reads `origin/master` at all
#   (measured: `grep -nE 'origin/master|merge-base|diff --name-only'` on that file
#   returns nothing). Two lanes disjoint from each OTHER can both collide with
#   what master landed after they forked.
#
# THE PREDICATE
#   For a lane branch B cut from a fork point F = merge-base(origin/master, B):
#
#       mine   = git diff --name-only F..B
#       theirs = git diff --name-only F..origin/master
#       drift  = mine INTERSECT theirs
#
#   A non-empty `drift` means one of exactly two things, and both are worth a
#   refusal by name: master already did this work (the lane is a duplicate), or
#   the lane will conflict on merge and needs a rebase before it is "done".
#
# WHAT IS PROVEN HERE (against real git repositories, not a description of them)
#   * A lane whose file set intersects post-fork master changes is REFUSED BY
#     NAME, naming the file AND the master commit that touched it.
#   * Vacuity control: a lane whose files master did NOT touch produces NO
#     finding — a rule that fires on everything is not a rule.
#   * An unresolvable fork point is CANNOT-ASSESS (rc 2), never a pass (GR-12).
#   * The predicate is load-bearing: with the intersection neutered in a scratch
#     copy, the SAME negative control is admitted.
#
# WHY THE LIVE FLEET SCAN IS ADVISORY
#   The same reason `check-scratch-safety.sh`'s live verdict is: this box carries
#   dozens of open lane records, and a gate that reddens an unrelated diff
#   because a NEIGHBOUR's lane overlaps master is a false red machine. So:
#     * this worktree's OWN lane (`AO_ISSUE`, or the branch it is standing on)
#       IS refused by name — that is this diff's own business, exactly as every
#       other check judges the tree it runs in;
#     * every OTHER lane is reported as a NOTE, never a red;
#     * `--scan` is the verb that refuses by name for the whole live set on
#       demand, and `--lane <n>` for one lane (the preflight/claim path).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network, no writes
# outside a scratch directory.

# ---knowledge---
# module_id: scripts.check-lane-base-drift
# system: scripts
# app: scripts
# solution_class: class
# patterns: [tri-state-gate, provoked-negative-control, refusal-by-name]
# derives_from: scripts/check-lane-collision.sh
# owner_sme: qa-sme
# tier: L1
# interfaces: [stdout: tri-state verdict lines, exit: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS]
# invariants: "an unresolvable fork point is CANNOT-ASSESS, never a pass; a neighbour's stale lane is a NOTE, never a red"
# gotchas: "refs/remotes/origin/issue-* outlive their PRs, so the fleet-wide scan counts stale lanes and is ADVISORY only; the high-signal verb is --lane <n>. A drift is a CANDIDATE duplicate: the refusal names the master commit so a reader judges duplicate-vs-rebase."
# related: ["#740", "#2046", "#1642"]
# do_not_duplicate: scripts/check-lane-collision.sh
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
# `git -C` does NOT override an exported GIT_DIR and `git -C` WALKS UP into an
# enclosing repo, so a caller that exports one makes this gate act on another
# repository -- fatal for a check whose whole job is measuring which repository a
# branch belongs to (#1642).
# shellcheck source=scripts/lib/unset-git-env.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/unset-git-env.sh"

root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lane-base-drift: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

mode="gate"
lane=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scan) mode="scan"; shift ;;
    --lane) lane="${2:-}"; mode="lane"; shift 2 ;;
    -h|--help)
      echo "usage: $0 [--scan | --lane <n>]" >&2
      exit 0
      ;;
    *) echo "check-lane-base-drift: usage: $0 [--scan | --lane <n>]" >&2; exit 2 ;;
  esac
done

ok=0
fail=0
note() { printf '  NOTE  %s\n' "$1"; }
good() { printf '  OK    %s\n' "$1"; ok=$((ok + 1)); }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

tmp="$(mktemp -d /tmp/ao-lane-drift.XXXXXX)" || { echo "check-lane-base-drift: CANNOT-ASSESS — cannot create a scratch dir" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT

# --- the predicate, in one place, used by both the fixtures and the live scan ---
# Reads: repo dir, fork base rev, lane rev, master rev. Prints one line per drifted
# file: "<file>\t<master commit>\t<master subject>". Prints nothing when clean.
drift_report() { # drift_report <repo> <lane-rev> <master-rev> [<base-rev>]
  python3 - "$1" "$2" "$3" "${4:-}" <<'PY'
import subprocess
import sys

repo, lane, master, base = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


def git(*args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


if not base:
    proc = git("merge-base", master, lane)
    if proc.returncode != 0 or not proc.stdout.strip():
        sys.exit(3)  # unresolvable fork point -> CANNOT-ASSESS
    base = proc.stdout.strip()

mine = set(git("diff", "--name-only", "{}..{}".format(base, lane)).stdout.split())
theirs = set(git("diff", "--name-only", "{}..{}".format(base, master)).stdout.split())
for path in sorted(mine & theirs):
    log = git("log", "-1", "--format=%h%x09%s", master, "--", path).stdout.strip()
    sha, _, subject = log.partition("\t")
    print("{}\t{}\t{}".format(path, sha or "?", subject[:72]))
PY
}

emits() { # emits <repo> <lane> <master> : rc 0 and >=1 line
  local out rc=0
  out="$(drift_report "$1" "$2" "$3" 2>/dev/null)" || rc=$?
  [ "$rc" -eq 0 ] && [ -n "$out" ]
}

# =====================================================================
# The live set: every `issue-<n>` branch that exists locally or as a fetched
# remote-tracking ref. Offline by design — no `git ls-remote`. ONE python call for
# the whole set: the first version spawned a process per lane, which on this box
# (~170 stale lane refs) made the gate take minutes.
live_report() { # prints "<n>\t<file>\t<sha>" for every drifting lane
  python3 - "$root" origin/master <<'PY'
import re
import subprocess
import sys

repo, master = sys.argv[1], sys.argv[2]


def git(*args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


refs = git("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin").stdout.split()
lanes = {}
for ref in refs:
    m = re.match(r"^(?:origin/)?issue-(\d+)$", ref)
    if m and m.group(1) not in lanes:
        lanes[m.group(1)] = ref
for n, ref in sorted(lanes.items(), key=lambda kv: int(kv[0])):
    mb = git("merge-base", master, ref)
    if mb.returncode != 0 or not mb.stdout.strip():
        continue
    base = mb.stdout.strip()
    mine = set(git("diff", "--name-only", base + ".." + ref).stdout.split())
    theirs = set(git("diff", "--name-only", base + ".." + master).stdout.split())
    for drift_path in sorted(mine & theirs):
        sha = git("log", "-1", "--format=%h", master, "--", drift_path).stdout.strip()
        print("{}\t{}\t{}".format(n, drift_path, sha or "?"))
PY
}

if [ "$mode" = "lane" ]; then
  # Documented seam (the provocation half of this gate uses it to point the SAME
  # code path at a fixture repository, so the mutant is EXECUTED rather than
  # re-implemented — a mutation that swaps the function but not the ops tests
  # nothing). Defaults are the real repository and the real master ref.
  probe_root="${AO_LANE_DRIFT_ROOT:-$root}"
  probe_master="${AO_LANE_DRIFT_MASTER:-origin/master}"
  echo "== lane #$lane against $probe_master =="
  probe_lane="${AO_LANE_DRIFT_BRANCH:-}"
  if [ -n "$probe_lane" ]; then
    git -C "$probe_root" rev-parse --verify -q "$probe_lane" >/dev/null 2>&1 || {
      echo "check-lane-base-drift: CANNOT-ASSESS — no branch $probe_lane to measure" >&2
      exit 2
    }
  elif ! git -C "$probe_root" rev-parse --verify -q "issue-$lane" >/dev/null 2>&1 \
     && ! git -C "$probe_root" rev-parse --verify -q "origin/issue-$lane" >/dev/null 2>&1; then
    echo "check-lane-base-drift: CANNOT-ASSESS — no branch issue-$lane to measure in $probe_root" >&2
    exit 2
  fi
  rev="${probe_lane:-issue-$lane}"
  if [ -z "$probe_lane" ]; then
    git -C "$probe_root" rev-parse --verify -q "$rev" >/dev/null 2>&1 || rev="origin/issue-$lane"
  fi
  rc=0
  out="$(drift_report "$probe_root" "$rev" "$probe_master" 2>&1)" || rc=$?
  if [ "$rc" -eq 3 ]; then
    echo "check-lane-base-drift: CANNOT-ASSESS — no fork point between $rev and origin/master" >&2
    exit 2
  fi
  if [ -z "$out" ]; then
    echo "check-lane-base-drift: OK — issue-$lane touches no file master changed since it forked"
    exit 0
  fi
  printf '%s\n' "$out" | while IFS=$'\t' read -r file sha subject; do
    printf 'lane-base-drift: %s changed on master at %s since issue-%s forked — %s\n' "$file" "$sha" "$lane" "$subject" >&2
  done
  echo "check-lane-base-drift: NOT-OK — issue-$lane overlaps post-fork master changes (rebased or duplicate?)" >&2
  exit 1
fi

if [ "$mode" = "scan" ]; then
  echo "== the whole live lane set, refusing by name =="
  rows="$(live_report)"
  if [ -z "$rows" ]; then
    echo "check-lane-base-drift: OK — no live lane overlaps post-fork master changes"
    exit 0
  fi
  printf '%s\n' "$rows" | while IFS=$'\t' read -r n file sha; do
    printf 'lane-base-drift: %s changed on master at %s since issue-%s forked\n' "$file" "$sha" "$n" >&2
  done
  echo "check-lane-base-drift: NOT-OK — $(printf '%s\n' "$rows" | wc -l) drift(s) across the live set" >&2
  exit 1
fi

# =====================================================================
echo "== provoked controls (scratch repositories only) =="
# A fixture is a real repo with a real fork: master advances after the lane forks.
make_fixture() { # make_fixture <dir> <file-master-touches>
  local d="$1" shared="$2"
  git init -q -b master "$d" 2>/dev/null || return 1
  git -C "$d" config user.email "gate@example.invalid"
  git -C "$d" config user.name "gate"
  printf 'base\n' > "$d/common.txt"
  printf 'base\n' > "$d/$shared"
  git -C "$d" add -A >/dev/null && git -C "$d" commit -qm "base"
  # the lane forks here and edits its own file
  git -C "$d" checkout -q -b lane
  printf 'lane\n' > "$d/lane-only.txt"
  printf 'lane edit\n' > "$d/$shared"
  git -C "$d" add -A >/dev/null && git -C "$d" commit -qm "the lane's work"
  # master moves on afterwards
  git -C "$d" checkout -q master
  printf 'master edit\n' > "$d/$shared"
  git -C "$d" add -A >/dev/null && git -C "$d" commit -qm "master's own later work"
}

# 1. the negative control: master touched the SAME file the lane did -> REFUSED
fx="$tmp/same-file"
make_fixture "$fx" "shared.txt" || bad "the fixture repository could not be created"
if emits "$fx" lane master; then
  named="$(drift_report "$fx" lane master 2>/dev/null | head -n1 | cut -f1)"
  sha="$(drift_report "$fx" lane master 2>/dev/null | head -n1 | cut -f2)"
  if [ "$named" = "shared.txt" ] && [ -n "$sha" ] && [ "$sha" != "?" ]; then
    good "a lane file master also changed is refused by name (lane-base-drift: $named, master commit $sha)"
  else
    bad "the refusal did not name the file and the master commit (got '$named' / '$sha')"
  fi
else
  bad "a lane file master also changed was ADMITTED (the predicate cannot fail here)"
fi

# 2. vacuity control: master's change is OUTSIDE the lane's file set -> no finding
fx="$tmp/other-file"
if git init -q -b master "$fx" 2>/dev/null; then
  git -C "$fx" config user.email "gate@example.invalid"
  git -C "$fx" config user.name "gate"
  printf 'base\n' > "$fx/shared.txt"
  printf 'base\n' > "$fx/elsewhere.txt"
  git -C "$fx" add -A >/dev/null && git -C "$fx" commit -qm "base"
  git -C "$fx" checkout -q -b lane
  printf 'lane\n' > "$fx/shared.txt"
  git -C "$fx" add -A >/dev/null && git -C "$fx" commit -qm "the lane's work"
  git -C "$fx" checkout -q master
  printf 'master\n' > "$fx/elsewhere.txt"
  git -C "$fx" add -A >/dev/null && git -C "$fx" commit -qm "master elsewhere"
  if emits "$fx" lane master; then
    bad "a lane whose files master did NOT touch was still refused (a rule matching everything is not a rule)"
  else
    good "a lane whose files master did not touch produces no finding (the rule is not always-red)"
  fi
else
  bad "the vacuity fixture repository could not be created"
fi

# 3. CANNOT-ASSESS: no common ancestor at all -> rc 2, never a pass
fx="$tmp/unrelated"
if git init -q -b master "$fx" 2>/dev/null; then
  git -C "$fx" config user.email "gate@example.invalid"
  git -C "$fx" config user.name "gate"
  printf 'a\n' > "$fx/a.txt"
  git -C "$fx" add -A >/dev/null && git -C "$fx" commit -qm "master lineage"
  git -C "$fx" checkout -q --orphan lane
  git -C "$fx" rm -q -rf . >/dev/null 2>&1
  printf 'b\n' > "$fx/b.txt"
  git -C "$fx" add -A >/dev/null && git -C "$fx" commit -qm "an unrelated lineage"
  rc=0
  drift_report "$fx" lane master >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 3 ]; then
    good "an unresolvable fork point is CANNOT-ASSESS, never a pass (rc 3 -> the gate's rc 2)"
  else
    bad "an unresolvable fork point did not answer CANNOT-ASSESS (rc=$rc)"
  fi
else
  bad "the unrelated-lineage fixture repository could not be created"
fi

# 4. GR-12: the predicate must be load-bearing. Neuter the intersection in a
#    scratch copy of this script's python block and watch the SAME negative
#    control stop refusing. The real file is never written.
self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
sha_before="$(sha256sum "$self" | cut -d' ' -f1)"
mutant="$tmp/mutant.sh"
# The mutant sources lib/ relative to its own location (lib/common.sh +
# lib/unset-git-env.sh), so a copy in $tmp would die at line 1 sourcing a lib it
# does not carry — measured rc=2 on the mutant before this, which read as
# "CANNOT-ASSESS" and made the control compare a crash against a refusal. The
# same shape check-duplicates.sh uses: copy the lib beside the mutant so the
# mutant is a WORKING script whose only difference is the predicate.
mkdir -p "$tmp/lib"
cp "$(dirname "$self")/lib/common.sh" "$tmp/lib/common.sh"
cp "$(dirname "$self")/lib/unset-git-env.sh" "$tmp/lib/unset-git-env.sh"
python3 - "$self" "$mutant" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
# Split so this needle literal is not itself a second occurrence of the code it
# matches, AND anchored on the FOLLOWING line: after the live scan was collapsed
# into one python call this file has two intersection loops, so the one-line anchor
# matched twice and the provocation silently stopped taking (the count assertion is
# what surfaced it, all three times -- including once where THIS comment was the
# second match, because a comment quoting the anchor matches it too).
needle = "for path in sorted(mine " + "& theirs):"
repl = "for path in sorted(set()):  # MUTANT: nothing ever drifts"
if text.count(needle) != 1:
    sys.exit("the mutation anchor does not occur exactly once (%d)" % text.count(needle))
open(dst, "w", encoding="utf-8").write(text.replace(needle, repl))
PY
if [ -f "$mutant" ]; then
  chmod +x "$mutant"
  took="$(grep -c 'MUTANT' "$mutant")"
  fx="$tmp/mutant-check"
  make_fixture "$fx" "shared.txt" || true
  # BOTH run the SAME code path (--lane) against the SAME fixture; only the
  # predicate differs. The real one must refuse, the mutant must admit.
  real_rc=0
  AO_LANE_DRIFT_ROOT="$fx" AO_LANE_DRIFT_MASTER=master AO_LANE_DRIFT_BRANCH=lane \
    bash "$self" --lane 1 >/dev/null 2>&1 || real_rc=$?
  mut_rc=0
  mut_out="$(AO_LANE_DRIFT_ROOT="$fx" AO_LANE_DRIFT_MASTER=master AO_LANE_DRIFT_BRANCH=lane \
    bash "$mutant" --lane 1 2>&1)" || mut_rc=$?
  if [ "$took" -ge 1 ] && [ "$real_rc" -eq 1 ] && [ "$mut_rc" -eq 0 ]; then
    good "the predicate is load-bearing: the same fixture is refused by the real check (rc 1) and ADMITTED by the mutant (rc 0); $took mutant line(s)"
  else
    bad "the mutation did not demonstrably change the outcome (real rc=$real_rc, mutant rc=$mut_rc, mutant lines=$took) ${mut_out:0:80}"
  fi
  rm -f "$mutant"
else
  bad "the mutant could not be written (the provocation did not take)"
fi
sha_after="$(sha256sum "$self" | cut -d' ' -f1)"
if [ "$sha_before" = "$sha_after" ]; then
  good "this script is unmodified by its own provocation (sha256 unchanged)"
else
  bad "the provocation wrote to the real script (sha256 changed)"
fi

# --- gate mode: this worktree's own lane refuses; neighbours are NOTES ----------
echo "== this worktree's own lane =="
own="${AO_ISSUE:-}"
if [ -z "$own" ]; then
  br="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo)"
  own="$(printf '%s' "$br" | sed -nE 's#^issue-([0-9]+)$#\1#p')"
fi
if [ -z "$own" ]; then
  note "no lane of its own here (detached HEAD and no AO_ISSUE) — the live set is reported below, never as a red"
else
  rc=0
  out="$(drift_report "$root" HEAD origin/master 2>&1)" || rc=$?
  if [ "$rc" -eq 3 ]; then
    note "lane #$own: CANNOT-ASSESS — no fork point between HEAD and origin/master"
  elif [ -z "$out" ]; then
    good "lane #$own touches no file master changed since it forked"
  else
    printf '%s\n' "$out" | while IFS=$'\t' read -r file sha subject; do
      printf '  FAIL  lane-base-drift: %s changed on master at %s since lane #%s forked — %s\n' \
        "$file" "$sha" "$own" "$subject" >&2
    done
    fail=$((fail + 1))
  fi
fi

# The fleet-wide scan is ADVISORY here by design (see the header): a neighbour's
# stale lane must never redden an unrelated diff. `--scan` is the refusing verb.
lrc=0
rows="$(live_report)" || lrc=$?
if [ "$lrc" -ne 0 ]; then
  note "advisory scan UNAVAILABLE (live_report rc=$lrc) — not a clean result; run --scan by hand"
elif [ -n "$rows" ]; then
  n_lanes="$(printf '%s\n' "$rows" | cut -f1 | sort -u | wc -l)"
  n_files="$(printf '%s\n' "$rows" | wc -l)"
  note "advisory: $n_files file(s) across $n_lanes live lane(s) also changed on master since they forked — run --scan to refuse by name, --lane <n> for one lane"
else
  good "no live lane overlaps post-fork master changes (advisory scan is clean)"
fi

echo
if [ "$fail" -gt 0 ]; then
  printf 'check-lane-base-drift: NOT-OK — %d control(s)/lane(s) failed\n' "$fail" >&2
  exit 1
fi
printf 'check-lane-base-drift: OK — %d control(s) passed (predicate load-bearing, vacuity control clean, CANNOT-ASSESS honest, this lane drift-free)\n' "$ok"
exit 0
