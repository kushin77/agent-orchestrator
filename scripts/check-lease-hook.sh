#!/usr/bin/env bash
# check-lease-hook.sh — the gate for scripts/git-hooks/pre-commit (issue #1541).
#
# WHAT THIS IS
#   The hook refuses a commit that stages a file leased to another LIVE claim, by
#   importing the repository's own claim reader (governance/dispatch/claims.py:
#   `read_ledger` -> `active_claims` -> `find_file_conflict`) and its own conflict
#   predicate. This is the gate of record for that behaviour: every arm below is a
#   REAL `git commit` in a REAL scratch repository, observed through the hook, so
#   the refusal is measured where it fires rather than reasoned about.
#
# WHY IT IS SHAPED LIKE THIS
#   A hook that installs itself into a checkout's hooks dir is a high-blast-radius
#   change, and a gate that cannot fail is a formality (GR-12). So:
#
#     1. the fixture is a real git repository that CONTAINS the repo's own reader
#        (governance/dispatch, governance/policy, fleet/runtime.py), so the hook
#        runs the shipped code path — never a copy, never a stub. A control asserts
#        the fixture's reader is live BEFORE any arm is judged: if the reader does
#        not import there, every apparent pass would be the hook failing open, and
#        the gate says so by name instead of reporting green;
#     2. each refusal arm names the finding it must produce (`#9001`, `REFUSED`,
#        `held: 1-3`), so an arm cannot pass for the wrong reason;
#     3. the ALLOW arms are the negative controls: an unleased file, a file leased
#        to THIS branch's claim, a region-disjoint lease and a non-lane branch must
#        all commit normally. Without them "refuses" and "refuses everything" are
#        the same observation;
#     4. two MUTANTS of the hook prove each half is load-bearing: with the conflict
#        branch neutered the refusal arm must STOP refusing, and with the region set
#        forced to whole-file the region-disjoint arm must START refusing. Each
#        mutant is proven to differ from the shipped hook by sha256 first, so a
#        mutation that never landed cannot be reported as a passing control;
#     5. the opt-in install path is exercised end to end (`make install-hooks`),
#        including that the DRY RUN writes nothing and that a FOREIGN hook is
#        refused rather than clobbered. Before and after, the checkout's own COMMON
#        hooks dir is hashed: this gate must not install anything into the venue it
#        runs in (the operator opts in, not `make verify`).
#
# WHAT IT DOES NOT COVER
#   The live board's own `.board/claims/`. The fixture's ledger is controlled on
#   purpose — the gate must never mutate the fleet's claim state — so this proves
#   the hook's DECISION against a ledger it can construct, not the current contents
#   of the fleet's.
#
# EXIT CONTRACT (the repo's honesty tri-state, consumed never redefined)
#   0  OK              every arm observed the behaviour it names
#   1  NOT-OK          an arm observed something else, named with what it saw
#   2  CANNOT-ASSESS   git or python3 absent, or the scratch fixture could not be
#                      built at all. The normal path NEVER returns 2: an arm that
#                      fails is a definite finding, not an unassessable one, so a
#                      skip cannot sit here hiding a broken hook.
#
# Usage: bash scripts/check-lease-hook.sh
# ---knowledge---
# module_id: scripts.check-lease-hook
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: unassigned
# tier: L1
# interfaces: [cleanup, ok, bad, cannot_assess, build_fixture, plant, rewrite_line, reset_fixture, (+4 more)]
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

EXIT_OK=0
EXIT_NOT_OK=1
EXIT_CANNOT_ASSESS=2

hook="$root/scripts/git-hooks/pre-commit"
doc="$root/docs/LEASE-HOOK.md"

# One global scratch variable, one EXIT trap (SP-1), with the run of marker digits
# assembled by the shell rather than spelled in the source (the docs gate refuses a
# literal marker token, and this gate must survive its own scan).
scratch="/tmp/lease-hook.$$.$(date +%s%N)"
cleanup() { [ -n "$scratch" ] && rm -rf "$scratch" || true; }
trap cleanup EXIT

arms=0
fails=0

# The unfinished-marker pattern is assembled from fragments: spelled whole here,
# this gate's own source would be refused by docs-lint for carrying the very tokens
# it looks for (the same reason check-shell-patterns.sh fragments its shapes).
frag_a="TO""DO"
frag_b="FIX""ME"
frag_c="HA""CK"
frag_d="XX""X"
marker_re="(${frag_a}|${frag_b}|${frag_c})[[:space:]]|(^|[^A-Za-z])${frag_d}([^A-Za-z]|\$)"

