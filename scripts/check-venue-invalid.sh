#!/usr/bin/env bash
# check-venue-invalid.sh -- the control for scripts/verify.sh's venue-invalid verdict (issue #1368).
#
# THE CLASS THIS EXISTS FOR
#   A venue whose git admin directory has been reclaimed survives as a directory
#   whose `.git` FILE names an admin dir that no longer exists. Measured while
#   closing #1345: the composite ran inside such a venue and published
#   `verify: FAIL (5 of 203 checks failed, 20 skipped: fleet-runbook, reconcile,
#   ...)` -- twenty checks each noticing "not a repository" in their own words,
#   counted in the skip bucket, reading like a small, believable failure instead
#   of "this venue is not a checkout, so NOTHING in it was assessed" (#1351).
#   `scripts/verify.sh` now decides that condition ONCE, before the check loop,
#   and publishes ONE `verify: CANNOT-ASSESS -- venue-invalid: ...` line plus a
#   `venue_invalid` record in `.verify/attestation.json` (rc 2, `check_count` 0,
#   `checks: []`, `skipped` 0). That behaviour is real; a REGRESSION in it would
#   have been caught by NOTHING -- measured on the merged #1359 head and filed
#   as #1368: outside the implementation and the reaper, no file under scripts/
#   or governance/ mentions `venue-invalid`, and #1351's acceptance evidence for
#   the provocation was a /tmp driver, not a durable control.
#
# WHAT IS PROVEN HERE (six arms, every one driving the REAL scripts/verify.sh)
#   1. the DESTROYED venue (a `.git` FILE naming a missing admin dir) -> rc 2,
#      EXACTLY ONE `venue-invalid` line naming the venue AND the missing admin
#      dir, `check_count` 0 / `checks: []` / `skipped` 0 / `venue_invalid` in
#      the record it writes, no verdict line, and no skip bucket at all (the
#      venue record carries no `skip_ratchet` key);
#   2. an INTACT venue -- the same `.git` FILE, its admin dir present and
#      holding a commit -- keeps the probe SILENT (no `venue_invalid` key, the
#      normal verdict). Without this arm a probe that fired on every linked
#      worktree would still pass arm 1;
#   3. a GITLESS venue (the same surface exported with `git archive HEAD` and
#      untarred) keeps the probe silent. The specificity is load-bearing: the
#      condition is "declares a linkage it cannot honour", NOT "has no `.git`" --
#      `scripts/check-skip-ratchet.sh` mounts exactly this gitless shape and
#      expects `verify: PASS`, so a bare "not a work tree" rule would red it;
#   4. MUTANT -- the venue block deleted from a scratch COPY of verify.sh, run
#      in the SAME destroyed venue: the run folds to `verify: PASS (2 of 2
#      checks)` (green inside a venue that is not a checkout) and arm 1's
#      assertions RED BY NAME against it;
#   5. MUTANT -- the venue block deleted, the two checks answering rc 2 and a
#      `venue` budget naming them: the old fold-in reappears AS A GREEN
#      `verify: PASS (0 of 2 checks, 2 skipped: ...)` -- the #1345 shape the
#      venue verdict replaced -- and arm 1's assertions RED BY NAME again;
#   6. MUTANT -- only the `.git`-FILE branch stripped: the run still refuses
#      (rc 2, one line) but the line NO LONGER NAMES the missing admin dir, so
#      the naming assertions RED BY NAME -- the narrow regression the issue
#      names, caught one layer deeper than arm 4.
#
# WHY A NEW CHECK, not one more end state in `scripts/check-skip-ratchet.sh`
# (#1368's other option): the venue verdict EXITS BEFORE the skip ratchet, so
# such an arm never reaches the mechanism whose gate that file is; its shim is
# gitless BY CONSTRUCTION and its six scenarios assert skip accounting, while
# the arms here need three venue SHAPES (broken / intact / gitless) plus mutants
# of the probe itself. One file per mechanism-under-test also keeps a failure
# attributable: `scripts/check-prune-worktrees.sh` owns the REAPER's
# `venue-invalid` naming (the #1345 half); this owns verify.sh's own verdict
# (the #1359 half).
#
# Every run happens in a scratch venue under this gate's own /tmp work dir:
# byte-identical copies of this tree's orchestrator surface, two fixture checks
# (the explicit `checks=()` array is neutered so ONLY they run), and a PRIVATE
# gate-permit store (`AO_GATE_LOCK_ROOT`) -- a control, not a competing gate;
# the box-wide cap is proved by `scripts/check-gate-lock.sh`.
#
# This check is auto-discovered by `scripts/discover-checks.sh` (a new
# `scripts/check-*.sh` is wired the moment it lands), so it needs no hand-edit to
# `scripts/verify.sh`'s array.
#
# Usage:
#   bash scripts/check-venue-invalid.sh
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage: bash scripts/check-venue-invalid.sh
The control for scripts/verify.sh's venue-invalid verdict (issue #1368): six arms,
each driving the REAL composite in a scratch venue with a private gate-permit store.
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

