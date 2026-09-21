#!/usr/bin/env bash
# ============================================================================
# scripts/check-checkout-bootstrap.sh
#
# Owner-lane: fleet / watchdog
# Class: elite
# Connects-to: consumes=fleet/watchdog.py; gates=AO-GR-21,AO-GR-25,#780
# ============================================================================
#
# #780: the watchdog's stale-checkout remedy could not bootstrap itself.
#
# cron runs `cd <checkout> && python3 fleet/watchdog.py run`, so the watchdog IN
# FLIGHT is whatever copy the checkout holds. #773 gave the `checkout-behind` case
# the right remedy — fast-forward the checkout — but the code holding that remedy
# lives in the checkout, so a stale checkout executed a watchdog that could not see
# it. Measured 2026-09-15: the shared checkout stood 5 commits behind (`e9cfc10`
# merged, `HEAD` `b95a8b7`), the sister ran `592b132` for ~5.5 hours missing every
# fix merged that day, and #773's own evidence records a HUMAN doing the move.
#
# Two circularities, and this gate provokes both:
#   (1) AVAILABILITY — the remedy must not be read out of the checkout it repairs.
#   (2) DURABILITY  — a process that moved the checkout had already imported the
#                     OLD module, so the rest of its pass was the code it replaced.
#
# What this proves, against the real module (never a re-implementation) and against
# REAL git repositories, because the code under test IS `git fetch` + `git merge
# --ff-only`:
#
#   1. The freshness verdict is answered from the REMOTE. A checkout that is
#      strictly behind reads `behind`; one that is AHEAD or carries uncommitted
#      edits reads `diverged` (and is never touched); one git cannot read reads
#      `cannot-assess`, never `current`. Both commits and both blobs are named. (a)
#   2. NEGATIVE CONTROL — a checkout deliberately set behind is brought forward.
#      Its own copy of the module is proven to LACK the remedy first, so the repair
#      cannot have come from the tree being repaired. (b)
#   3. DURABILITY — after the move the process re-execs onto the code that arrived
#      (the same verb, the new file), bounded to ONE re-exec per invocation, so a
#      remedy for the checkout-behind state cannot become the runaway of #773. (c)
#   4. FAIL-CLOSED — an unreadable checkout refuses the pass (exit 2) and refuses
#      the move by name; it is never reported current and never reported repaired. (d)
#   5. MUTATION PROOF, provoked rather than asserted. One mutant is built from the
#      REAL source in a scratch tree, the mutation is proved to have LANDED
#      (sha256 before != after), and the SAME probe must DIVERGE:
#        * M1 makes the freshness read the WORKING TREE instead of the remote
#          (`remote_source_blob` -> `source_blob`), i.e. the checkout judges itself.
#          A behind checkout must stop reading `behind` and must stop being moved —
#          the measured defect in one line.
#      A happy-path-only assertion cannot pass this: the control's own premise
#      ("the stale copy lacks the remedy") is asserted, not assumed.
#
# Tri-state contract (docs/QA-GATE.md):
#   0 OK             — every case correct and the mutant caught
#   1 NOT-OK         — a case misclassified, a fold fail-open, or a mutant survived
#   2 CANNOT-ASSESS  — python3/git missing, or the tree under test cannot be imported
#
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
ROOT="$(find_repo_root)" || exit 2

fail=0
note() { printf '  %-6s %s\n' "$1" "$2"; }
bad() { note "FAIL" "$1"; fail=1; }
ok() { note "OK" "$1"; }
info() { printf '  %s\n' "$1"; }

if [ ! -f "$ROOT/fleet/watchdog.py" ]; then
  echo "check-checkout-bootstrap: CANNOT-ASSESS — fleet/watchdog.py is unreadable" >&2
  exit 2
fi
for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-checkout-bootstrap: CANNOT-ASSESS — $tool is not on PATH" >&2
    exit 2
  fi
done