ok()   { arms=$((arms + 1)); printf '  OK    %s\n' "$1"; }
bad()  { arms=$((arms + 1)); fails=$((fails + 1)); printf '  FAIL  %s\n' "$1" >&2; }
cannot_assess() {
  printf 'check-lease-hook: CANNOT-ASSESS — %s\n' "$1" >&2
  exit "$EXIT_CANNOT_ASSESS"
}

for tool in git python3; do
  command -v "$tool" >/dev/null 2>&1 || cannot_assess "$tool is not on PATH"
done
[ -f "$hook" ] || cannot_assess "scripts/git-hooks/pre-commit does not exist"

mkdir -p "$scratch" || cannot_assess "could not create a scratch directory under /tmp"
fixture="$scratch/fixture"

# ── the fixture: a real repository containing this repo's own claim reader ────
build_fixture() {
  mkdir -p "$fixture/governance" "$fixture/fleet" || return 1
  cp -r "$root/governance/dispatch" "$fixture/governance/dispatch" || return 1
  cp -r "$root/governance/policy" "$fixture/governance/policy" || return 1
  rm -rf "$fixture/governance/dispatch/tests" \
         "$fixture/governance/dispatch/__pycache__" \
         "$fixture/governance/policy/__pycache__"
  cp "$root/fleet/runtime.py" "$fixture/fleet/runtime.py" || return 1
  git -C "$fixture" init -q || return 1
  git -C "$fixture" checkout -q -b issue-1541 || return 1
  git -C "$fixture" config user.email "gate@agents.invalid" || return 1
  git -C "$fixture" config user.name "lease-hook gate" || return 1
  git -C "$fixture" config commit.gpgsign false || return 1
  return 0
}
build_fixture || cannot_assess "could not build the scratch fixture repository"

# The fixture's own content: eight-line files, so a staged hunk's line numbers are
# known and a region lease can be made to overlap or to miss on purpose.
printf 'l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n' > "$fixture/leased.md"
printf 'l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n' > "$fixture/free.md"
printf 'l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n' > "$fixture/shared.md"
git -C "$fixture" add -A >/dev/null 2>&1
git -C "$fixture" commit -q -m "fixture seed" >/dev/null 2>&1

hook_dst_dir="$fixture/.git/hooks"
mkdir -p "$hook_dst_dir"
cp "$hook" "$hook_dst_dir/pre-commit"
chmod 0755 "$hook_dst_dir/pre-commit"

seed_sha="$(git -C "$fixture" rev-parse HEAD)"
fixture_hook_sha="$(sha256sum "$hook_dst_dir/pre-commit" | cut -d' ' -f1)"

# ── helpers ──────────────────────────────────────────────────────────────────
plant() {  # plant <issue> <agent> <lane> <files-json>  -> prints PLANT_OK / PLANT_FAIL
  python3 - "$root" "$fixture" "$1" "$2" "$3" "$4" <<'PY'
"""Write one claim event through the repository's OWN writer, then read it back.

The read-back is the control: a fixture whose claim never landed would make every
refusal arm fail, and a fixture that silently landed nothing cannot be told apart
from a hook that refuses nothing unless the claim is proven readable first.
"""
import json
import sys
from pathlib import Path

root, repo, issue, agent, lane, files = sys.argv[1:7]
sys.path.insert(0, str(Path(root) / "governance" / "dispatch"))
sys.path.insert(0, root)
try:
    import claims
    from model import ClaimEvent, parse_file_claims
    from snapshot import now_iso

    ledger = Path(repo) / ".board" / "claims"
    claims.append_event(
        ClaimEvent(
            event="claim",
            issue=int(issue),
            agent=agent,
            at=now_iso(),
            lane=lane,
            files=parse_file_claims(json.loads(files)),
        ),
        ledger,
    )
    read_back = claims.read_ledger(str(ledger))
    mine = [e for e in read_back if e.issue == int(issue) and e.is_claim]
    if not mine:
        print(f"PLANT_FAIL #{issue} was written but does not read back")
        raise SystemExit(1)
    live = claims.active_claims(read_back)
    if int(issue) not in live:
        print(f"PLANT_FAIL #{issue} is not LIVE after the read-back")
        raise SystemExit(1)
    print(f"PLANT_OK #{issue} files={len(mine[0].files)}")
except SystemExit:
    raise
except Exception as exc:  # noqa: BLE001
    print(f"PLANT_FAIL {exc.__class__.__name__}: {exc}")
    raise SystemExit(1)
PY
}