case "${1:-}" in
  --help|-h) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

if [ ! -f "$root/scripts/verify.sh" ]; then
  echo "check-venue-invalid: CANNOT-ASSESS -- $root/scripts/verify.sh is missing (nothing to assert against)" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "check-venue-invalid: CANNOT-ASSESS -- python3 is not available, so the control cannot run" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-venue-invalid: CANNOT-ASSESS -- git is not available, so the venue shapes cannot be built" >&2
  exit 2
fi

# All scratch lives OUTSIDE the tree (this gate must leave the tree byte-clean)
# and carries an explicit /tmp/<name>. template: mktemp's own default lands in
# the shared, periodically-cleaned TMPDIR and can vanish mid-run (SP-9). The
# X-run is assembled by printf so no literal marker token sits in this file.
work="$(mktemp -d "/tmp/ao1368-venue.$(printf 'X%.0s' 1 2 3 4 5 6)")" \
  || { echo "check-venue-invalid: CANNOT-ASSESS -- could not create a scratch directory under /tmp" >&2; exit 2; }
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

arms_tsv="$work/arms.tsv"
python3 - "$root" "$work" "$arms_tsv" > "$work/arms.txt" 2>&1 <<'PY'
"""Drive the REAL scripts/verify.sh in scratch venues and assert the venue-invalid
contract (issue #1368) -- plus the mutants that must RED it by name.

Every venue is built under this gate's own work dir: byte-identical copies of the
tree's orchestrator surface, two fixture checks (the explicit `checks=()` array is
neutered so ONLY they run), and a private gate-permit store. Nothing here touches
the lane's checkout, another lane's worktree, or the box's permit pool.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
arms_path = Path(sys.argv[3])

ORCHESTRATOR = (
    "scripts/verify.sh",
    "scripts/gate-lock.sh",
    "scripts/discover-checks.sh",
    "scripts/lib/skip-ratchet.py",
    "scripts/lib/validate-attestation.py",
    "governance/isolation/attestation.schema.json",
    "fleet/gatelock.py",
    "fleet/lease.py",
)
FIXTURE_A = "venue-fixture-a"
FIXTURE_B = "venue-fixture-b"
CHECK_TOTAL = 2

ARMS = []


def cannot_assess(message):
    print("check-venue-invalid: CANNOT-ASSESS -- %s" % message, file=sys.stderr)
    raise SystemExit(2)


def arm(label, ok, detail, evidence=()):
    ARMS.append((label, 0 if ok else 1, " ".join(str(detail).split())))
    print("arm %d -- %s" % (len(ARMS), label))
    for line in evidence:
        print("    %s" % str(line).replace("\n", " ")[:400])


def staged(what, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except SystemExit:
        raise
    except Exception as exc:  # staging, not a verdict: never a silent pass
        cannot_assess("%s: %s" % (what, exc))


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, timeout=120
    )


def copy_surface(venue):
    for relative in ORCHESTRATOR:
        source = root / relative
        if not source.is_file():
            cannot_assess("%s is missing from this tree" % relative)
        target = venue / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def neuter(venue):
    verify = venue / "scripts" / "verify.sh"
    text = verify.read_text(encoding="utf-8")
    try:
        start = text.index("\nchecks=(\n")
        end = text.index("\n)\n", start)
    except ValueError:
        cannot_assess("the `checks=()` neuter anchor moved in scripts/verify.sh")
    verify.write_text(text[:start] + "\nchecks=()\n" + text[end + 3:], encoding="utf-8")


def fixture_checks(venue, rcs):
    for name, rc in rcs:
        (venue / "scripts" / ("check-%s.sh" % name)).write_text(
            "#!/usr/bin/env bash\nset -u\necho 'check-%s: fixture, rc %d'\nexit %d\n"
            % (name, rc, rc),
            encoding="utf-8",
        )


def write_budget(venue, entries):
    (venue / "scripts" / "skip-budget.json").write_text(
        json.dumps({"schema": "ao.verify.skip-budget/v1", "entries": entries}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def mount(venue, rcs, budget=()):
    shutil.rmtree(venue, ignore_errors=True)
    venue.mkdir(parents=True, exist_ok=True)
    copy_surface(venue)
    neuter(venue)
    fixture_checks(venue, rcs)
    write_budget(venue, list(budget))
    return venue


def link_venue(venue, admin_name):
    """Give the venue a `.git` FILE (the linked-worktree shape) naming a real
    admin dir. Returns (admin dir, the gitdir target the probe will read)."""
    admin = venue.parent / admin_name
    shutil.rmtree(admin, ignore_errors=True)
    proc = git("init", "-q", "--separate-git-dir=%s" % admin, str(venue))
    if proc.returncode != 0:
        cannot_assess("git init failed in %s: %s" % (venue, proc.stderr.strip()))
    content = (venue / ".git").read_text(encoding="utf-8")
    targets = [
        line.split("gitdir:", 1)[1].strip()
        for line in content.splitlines()
        if line.startswith("gitdir:")
    ]
    if len(targets) != 1:
        cannot_assess("the venue's .git file carries no single gitdir line")
    target = targets[0]
    if not os.path.isabs(target):
        target = str(venue / target)
    return admin, target


def destroy_venue(venue, admin):
    shutil.rmtree(admin, ignore_errors=True)
    proc = git("-C", str(venue), "rev-parse", "HEAD")
    if proc.returncode == 0:
        cannot_assess("the venue is still a usable git checkout (the destruction did not take)")


def commit_fixture(venue):
    proc = git(
        "-C", str(venue),
        "-c", "user.name=ao-fixture",
        "-c", "user.email=fixture@ao.invalid",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "--allow-empty", "-m", "fixture",
    )
    if proc.returncode != 0:
        cannot_assess("the intact venue could not take its fixture commit: %s" % proc.stderr.strip())


def export_venue(venue):
    """The gitless shape: `git archive HEAD | tar -x` of the same surface."""
    venue.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "-C", str(root), "archive", "HEAD", "--", *ORCHESTRATOR],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        cannot_assess("git archive HEAD failed: %s" % proc.stderr.decode(errors="replace").strip())
    untar = subprocess.run(
        ["tar", "-x", "-C", str(venue)], input=proc.stdout, capture_output=True, timeout=120
    )
    if untar.returncode != 0:
        cannot_assess("tar -x failed: %s" % untar.stderr.decode(errors="replace").strip())
    if (venue / ".git").exists() or (venue / ".git").is_symlink():
        cannot_assess("the exported venue carries a .git (it must be gitless)")
    return venue


def run(venue):
    env = dict(os.environ)
    env["AO_GATE_LOCK_ROOT"] = str(work / "permits")
    env["AO_GATE_MAX_CONCURRENT"] = "1"
    env["AO_AGENT_ID"] = "venue-invalid-fixture"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            ["bash", "scripts/verify.sh", "verify"],
            cwd=str(venue), capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        cannot_assess("the composite did not finish within 300s in %s" % venue)
    text = proc.stdout + proc.stderr
    attestation = None
    path = venue / ".verify" / "attestation.json"
    if path.is_file():
        try:
            attestation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            cannot_assess("the run's attestation is unreadable (%s)" % exc)
    return proc.returncode, text, attestation


def venue_lines(text):
    return [line for line in text.splitlines() if "venue-invalid" in line]


def run_report(rc, text, attestation):
    out = ["rc=%d" % rc]
    lines = venue_lines(text)
    verdict = [
        line
        for line in text.splitlines()
        if line.startswith("verify: PASS") or line.startswith("verify: FAIL")
    ]
    if lines:
        out.append("venue line: %s" % lines[0])
    if verdict:
        out.append("verdict: %s" % verdict[0])
    if isinstance(attestation, dict):
        node = attestation.get("venue_invalid")
        out.append(
            "record: check_count=%r skipped=%r skip_ratchet=%s venue_invalid=%s"
            % (
                attestation.get("check_count"),
                attestation.get("skipped"),
                "yes" if "skip_ratchet" in attestation else "no",
                json.dumps(node, sort_keys=True) if node is not None else None,
            )
        )
    else:
        out.append("record: (none written)")
    return out


def venue_shape_problems(rc, text, attestation, venue, admin):
    """Arm 1's whole contract, as a list of named problems (empty == held)."""
    problems = []
    if rc != 2:
        problems.append("rc %r != 2 (the refusal must be CANNOT-ASSESS)" % rc)
    lines = venue_lines(text)
    if len(lines) != 1:
        problems.append("%d line(s) name venue-invalid, wanted exactly 1" % len(lines))
    else:
        line = lines[0]
        if str(venue) not in line:
            problems.append("the venue-invalid line does not name the venue")
        if admin not in line:
            problems.append("the venue-invalid line does not name the missing admin dir %s" % admin)
        if "2 of 2 checks were not run" not in line:
            problems.append("the venue-invalid line does not count the checks that did not run")
    for line in text.splitlines():
        if line.startswith("verify: PASS") or line.startswith("verify: FAIL"):
            problems.append("the run published a verdict line (%s)" % line[:60])
            break
    if not isinstance(attestation, dict):
        problems.append("no attestation record was written")
        return problems
    if attestation.get("result") != "CANNOT-ASSESS":
        problems.append("attestation result is %r, wanted CANNOT-ASSESS" % attestation.get("result"))
    if attestation.get("exit_code") != 2:
        problems.append("attestation exit_code is %r, wanted 2" % attestation.get("exit_code"))
    if attestation.get("check_count") != 0:
        problems.append("attestation check_count is %r, wanted 0" % attestation.get("check_count"))
    if attestation.get("checks") != []:
        problems.append("attestation checks is %r, wanted []" % attestation.get("checks"))
    if attestation.get("skipped") != 0:
        problems.append("attestation skipped is %r, wanted 0 (no skip bucket)" % attestation.get("skipped"))
    if "skip_ratchet" in attestation:
        problems.append("attestation carries a skip_ratchet record; the venue path runs before the ratchet")
    node = attestation.get("venue_invalid")
    if not isinstance(node, dict):
        problems.append("attestation has no venue_invalid record (%r)" % node)
    else:
        if node.get("finding") != "venue-invalid":
            problems.append("venue_invalid.finding is %r" % node.get("finding"))
        if node.get("root") != str(venue):
            problems.append("venue_invalid.root is %r, wanted the venue" % node.get("root"))
        if node.get("gitdir") != admin:
            problems.append(
                "venue_invalid.gitdir is %r, wanted the missing admin dir %s" % (node.get("gitdir"), admin)
            )
        if node.get("checks_not_run") != CHECK_TOTAL or node.get("checks_total") != CHECK_TOTAL:
            problems.append(
                "venue_invalid counts %r of %r, wanted %d of %d"
                % (node.get("checks_not_run"), node.get("checks_total"), CHECK_TOTAL, CHECK_TOTAL)
            )
    return problems