# Unique scratch root: $TMPDIR is a shared, periodically-cleaned cache on this box,
# and a copy that vanishes mid-run reports a failure that is not the code's.
work="/tmp/ao-checkout-bootstrap.$(date +%s).$$"
mkdir -p "$work" || exit 2
trap 'rm -rf "$work"' EXIT

# --- the driver --------------------------------------------------------------
#
# Drives the real module against real repositories, and prints MEASUREMENTS, never
# claims. Every `git` call is a real subprocess: the remedy under test IS git.
cat > "$work/driver.py" <<'DRIVER'
"""Drive the #780 bootstrap; print MEASUREMENTS, never claims."""
import os
import subprocess
import sys
from pathlib import Path

mode = sys.argv[1]
root = Path(sys.argv[2]).resolve()
scratch = Path(sys.argv[3]).resolve()
scratch.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True

# The tree under test goes on the path FIRST, so `channel`, `runtime` and
# `governance` all resolve inside it and a mutant cannot be shadowed by the
# checkout's own modules.
sys.path.insert(0, str(root / "fleet"))
sys.path.insert(0, str(root))

import watchdog  # noqa: E402

if not Path(watchdog.__file__).resolve().is_relative_to(root):
    print(f"IMPORT-ESCAPED {watchdog.__file__}", file=sys.stderr)
    sys.exit(9)

AGENT = ["-c", "user.email=agent780@agents.invalid", "-c", "user.name=agent780"]
#: The pre-remedy revision (stand-in for the pre-#780 module) and the remote's.
PRE = '"""the revision before the checkout remedy was reachable"""\nERA = "no-remedy"\n'
FIX = '"""the revision carrying the remedy"""\nERA = "remedy"\n\n\ndef bootstrap_kernel():\n    return "remedy"\n'


def sh(*argv):
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def rev(repo, spec="HEAD"):
    return sh("git", "-C", str(repo), "rev-parse", spec).stdout.strip()


def commit_all(repo, message):
    sh("git", "-C", str(repo), "add", "-A")
    result = sh("git", "-C", str(repo), *AGENT, "commit", "-q", "-m", message)
    if result.returncode != 0:
        print(f"SCENARIO-BROKEN commit: {result.stderr}", file=sys.stderr)
        sys.exit(9)


def yesno(condition):
    return "yes" if condition else "no"


def build(where):
    """A real origin, and a real clone of it left strictly one commit BEHIND."""
    where.mkdir(parents=True, exist_ok=True)
    origin = where / "origin.git"
    sh("git", "init", "--bare", "-q", str(origin))
    # Pin the bare HEAD, so `origin/master` is the remote-tracking ref this module
    # reads whatever the host's `init.defaultBranch` happens to be.
    sh("git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/master")
    seed = where / "seed"
    sh("git", "clone", "-q", str(origin), str(seed))
    (seed / "fleet").mkdir(parents=True, exist_ok=True)
    (seed / "fleet" / "watchdog.py").write_text(PRE, encoding="utf-8")
    commit_all(seed, "the revision before the remedy")
    behind = rev(seed)
    sh("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master")
    (seed / "fleet" / "watchdog.py").write_text(FIX, encoding="utf-8")
    commit_all(seed, "the remedy")
    remote = rev(seed)
    sh("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master")
    stale = where / "stale"
    sh("git", "clone", "-q", str(origin), str(stale))
    sh("git", "-C", str(stale), "reset", "-q", "--hard", behind)
    if rev(stale) != behind:
        print("SCENARIO-BROKEN the clone was not left behind", file=sys.stderr)
        sys.exit(9)
    return seed, stale, behind, remote