rewrite_line() {  # rewrite_line <file> <1-based-line> <text>
  python3 - "$fixture" "$1" "$2" "$3" <<'PY'
import sys
from pathlib import Path

repo, name, lineno, text = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
path = Path(repo) / name
lines = path.read_text().splitlines()
lines[lineno - 1] = text
path.write_text("\n".join(lines) + "\n")
PY
}

reset_fixture() {  # the seed commit, on the lane branch, with NO ledger and a clean index
  # The branching half is load-bearing, not tidiness: two arms below move HEAD (onto
  # a non-lane branch, then to a detached one), and a fixture left detached makes the
  # hook fail open — so a refusal arm would fail and, worse, an ALLOW arm would PASS
  # for a reason that has nothing to do with the lease.
  rm -rf "$fixture/.board"
  git -C "$fixture" checkout -q -f -B issue-1541 "$seed_sha" >/dev/null 2>&1
  git -C "$fixture" reset -q --hard "$seed_sha" >/dev/null 2>&1
  git -C "$fixture" clean -qfd >/dev/null 2>&1
}

commit_rc=0
commit_out=""
try_commit() {  # try_commit <paths...>  -> sets commit_rc / commit_out
  local path
  for path in "$@"; do git -C "$fixture" add -- "$path" >/dev/null 2>&1; done
  commit_out="$(git -C "$fixture" commit -q -m "gate arm: $*" 2>&1)"
  commit_rc=$?
}

standalone_rc=0
standalone_out=""
try_standalone() {  # try_standalone <cwd>
  standalone_out="$(cd "$1" && bash "$hook" 2>&1)"
  standalone_rc=$?
}

present() { case "$2" in *"$1"*) return 0 ;; *) return 1 ;; esac; }

# ── control: the fixture's reader is live BEFORE anything is judged ──────────
printf 'check-lease-hook: proving the fixture, then the hook\n'
reset_fixture
control="$(plant 9001 gate-other other-lane '[{"path":"leased.md","regions":null}]')"
case "$control" in
  PLANT_OK*)
    # The reader must be reachable FROM THE FIXTURE (not from this checkout): a
    # hook that cannot import there fails open, and every refusal arm would fail
    # for a reason that has nothing to do with the lease logic.
    reader_out="$(cd "$fixture" && python3 -c '
import sys
sys.path.insert(0, "governance/dispatch")
import claims
from model import FileClaim
live = claims.active_claims(claims.read_ledger(".board/claims"))
hit = claims.find_file_conflict((FileClaim(path="leased.md"),), live, 1541, "me")
print("READER_OK" if hit is not None else "READER_FAIL")
' 2>&1)"
    case "$reader_out" in
      READER_OK*)
        ok "control: the fixture's own claim reader resolves a planted lease (plant read back live)"
        fixture_branch="$(git -C "$fixture" symbolic-ref --quiet --short HEAD 2>/dev/null)"
        if [ "$fixture_branch" = "issue-1541" ]; then
          ok "control: the fixture sits on the lane branch issue-1541 (a refusal arm cannot pass or fail because the fixture drifted off a lane branch)"
        else
          bad "control: the fixture is on '${fixture_branch:-detached}' and not issue-1541 — every arm below would judge the wrong code path"
          printf '\ncheck-lease-hook: NOT-OK — the control failed; %s arm(s) not judged\n' "$arms" >&2
          exit "$EXIT_NOT_OK"
        fi
        ;;
      *)
        bad "control: the fixture's reader is NOT live — $reader_out (every arm below would be a hook failing open, so nothing else is judged)"
        printf '\ncheck-lease-hook: NOT-OK — the control failed; %s arm(s) not judged\n' "$arms" >&2
        exit "$EXIT_NOT_OK"
        ;;
    esac
    ;;
  *)
    bad "control: could not plant a claim in the fixture — $control"
    printf '\ncheck-lease-hook: NOT-OK — the control failed; %s arm(s) not judged\n' "$arms" >&2
    exit "$EXIT_NOT_OK"
    ;;
esac