class AnchorMoved(Exception):
    """The mutation's anchor is not where this control expects it."""


def read_verify(venue):
    return (venue / "scripts" / "verify.sh").read_text(encoding="utf-8")


def write_verify(venue, text):
    (venue / "scripts" / "verify.sh").write_text(text, encoding="utf-8")


def syntax_ok(venue):
    proc = subprocess.run(
        ["bash", "-n", str(venue / "scripts" / "verify.sh")],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode == 0, proc.stderr.strip()


def mutant_remove_venue_block(venue):
    """Delete the whole venue block from the MOUNTED copy (never the source)."""
    text = read_verify(venue)
    start_marker = "# --- venue precondition (issue #1351)"
    end_marker = '\ncheck_out_dir="$verify_dir/.check-out"\n'
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise AnchorMoved(
            "the venue-block anchors moved (markers matched %d and %d, wanted 1 each)"
            % (text.count(start_marker), text.count(end_marker))
        )
    start = text.index(start_marker)
    end = text.index(end_marker)
    mutated = text[:start] + text[end + 1:]
    if "venue_declares_linkage" in mutated or len(mutated) >= len(text):
        raise AnchorMoved("the venue-block deletion did not remove the block")
    write_verify(venue, mutated)
    return len(text) - len(mutated)


def mutant_strip_gitdir_branch(venue):
    """Delete only the `.git`-FILE branch, keeping the rest of the probe."""
    text = read_verify(venue)
    start_marker = '  elif [ ! -d "$root/.git" ]; then\n'
    end_marker = '  fi\n  venue_sha="$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$root" rev-parse HEAD 2>/dev/null || true)"\n'
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise AnchorMoved(
            "the .git-FILE branch anchors moved (markers matched %d and %d, wanted 1 each)"
            % (text.count(start_marker), text.count(end_marker))
        )
    start = text.index(start_marker)
    end = text.index(end_marker)
    mutated = text[:start] + "  fi\n" + text[end + len("  fi\n"):]
    if "missing admin dir" not in text or "missing admin dir" in mutated or len(mutated) >= len(text):
        raise AnchorMoved("the .git-FILE branch was not removed")
    write_verify(venue, mutated)
    return len(text) - len(mutated)


print("== the venue shapes (every run: the REAL scripts/verify.sh, a scratch venue, a private permit store) ==")

# --- arm 1: the destroyed venue ----------------------------------------------
venue = staged("the destroyed venue", mount, work / "venues" / "broken", ((FIXTURE_A, 0), (FIXTURE_B, 0)))
admin, target = staged("the destroyed venue's git linkage", link_venue, venue, "broken-admin")
staged("the destruction", destroy_venue, venue, admin)
rc, text, attestation = staged("the destroyed venue's run", run, venue)
problems = venue_shape_problems(rc, text, attestation, venue, target)
arm(
    "a destroyed venue is ONE venue-invalid finding naming the venue and its missing admin dir "
    "(rc 2, nothing run, no skip bucket)",
    not problems,
    problems[0] if problems else "rc 2, exactly one venue-invalid line, check_count=0, skipped=0, no skip_ratchet",
    run_report(rc, text, attestation) + (["problems: %s" % "; ".join(problems[:4])] if problems else []),
)

# --- arm 2: an intact venue --------------------------------------------------
venue = staged("the intact venue", mount, work / "venues" / "intact", ((FIXTURE_A, 0), (FIXTURE_B, 0)))
admin, target = staged("the intact venue's git linkage", link_venue, venue, "intact-admin")
staged("the intact venue's commit", commit_fixture, venue)
rc, text, attestation = staged("the intact venue's run", run, venue)
problems = []
if rc != 0:
    problems.append("rc %r != 0" % rc)
if "verify: PASS (2 of 2 checks)" not in text:
    problems.append("no 'verify: PASS (2 of 2 checks)' verdict line")
if "venue-invalid" in text:
    problems.append("the probe fired inside a venue whose linkage it CAN honour")
att = attestation if isinstance(attestation, dict) else {}
if "venue_invalid" in att:
    problems.append("attestation carries a venue_invalid key")
if att.get("check_count") != CHECK_TOTAL:
    problems.append("check_count %r != %d" % (att.get("check_count"), CHECK_TOTAL))
if att.get("skipped") != 0:
    problems.append("skipped %r != 0" % att.get("skipped"))
if att.get("result") != "PASS":
    problems.append("result %r != PASS" % att.get("result"))
arm(
    "an INTACT venue (the same .git FILE, admin dir present) keeps the probe silent with a normal verdict",
    not problems,
    problems[0] if problems else "rc 0, verify: PASS (2 of 2 checks), no venue_invalid key",
    run_report(rc, text, attestation) + (["problems: %s" % "; ".join(problems[:4])] if problems else []),
)

# --- arm 3: a gitless venue --------------------------------------------------
venue = work / "venues" / "gitless"
staged("the exported venue", export_venue, venue)
staged("the exported venue's surface", neuter, venue)
staged("the exported venue's fixtures", fixture_checks, venue, ((FIXTURE_A, 0), (FIXTURE_B, 0)))
staged("the exported venue's budget", write_budget, venue, ())
rc, text, attestation = staged("the exported venue's run", run, venue)
problems = []
if rc != 0:
    problems.append("rc %r != 0" % rc)
if "verify: PASS (2 of 2 checks)" not in text:
    problems.append("no 'verify: PASS (2 of 2 checks)' verdict line")
if "venue-invalid" in text:
    problems.append("the probe fired in a venue that never declared a git linkage")
att = attestation if isinstance(attestation, dict) else {}
if "venue_invalid" in att:
    problems.append("attestation carries a venue_invalid key")
if att.get("check_count") != CHECK_TOTAL:
    problems.append("check_count %r != %d" % (att.get("check_count"), CHECK_TOTAL))
arm(
    "a GITLESS venue (exported with git archive) keeps the probe silent",
    not problems,
    problems[0] if problems else "rc 0, verify: PASS (2 of 2 checks), no venue-invalid line, no venue_invalid key",
    run_report(rc, text, attestation) + (["problems: %s" % "; ".join(problems[:4])] if problems else []),
)

# --- arm 4: mutant, venue block deleted, both checks assess ------------------
venue = staged("the mutant's venue", mount, work / "venues" / "mutant-block", ((FIXTURE_A, 0), (FIXTURE_B, 0)))
admin, target = staged("the mutant venue's git linkage", link_venue, venue, "mutant-block-admin")
staged("the mutant venue's destruction", destroy_venue, venue, admin)
try:
    removed = mutant_remove_venue_block(venue)
except AnchorMoved as exc:
    arm(
        "mutant (venue block deleted): the destroyed venue folds to a green PASS, and the arm-1 shape REDs by name",
        False,
        str(exc),
    )
else:
    ok_syntax, syntax_err = syntax_ok(venue)
    rc, text, attestation = staged("the mutant's run", run, venue)
    problems = venue_shape_problems(rc, text, attestation, venue, target)
    caught = any("venue-invalid" in p or p.startswith("rc") for p in problems)
    green = rc == 0 and "verify: PASS (2 of 2 checks)" in text
    if not ok_syntax:
        detail = "the mutant is not valid bash (%s)" % syntax_err
    elif not green:
        detail = "the mutant did not reproduce the fold-in (rc=%d, no 'verify: PASS (2 of 2 checks)')" % rc
    elif not caught:
        detail = "the arm-1 shape did NOT red against the mutant -- the control has no failing path"
    else:
        detail = "the mutant reads GREEN in the destroyed venue; the arm-1 shape REDs by name (%d problem(s))" % len(problems)
    arm(
        "mutant (venue block deleted): the destroyed venue folds to a green PASS, and the arm-1 shape REDs by name",
        ok_syntax and green and caught,
        detail,
        ["removed %d byte(s) of the venue block from the mounted copy" % removed]
        + run_report(rc, text, attestation)
        + (["the arm-1 shape REDs by name: %s" % "; ".join(problems[:4])] if problems else []),
    )

# --- arm 5: mutant, venue block deleted, rc-2 checks budgeted ---------------
budget_entries = [
    {"check": FIXTURE_A, "kind": "venue", "precondition": "vendor/CMR/sync", "reason": "fixture"},
    {"check": FIXTURE_B, "kind": "venue", "precondition": "vendor/CMR/sync", "reason": "fixture"},
]
venue = staged(
    "the fold-in mutant's venue",
    mount,
    work / "venues" / "mutant-foldin",
    ((FIXTURE_A, 2), (FIXTURE_B, 2)),
    budget_entries,
)
admin, target = staged("the fold-in venue's git linkage", link_venue, venue, "mutant-foldin-admin")
staged("the fold-in venue's destruction", destroy_venue, venue, admin)
try:
    removed = mutant_remove_venue_block(venue)
except AnchorMoved as exc:
    arm(
        "mutant (venue block deleted, rc-2 checks budgeted): the old fold-in reappears as a named-skip PASS, "
        "and the arm-1 shape REDs by name",
        False,
        str(exc),
    )
else:
    ok_syntax, syntax_err = syntax_ok(venue)
    rc, text, attestation = staged("the fold-in mutant's run", run, venue)
    problems = venue_shape_problems(rc, text, attestation, venue, target)
    verdict = [
        line for line in text.splitlines() if line.startswith("verify: PASS") or line.startswith("verify: FAIL")
    ]
    verdict_line = verdict[0] if verdict else ""
    caught = any("venue-invalid" in p or p.startswith("rc") for p in problems)
    foldin = rc == 0 and verdict_line.startswith("verify: PASS") and "2 skipped" in verdict_line
    if not ok_syntax:
        detail = "the mutant is not valid bash (%s)" % syntax_err
    elif not foldin:
        detail = "the fold-in did not reappear (rc=%d, verdict %r)" % (rc, verdict_line[:80])
    elif not caught:
        detail = "the arm-1 shape did NOT red against the mutant -- the control has no failing path"
    else:
        detail = "the destroyed venue reads GREEN with its checks folded into the skip bucket; the arm-1 shape REDs by name"
    arm(
        "mutant (venue block deleted, rc-2 checks budgeted): the old fold-in reappears as a named-skip PASS, "
        "and the arm-1 shape REDs by name",
        ok_syntax and foldin and caught,
        detail,
        ["removed %d byte(s) of the venue block from the mounted copy" % removed]
        + run_report(rc, text, attestation)
        + (["the arm-1 shape REDs by name: %s" % "; ".join(problems[:4])] if problems else []),
    )

# --- arm 6: mutant, only the .git-FILE branch stripped -----------------------
venue = staged("the gitdir mutant's venue", mount, work / "venues" / "mutant-gitdir", ((FIXTURE_A, 0), (FIXTURE_B, 0)))
admin, target = staged("the gitdir mutant venue's git linkage", link_venue, venue, "mutant-gitdir-admin")
staged("the gitdir mutant venue's destruction", destroy_venue, venue, admin)
try:
    removed = mutant_strip_gitdir_branch(venue)
except AnchorMoved as exc:
    arm(
        "mutant (.git-FILE branch stripped): the refusal stops naming the missing admin dir, and the naming "
        "assertions RED by name",
        False,
        str(exc),
    )
else:
    ok_syntax, syntax_err = syntax_ok(venue)
    rc, text, attestation = staged("the gitdir mutant's run", run, venue)
    problems = venue_shape_problems(rc, text, attestation, venue, target)
    naming = [p for p in problems if "admin" in p or "gitdir" in p]
    if not ok_syntax:
        detail = "the mutant is not valid bash (%s)" % syntax_err
    elif rc != 2:
        detail = "the mutant run returned rc %d, not the rc-2 refusal this arm predicts" % rc
    elif not naming:
        detail = "the naming assertions did NOT red against the mutant -- the control would prove nothing"
    else:
        detail = "still ONE rc-2 refusal, but the missing admin dir is no longer named; %d naming assertion(s) RED by name" % len(naming)
    arm(
        "mutant (.git-FILE branch stripped): the refusal stops naming the missing admin dir, and the naming "
        "assertions RED by name",
        ok_syntax and rc == 2 and bool(naming),
        detail,
        ["removed %d byte(s) of the .git-FILE branch from the mounted copy" % removed]
        + run_report(rc, text, attestation)
        + (["the naming assertions RED by name: %s" % "; ".join(naming[:3])] if naming else []),
    )

arms_path.write_text(
    "".join(
        "%s\t%d\t%s\n" % (label.replace("\t", " "), status, detail.replace("\t", " "))
        for label, status, detail in ARMS
    ),
    encoding="utf-8",
)
failed = [label for label, status, _ in ARMS if status]
if failed:
    print("  %d of %d arm(s) failed" % (len(failed), len(ARMS)))
    raise SystemExit(1)
print("  all %d arms held" % len(ARMS))
raise SystemExit(0)
PY
arms_rc=$?
cat "$work/arms.txt"

if [ "$arms_rc" -eq 2 ]; then
  echo "check-venue-invalid: CANNOT-ASSESS -- the control could not build its own venues (named above); nothing was asserted about this tree" >&2
  exit 2
fi

fails=0
checks=0
check() { # check <label> <held:0|1> <detail>
  checks=$((checks + 1))
  if [ "$2" -eq 0 ]; then
    printf '  OK    %s\n' "$1"
  else
    printf '  FAIL  %s: %s\n' "$1" "$3"
    fails=$((fails + 1))
  fi
}

echo "== the assertions =="
if [ -s "$arms_tsv" ]; then
  while IFS=$'\t' read -r label status detail; do
    [ -n "$label" ] || continue
    check "$label" "$status" "$detail"
  done < "$arms_tsv"
fi

if [ "$fails" -eq 0 ] && [ "$arms_rc" -ne 0 ]; then
  check "the control's own battery completed" 1 "the harness exited $arms_rc without a per-arm result"
fi

if [ "$fails" -gt 0 ]; then
  echo "check-venue-invalid: NOT-OK -- $fails of $checks assertion(s) failed" >&2
  exit 1
fi
echo "check-venue-invalid: OK -- $checks assertion(s) held: a destroyed venue is ONE venue-invalid finding naming the venue and its missing admin dir (rc 2, zero checks run, no skip bucket, no skip_ratchet record); an intact venue and a gitless exported venue both keep the probe silent; and three mutants -- the venue block deleted twice (green inside a destroyed venue, and the old fold-in as a named-skip PASS) and the .git-FILE branch stripped -- each RED the arm-1 shape by name"
exit 0