if mode == "classify":
    _seed, stale, behind, remote = build(scratch / "one")

    behind_freshness = watchdog.self_freshness(stale)
    print(f"verdict_behind={behind_freshness['verdict']}")
    print(
        "behind_names_both_commits="
        + yesno(behind.startswith(behind_freshness["local_head"]) and remote.startswith(behind_freshness["remote_head"]))
    )
    print("behind_names_both_blobs=" + yesno(behind_freshness["local_blob"] != behind_freshness["remote_blob"]))
    print("behind_says_it_is_behind=" + yesno("BEHIND" in behind_freshness["reason"]))

    # The verdict is computed against the REMOTE's blob, not the working tree's.
    print("local_blob_is_the_working_tree=" + yesno(watchdog.source_blob(stale) == behind_freshness["local_blob"]))

    # A checkout whose HEAD IS the remote's, and whose file has been edited but not
    # committed, is NOT behind: a commit is an ancestor of itself, so the ancestor
    # test alone would call local work "stale" and try to overwrite it.
    sh("git", "-C", str(stale), "reset", "-q", "--hard", "origin/master")
    (stale / "fleet" / "watchdog.py").write_text("uncommitted, never committed\n", encoding="utf-8")
    print("head_equals_remote=" + yesno(rev(stale) == remote))
    print(f"verdict_uncommitted={watchdog.self_freshness(stale)['verdict']}")

    # A lane with its own commits is AHEAD, not behind, and must never be moved.
    commit_all(stale, "the lane's own work")
    diverged_freshness = watchdog.self_freshness(stale)
    print(f"verdict_ahead={diverged_freshness['verdict']}")
    ahead_head = rev(stale)
    moved, _head, detail = watchdog.bootstrap_checkout(stale)
    print("ahead_moved=" + yesno(moved))
    print("ahead_refused_by_name=" + yesno("REFUSED" in detail))
    print("ahead_head_unchanged=" + yesno(rev(stale) == ahead_head))

    # A checkout already at the remote is a no-op.
    current = scratch / "one" / "current"
    sh("git", "clone", "-q", str(scratch / "one" / "origin.git"), str(current))
    print(f"verdict_current={watchdog.self_freshness(current)['verdict']}")
    moved, _head, _detail = watchdog.bootstrap_checkout(current)
    print("current_moved=" + yesno(moved))

    # Fail-closed: a directory git cannot read is never `current`.
    plain = scratch / "plain"
    plain.mkdir(parents=True, exist_ok=True)
    plain_freshness = watchdog.self_freshness(plain)
    print(f"verdict_unreadable={plain_freshness['verdict']}")
    print("unreadable_says_why=" + yesno("cannot read" in plain_freshness["reason"]))
    print("unreadable_never_current=" + yesno(plain_freshness["verdict"] != watchdog.FRESH_CURRENT))
    moved, _head, detail = watchdog.bootstrap_checkout(plain)
    print(
        "unreadable_refused="
        + yesno(moved is False and "REFUSED" in detail and str(plain) in detail)
    )
    print(f"preflight_unreadable_rc={watchdog.bootstrap_preflight(['run'], root=plain)}")
    sys.exit(0)

if mode == "remedy":
    _seed, stale, behind, remote = build(scratch / "two")
    stale_source = (stale / "fleet" / "watchdog.py").read_text(encoding="utf-8")
    remote_source = sh("git", "-C", str(stale), "show", "origin/master:fleet/watchdog.py").stdout
    # The premise of the negative control, asserted rather than assumed: the tree
    # being repaired does NOT contain the remedy.
    print("stale_copy_lacks_remedy=" + yesno("def bootstrap_kernel" not in stale_source))
    print("remote_copy_has_remedy=" + yesno("def bootstrap_kernel" in remote_source))
    print("stale_head_is_behind=" + yesno(rev(stale) == behind))
    print(f"pre_verdict={watchdog.self_freshness(stale)['verdict']}")

    moved, _head, detail = watchdog.bootstrap_checkout(stale)

    print("remedy_moved=" + yesno(moved))
    print("remedy_head_is_the_remote=" + yesno(rev(stale) == remote))
    print("remedy_detail_names_the_move=" + yesno("fast-forwarded" in detail))
    print(f"post_verdict={watchdog.self_freshness(stale)['verdict']}")
    sys.exit(0)