# ── A. the hook, observed through REAL commits ───────────────────────────────
reset_fixture
plant 9001 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
try_commit leased.md
if [ "$commit_rc" -ne 0 ] \
   && present "REFUSED" "$commit_out" \
   && present "#9001" "$commit_out" \
   && present "gate-other" "$commit_out" \
   && present "--no-verify" "$commit_out" \
   && present "AO_LEASE_HOOK_OVERRIDE" "$commit_out"; then
  ok "a file leased whole to another live claim is REFUSED, naming #9001 and both opt-outs (rc=$commit_rc)"
else
  bad "expected a refusal naming #9001 + the opt-outs; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9001 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line free.md 1 "l1 mine"
try_commit free.md
if [ "$commit_rc" -eq 0 ] && ! present "REFUSED" "$commit_out"; then
  ok "negative control: an unleased file commits normally (rc=0)"
else
  bad "expected an allowed commit for an unleased file; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 1541 gate-me my-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
try_commit leased.md
if [ "$commit_rc" -eq 0 ]; then
  ok "negative control: a file leased to THIS branch's own live claim commits normally (rc=0)"
else
  bad "expected an allowed commit for the branch's own lease; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9002 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
try_commit leased.md
if [ "$commit_rc" -ne 0 ] && present "#9002" "$commit_out" && present "no live claim (issue #1541)" "$commit_out"; then
  ok "an UNCLAIMED session is still refused on a lease a live claim holds, naming #9002 (rc=$commit_rc)"
else
  bad "expected a refusal naming #9002 for an unclaimed session; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
rewrite_line leased.md 1 "l1 mine"
try_commit leased.md
if [ "$commit_rc" -eq 0 ]; then
  ok "fail-open: with no ledger and no live claims at all, the commit is allowed (rc=0)"
else
  bad "expected a fail-open allow with an empty ledger; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9003 gate-other other-lane '[{"path":"shared.md","regions":[[1,3]]}]' >/dev/null
rewrite_line shared.md 7 "l7 mine"
try_commit shared.md
if [ "$commit_rc" -eq 0 ]; then
  ok "a region-DISJOINT lease (held 1-3, staged hunk at line 7) commits normally (rc=0)"
else
  bad "expected an allowed commit for a region-disjoint lease; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9003 gate-other other-lane '[{"path":"shared.md","regions":[[1,3]]}]' >/dev/null
rewrite_line shared.md 2 "l2 mine"
try_commit shared.md
if [ "$commit_rc" -ne 0 ] && present "#9003" "$commit_out" && present "held: 1-3" "$commit_out"; then
  ok "a region-OVERLAPPING lease (held 1-3, staged hunk at line 2) is refused, naming the held regions (rc=$commit_rc)"
else
  bad "expected a refusal naming #9003 and held: 1-3; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9004 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
git -C "$fixture" rm -q -- leased.md
commit_out="$(git -C "$fixture" commit -q -m "gate arm: delete a leased file" 2>&1)"
commit_rc=$?
if [ "$commit_rc" -ne 0 ] && present "#9004" "$commit_out"; then
  ok "DELETING a file leased to another live claim is refused (the whole-file fallback, rc=$commit_rc)"
else
  bad "expected a refusal for a staged deletion of a leased file; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9005 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
git -C "$fixture" add -- leased.md >/dev/null 2>&1
commit_out="$(AO_LEASE_HOOK_OVERRIDE="cross-lane fix authorised by the operator" git -C "$fixture" commit -q -m "gate arm: override" 2>&1)"
commit_rc=$?
if [ "$commit_rc" -eq 0 ] && present "OVERRIDDEN" "$commit_out" && present "cross-lane fix authorised by the operator" "$commit_out"; then
  ok "the documented override allows the commit AND announces its justification (rc=0)"
else
  bad "expected the override to allow the commit and print the justification; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9006 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
git -C "$fixture" add -- leased.md >/dev/null 2>&1
try_standalone "$fixture"
if [ "$standalone_rc" -eq 1 ] && present "REFUSED" "$standalone_out"; then
  ok "standalone 'bash scripts/git-hooks/pre-commit' against a conflicting stage exits 1 (the issue's third acceptance check)"
else
  bad "expected standalone rc=1; got rc=$standalone_rc out=$(printf '%s' "$standalone_out" | head -3 | tr '\n' ' ')"
fi

mkdir -p "$fixture/nested/deeper"
try_standalone "$fixture/nested/deeper"
if [ "$standalone_rc" -eq 1 ] && present "#9006" "$standalone_out"; then
  ok "the same refusal holds when git runs the hook from a SUBDIRECTORY (cwd-independent resolution, rc=1)"
else
  bad "expected rc=1 from a subdirectory; got rc=$standalone_rc out=$(printf '%s' "$standalone_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9007 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
git -C "$fixture" add -- leased.md >/dev/null 2>&1
git -C "$fixture" symbolic-ref HEAD refs/heads/master
commit_out="$(git -C "$fixture" commit -q -m "gate arm: non-lane branch" 2>&1)"
commit_rc=$?
if [ "$commit_rc" -eq 0 ] && present "is not a lane branch" "$commit_out"; then
  ok "fail-open: a branch that is not a lane branch (issue-<n>) is allowed and says why (rc=0)"
else
  bad "expected a fail-open allow on a non-lane branch; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

reset_fixture
plant 9008 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
rewrite_line leased.md 1 "l1 mine"
git -C "$fixture" add -- leased.md >/dev/null 2>&1
git -C "$fixture" checkout -q --detach >/dev/null 2>&1
commit_out="$(git -C "$fixture" commit -q -m "gate arm: detached HEAD" 2>&1)"
commit_rc=$?
if [ "$commit_rc" -eq 0 ] && present "detached HEAD" "$commit_out"; then
  ok "fail-open: a detached HEAD is allowed and says why (rc=0)"
else
  bad "expected a fail-open allow on a detached HEAD; rc=$commit_rc out=$(printf '%s' "$commit_out" | head -3 | tr '\n' ' ')"
fi

# ── B. the mutants: each half of the decision is load-bearing ────────────────
# A mutant is built from the SHIPPED hook, is proven to differ from it by sha256,
# is proven to contain the mutation, and is installed where the arm it must change
# actually runs. A mutant that never landed would look like a passing control.
mutate() {  # mutate <old> <new> <destination> -> 0 when the mutation landed
  python3 - "$root/scripts/git-hooks/pre-commit" "$1" "$2" "$3" <<'PY'
import hashlib
import sys
from pathlib import Path

source, old, new, dest = sys.argv[1:5]
text = Path(source).read_text()
if text.count(old) != 1:
    print(f"MUTATE_FAIL the anchor appears {text.count(old)} times: {old!r}")
    raise SystemExit(1)
if old == new:
    print("MUTATE_FAIL the mutation is a no-op")
    raise SystemExit(1)
mutant = text.replace(old, new)
Path(dest).write_text(mutant)
before = hashlib.sha256(text.encode()).hexdigest()
after = hashlib.sha256(mutant.encode()).hexdigest()
if before == after:
    print("MUTATE_FAIL the mutant is byte-identical to the shipped hook")
    raise SystemExit(1)
if new not in mutant:
    print("MUTATE_FAIL the mutation text is not in the mutant")
    raise SystemExit(1)
print(f"MUTATE_OK sha_before={before[:12]} sha_after={after[:12]} bytes={len(text)}->{len(mutant)}")
PY
}

mutant_dir="$scratch/mutants"
mkdir -p "$mutant_dir"

# M1 — the conflict branch neutered. The SAME fixture state must be refused by the
# shipped hook and allowed by the mutant: an A/B in one state cannot pass because
# the fixture drifted, and it cannot pass for the wrong reason either.
m1="$mutant_dir/m1-pre-commit"
msha="$(mutate 'if hit is not None:' 'if False:' "$m1")"
case "$msha" in
  MUTATE_OK*)
    reset_fixture
    plant 9001 gate-other other-lane '[{"path":"leased.md","regions":null}]' >/dev/null
    rewrite_line leased.md 1 "l1 mine"
    try_commit leased.md
    shipped_m1_rc="$commit_rc"
    rewrite_line leased.md 2 "l2 mine"
    cp "$m1" "$hook_dst_dir/pre-commit"; chmod 0755 "$hook_dst_dir/pre-commit"
    try_commit leased.md
    if [ "$shipped_m1_rc" -ne 0 ] && [ "$commit_rc" -eq 0 ] && ! present "REFUSED" "$commit_out"; then
      ok "MUTANT M1 (conflict branch neutered): the SAME fixture is refused by the shipped hook (rc=$shipped_m1_rc) and allowed by the mutant (rc=0) — the refusal is the predicate ($msha)"
    else
      bad "MUTANT M1 differential did not hold (shipped rc=$shipped_m1_rc, mutant rc=$commit_rc): the refusal does not come from the rule under test"
    fi
    cp "$hook" "$hook_dst_dir/pre-commit"; chmod 0755 "$hook_dst_dir/pre-commit"
    ;;
  *) bad "MUTANT M1 could not be built: $msha" ;;