if mode == "reexec":
    _seed, stale, _behind, remote = build(scratch / "three")
    os.environ.pop(watchdog.ENV_BOOTSTRAPPED, None)
    calls = []

    def fake_execv(path, argv):  # never returns in production; recorded here
        calls.append((path, list(argv)))

    watchdog.os.execv = fake_execv
    rc = watchdog.bootstrap_preflight(["run"], root=stale)
    print("reexec_rc_is_none=" + yesno(rc is None))
    print(f"reexec_calls={len(calls)}")
    print("reexec_same_verb=" + yesno(len(calls) == 1 and calls[0][1][2:] == ["run"]))
    print("reexec_onto_this_file=" + yesno(len(calls) == 1 and Path(calls[0][1][1]).name == "watchdog.py"))
    print("reexec_marks_the_process=" + yesno(os.environ.get(watchdog.ENV_BOOTSTRAPPED) == "1"))
    print("reexec_moved_the_checkout=" + yesno(rev(stale) == remote))

    # The bound: the re-exec'd process must NOT re-exec again.
    _seed2, stale2, _behind2, remote2 = build(scratch / "four")
    os.environ[watchdog.ENV_BOOTSTRAPPED] = "1"
    calls.clear()
    rc2 = watchdog.bootstrap_preflight(["run"], root=stale2)
    print("bounded_rc_is_none=" + yesno(rc2 is None))
    print(f"bounded_reexec_calls={len(calls)}")
    print("bounded_still_moved=" + yesno(rev(stale2) == remote2))
    sys.exit(0)

print(f"UNKNOWN-MODE {mode}", file=sys.stderr)
sys.exit(9)
DRIVER

run_driver() { # run_driver <root> <state-dir> <mode> <out>
  local root="$1" state="$2" mode="$3" out="$4"
  mkdir -p "$state"
  # The remedy knobs are scrubbed so a developer's environment cannot change what
  # this gate measures, and AO_FLEET_DIR is redirected so a pass never writes into
  # the checkout's real `.fleet/`.
  env -u AO_WATCHDOG_RESPAWN_ATTEMPTS -u AO_WATCHDOG_RESPAWN_BACKOFF -u AO_WATCHDOG_BOOTSTRAPPED \
    AO_FLEET_DIR="$state" PYTHONDONTWRITEBYTECODE=1 \
    python3 "$work/driver.py" "$mode" "$root" "$state-scratch" > "$out" 2>&1
}

kv() { # kv <file> <key> -> value (empty when absent)
  sed -n "s/^$2=//p" "$1" | head -1
}

expect() { # expect <file> <key> <expected> <description>
  local got
  got="$(kv "$1" "$2")"
  if [ "$got" = "$3" ]; then
    ok "$4"
  else
    bad "$4 (expected $2=$3, measured $got)"
  fi
}

# --- 1. the real tree: the classification -------------------------------------
echo "== the freshness verdict (the real watchdog, real repositories) =="
classify_out="$work/classify.out"
run_driver "$ROOT" "$work/state-classify" classify "$classify_out"
classify_rc=$?
if [ "$classify_rc" -eq 9 ]; then
  echo "check-checkout-bootstrap: CANNOT-ASSESS — the probe imported code outside the tree under test" >&2
  exit 2
fi
if [ "$classify_rc" -ne 0 ]; then
  bad "the classification probe could not run against the real tree (rc=$classify_rc)"
  sed -n '1,20p' "$classify_out" | sed 's/^/    /'
else
  info "$(head -1 "$classify_out")"
fi

expect "$classify_out" verdict_behind "behind" "a checkout strictly behind origin/master reads 'behind'"
expect "$classify_out" behind_names_both_commits "yes" "and names BOTH commits (the requirement of #780)"
expect "$classify_out" behind_names_both_blobs "yes" "and names both blobs, so the compared bytes are auditable"
expect "$classify_out" behind_says_it_is_behind "yes" "and says which side is stale"
expect "$classify_out" local_blob_is_the_working_tree "yes" "the working-tree side is the file on disk, not the commit"