esac

# M2 — the region set forced to whole-file. The SAME fixture state must be ALLOWED
# by the shipped hook and refused by the mutant, or the region comparison is inert
# and the conservative-fallback claim would be an assertion rather than a
# measurement.
m2="$mutant_dir/m2-pre-commit"
msha="$(mutate '    return tuple(regions)' '    return None' "$m2")"
case "$msha" in
  MUTATE_OK*)
    reset_fixture
    plant 9003 gate-other other-lane '[{"path":"shared.md","regions":[[1,3]]}]' >/dev/null
    rewrite_line shared.md 7 "l7 mine"
    try_commit shared.md
    shipped_m2_rc="$commit_rc"
    rewrite_line shared.md 7 "l7 mine again"
    cp "$m2" "$hook_dst_dir/pre-commit"; chmod 0755 "$hook_dst_dir/pre-commit"
    try_commit shared.md
    if [ "$shipped_m2_rc" -eq 0 ] && [ "$commit_rc" -ne 0 ] && present "REFUSED" "$commit_out"; then
      ok "MUTANT M2 (regions forced whole-file): the SAME region-disjoint fixture is allowed by the shipped hook (rc=0) and refused by the mutant (rc=$commit_rc) — the region comparison is load-bearing ($msha)"
    else
      bad "MUTANT M2 differential did not hold (shipped rc=$shipped_m2_rc, mutant rc=$commit_rc): the region comparison is not what decides"
    fi
    cp "$hook" "$hook_dst_dir/pre-commit"; chmod 0755 "$hook_dst_dir/pre-commit"
    ;;
  *) bad "MUTANT M2 could not be built: $msha" ;;
esac

installed_sha="$(sha256sum "$hook_dst_dir/pre-commit" | cut -d' ' -f1)"
if [ "$installed_sha" = "$fixture_hook_sha" ]; then
  ok "the fixture's installed hook is restored to the shipped file after the mutants (sha256 match)"
else
  bad "the fixture's hook was left as a mutant (sha $installed_sha)"
fi

# ── C. the opt-in install path, and that it is NOT a side effect ─────────────
common="$(git rev-parse --git-common-dir 2>/dev/null)" || common=".git"
common_hooks="$common/hooks"
hooks_before=""
[ -d "$common_hooks" ] && hooks_before="$(cd "$common_hooks" && find . -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum 2>/dev/null)"

install_dest="$scratch/install-dest"
dry_out="$(make -C "$root" install-hooks HOOKS_DEST="$install_dest" 2>&1)"
if [ ! -e "$install_dest/pre-commit" ] && present "DRY RUN" "$dry_out" && present "AO_HOOKS_APPLY=1" "$dry_out"; then
  ok "'make install-hooks' is a DRY RUN by default: nothing written, and the opt-in command is printed"
else
  bad "'make install-hooks' without the opt-in was not a pure dry run (exists=$([ -e "$install_dest/pre-commit" ] && echo yes || echo no)) out=$(printf '%s' "$dry_out" | tail -3 | tr '\n' ' ')"
fi

apply_out="$(AO_HOOKS_APPLY=1 make -C "$root" install-hooks HOOKS_DEST="$install_dest" 2>&1)"
src_sha="$(sha256sum "$hook" | cut -d' ' -f1)"
got_sha="$(sha256sum "$install_dest/pre-commit" 2>/dev/null | cut -d' ' -f1)"
if [ "$src_sha" = "$got_sha" ] && [ -x "$install_dest/pre-commit" ]; then
  ok "AO_HOOKS_APPLY=1 installs a byte-identical, executable hook (sha256 ${src_sha:0:12})"
else
  bad "the applied install is not a byte-identical executable copy (src=${src_sha:0:12} dst=${got_sha:0:12})"
fi

foreign_dest="$scratch/foreign-dest"
mkdir -p "$foreign_dest"
printf '#!/bin/sh\necho a hook this repo does not own\n' > "$foreign_dest/pre-commit"
foreign_sha="$(sha256sum "$foreign_dest/pre-commit" | cut -d' ' -f1)"
foreign_out="$(AO_HOOKS_APPLY=1 make -C "$root" install-hooks HOOKS_DEST="$foreign_dest" 2>&1)"
foreign_after="$(sha256sum "$foreign_dest/pre-commit" | cut -d' ' -f1)"
if present "REFUSED" "$foreign_out" && [ "$foreign_sha" = "$foreign_after" ]; then
  ok "a FOREIGN pre-commit hook is refused by name and left byte-identical (never clobbered)"
else
  bad "a foreign hook was not refused intact (out=$(printf '%s' "$foreign_out" | tail -2 | tr '\n' ' '))"
fi

force_out="$(AO_HOOKS_APPLY=1 AO_HOOKS_FORCE=1 make -C "$root" install-hooks HOOKS_DEST="$foreign_dest" 2>&1)"
force_sha="$(sha256sum "$foreign_dest/pre-commit" | cut -d' ' -f1)"
if [ "$force_sha" = "$src_sha" ]; then
  ok "AO_HOOKS_FORCE=1 is the one documented way past the refusal (dest now matches the source)"
else
  bad "AO_HOOKS_FORCE=1 did not install (sha ${force_sha:0:12})"
fi

hooks_after=""
[ -d "$common_hooks" ] && hooks_after="$(cd "$common_hooks" && find . -maxdepth 1 -type f -print0 | sort -z | xargs -0 -r sha256sum 2>/dev/null)"
if [ "$hooks_before" = "$hooks_after" ]; then
  ok "this gate installed nothing into the checkout's own COMMON hooks dir ($common_hooks) — the operator opts in"
else
  bad "the checkout's own hooks dir CHANGED while the gate ran: before=$(printf '%s' "$hooks_before" | tr '\n' ' ') after=$(printf '%s' "$hooks_after" | tr '\n' ' ')"
fi

# ── D. the hook's own static contract ───────────────────────────────────────
# The hook has no .sh suffix — git insists on the name — so the repo-wide shell
# scans (`find . -name '*.sh'`, `git ls-files -- '*.sh'`) cannot see it. These two
# arms are what closes that coverage gap, and they are the reason both are named
# here rather than left to the tree scans.
if bash -n "$hook" >/dev/null 2>&1; then
  ok "bash -n is clean on the hook (the shell-syntax gate scans only *.sh, so it cannot cover this file)"
else
  bad "bash -n FAILED on the hook: $(bash -n "$hook" 2>&1 | head -3 | tr '\n' ' ')"
fi

if bash scripts/check-shell-patterns.sh --files scripts/git-hooks/pre-commit >"$scratch/patterns.txt" 2>&1; then
  ok "the shell-pattern doctrine holds for the hook ($(tail -1 "$scratch/patterns.txt"))"
else
  bad "check-shell-patterns refuses the hook: $(tail -3 "$scratch/patterns.txt" | tr '\n' ' ')"
fi

if grep -qE "$marker_re" "$hook"; then
  bad "the hook carries an unfinished marker: $(grep -nE "$marker_re" "$hook" | head -2 | tr '\n' ' ')"
else
  ok "no unfinished markers in the hook (the docs gate scans *.sh/py/go, so it cannot cover this file either)"
fi

if [ -f "$doc" ] && grep -q -- '--no-verify' "$doc" && grep -q 'AO_LEASE_HOOK_OVERRIDE' "$doc" && grep -q 'make install-hooks' "$doc"; then
  ok "docs/LEASE-HOOK.md exists and names both opt-outs and the install target (the pointer the hook prints resolves)"
else
  bad "docs/LEASE-HOOK.md is missing or does not name the opt-out path the hook prints"
fi

# ── summary ─────────────────────────────────────────────────────────────────
printf '\n'
if [ "$fails" -ne 0 ]; then
  printf 'check-lease-hook: NOT-OK — %s of %s arm(s) failed (a hook that refuses the wrong thing, or fails to refuse the right one, blocks work or leaves the lease unenforced)\n' \
    "$fails" "$arms" >&2
  exit "$EXIT_NOT_OK"
fi
printf 'check-lease-hook: OK — %s arm(s) observed: the refusal, the four negative controls, the fail-open arms, two mutant differentials, the opt-in install path, and the hook static contract\n' "$arms"
exit "$EXIT_OK"