# The strictness that a naive ancestor test gets wrong.
expect "$classify_out" head_equals_remote "yes" "the premise for the uncommitted-edit case"
expect "$classify_out" verdict_uncommitted "diverged" "HEAD == remote with edited bytes is LOCAL WORK, not 'behind'"
expect "$classify_out" verdict_ahead "diverged" "a checkout carrying its own commits reads 'diverged', not 'behind'"
expect "$classify_out" ahead_moved "no" "and is never moved"
expect "$classify_out" ahead_refused_by_name "yes" "the refusal is named, not silent"
expect "$classify_out" ahead_head_unchanged "yes" "and the lane's work is untouched"

expect "$classify_out" verdict_current "current" "a checkout at the remote reads 'current'"
expect "$classify_out" current_moved "no" "and is left alone"

# Fail-closed: #739's rule, one level up.
expect "$classify_out" verdict_unreadable "cannot-assess" "an unreadable checkout is CANNOT-ASSESS"
expect "$classify_out" unreadable_never_current "yes" "never 'current' — 'I cannot tell' must not read as 'I am current'"
expect "$classify_out" unreadable_says_why "yes" "and says why it could not be judged"
expect "$classify_out" unreadable_refused "yes" "the move is refused by name, naming the tree"
expect "$classify_out" preflight_unreadable_rc "2" "and the pass is refused with CANNOT-ASSESS, not 0"

# --- 2. the negative control: a deliberately stale checkout is repaired--------
echo "== NEGATIVE CONTROL: a checkout deliberately set behind (real repositories) =="
remedy_out="$work/remedy.out"
run_driver "$ROOT" "$work/state-remedy" remedy "$remedy_out"
if [ "$?" -eq 9 ]; then
  bad "the remedy probe imported code outside the tree under test (vacuous control)"
else
  info "$(kv "$remedy_out" pre_verdict)"
fi

expect "$remedy_out" stale_head_is_behind "yes" "the fixture really is behind (a real clone, really reset back)"
expect "$remedy_out" stale_copy_lacks_remedy "yes" "the tree being repaired does NOT contain the remedy"
expect "$remedy_out" remote_copy_has_remedy "yes" "while origin/master's copy does — so the repair cannot have come from the stale tree"
expect "$remedy_out" pre_verdict "behind" "the stale checkout is classified 'behind' before the remedy runs"
expect "$remedy_out" remedy_moved "yes" "the deliberately stale checkout IS brought forward"
expect "$remedy_out" remedy_head_is_the_remote "yes" "and lands exactly on origin/master"
expect "$remedy_out" remedy_detail_names_the_move "yes" "the move is named, not claimed"
expect "$remedy_out" post_verdict "current" "and the checkout now holds the code it is executing"

# --- 3. durability: the pass continues on the code that arrived ---------------
echo "== DURABILITY: the re-exec, and its bound =="
reexec_out="$work/reexec.out"
run_driver "$ROOT" "$work/state-reexec" reexec "$reexec_out"
if [ "$?" -eq 9 ]; then
  bad "the re-exec probe imported code outside the tree under test (vacuous control)"
fi

expect "$reexec_out" reexec_calls "1" "after the move the process EXACTLY one re-execs onto the new code"
expect "$reexec_out" reexec_same_verb "yes" "re-running the same verb, so the pass is the code that arrived"
expect "$reexec_out" reexec_onto_this_file "yes" "onto this module's path, which the fast-forward just rewrote"
expect "$reexec_out" reexec_marks_the_process "yes" "marking the process so the bootstrap cannot repeat"
expect "$reexec_out" reexec_moved_the_checkout "yes" "with the move actually performed"
expect "$reexec_out" bounded_reexec_calls "0" "and a second pass does NOT re-exec again"
expect "$reexec_out" bounded_still_moved "yes" "while still repairing the checkout — only the re-exec is bounded"

# --- 4. the mutation proof ----------------------------------------------------
copy_tree() { # copy_tree <dest>
  mkdir -p "$1" || return 1
  local part
  for part in fleet governance; do
    [ -e "$ROOT/$part" ] || continue
    cp -R "$ROOT/$part" "$1/$part" 2>/dev/null || return 1
  done
  # A stale __pycache__ carried into the copy would shadow the mutated source and
  # make the control vacuous.
  find "$1" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  return 0
}

sha_of() { python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

echo "== mutant 1: the checkout judges itself (the measured defect in one line) =="
mutant_tree="$work/mutant-self"
copy_tree "$mutant_tree" || {
  echo "check-checkout-bootstrap: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/fleet/watchdog.py")"
python3 - "$mutant_tree/fleet/watchdog.py" <<'MUTATE'
"""Make the freshness read the WORKING TREE instead of the remote."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    remote_blob = remote_source_blob(target, remote=remote)"
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(needle, "    remote_blob = source_blob(target)  # MUTANT: the tree judges itself", 1)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the self-judging mutation could not be constructed (the source moved)"
else
  after="$(sha_of "$mutant_tree/fleet/watchdog.py")"
  if [ "$after" = "$before" ]; then
    bad "M1: the mutation did not land (sha256 unchanged) — the control would be vacuous"
  else
    ok "M1: the mutation landed (sha256 ${before:0:12} -> ${after:0:12})"
    case "$(cat "$mutant_tree/fleet/watchdog.py")" in
      *MUTANT*) ok "M1: the mutated file carries its MUTANT marker" ;;
      *) bad "M1: the mutated file carries no MUTANT marker — the edit is not identifiable" ;;
    esac
    m1_remedy="$work/mutant-self-remedy.out"
    run_driver "$mutant_tree" "$work/state-m1r" remedy "$m1_remedy"
    m1_verdict="$(kv "$m1_remedy" pre_verdict)"
    m1_moved="$(kv "$m1_remedy" remedy_moved)"
    if [ "$m1_verdict" != "behind" ] && [ "$m1_moved" = "no" ]; then
      ok "M1 caught: a behind checkout reads '$m1_verdict' and is NOT moved (moved=$m1_moved) — the stale copy would judge itself current"
    else
      bad "M1 survived: the checkout still judges itself against the remote (verdict=$m1_verdict moved=$m1_moved)"
    fi
  fi
fi

# --- 5. the check is wired, not a formality -----------------------------------
echo "== wiring =="
wiring="$(cd "$ROOT" && bash -c 'source scripts/discover-checks.sh 2>/dev/null; discover_check_scripts 2>/dev/null')"
if [ -z "$wiring" ]; then
  echo "check-checkout-bootstrap: CANNOT-ASSESS — scripts/discover-checks.sh could not be sourced" >&2
  exit 2
fi
case "$wiring" in
  *"checkout-bootstrap|bash scripts/check-checkout-bootstrap.sh"*)
    ok "this check is auto-discovered by scripts/discover-checks.sh, so make verify runs it"
    ;;
  *)
    bad "this check is NOT discovered — make verify would not run it"
    ;;
esac

denylisted=no
if [ -f "$ROOT/scripts/check-denylist.txt" ]; then
  while IFS= read -r entry; do
    case "$entry" in
      ''|'#'*) continue ;;
    esac
    case "$entry" in
      checkout-bootstrap|check-checkout-bootstrap.sh) denylisted=yes ;;
    esac
  done < "$ROOT/scripts/check-denylist.txt"
fi
if [ "$denylisted" = "no" ]; then
  ok "and it is not denylisted"
else
  bad "this check is denylisted, so make verify would skip it"
fi

# --- verdict ------------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo "check-checkout-bootstrap: FAIL — the checkout-behind remedy is not provably reachable" >&2
  exit 1
fi
echo "check-checkout-bootstrap: OK — the verdict is answered from the remote, a deliberately stale checkout is brought forward without its own copy of the remedy, the re-exec is bounded, the unreadable case is fail-closed, and the self-judging mutant is caught"
exit 0
