#!/usr/bin/env bash
# check-reap-branches.sh -- the scheduled reap rung must drain LANDED BRANCHES (issue #1360).
#
# THE DEFECT THIS EXISTS FOR
#   `config/fleet-jobs.json` declared the nightly reap rung as
#   `bash scripts/prune-worktrees.sh --apply`, and the installed crontab line
#   (`# ao-fleet-reap`, `47 3 * * *`) was byte-identical to it. NEITHER passed
#   `--branches`, so the branch half the reaper gained in #1118 -- reap a local
#   lane branch whose own change is provably on `origin/master`, decided by
#   CONTENT, because this repo squash-merges and a landed branch is therefore
#   never an ancestor of master -- NEVER RAN. Only the worktree half did.
#
# WHY THAT IS FLEET-FATAL, NOT UNTIDY
#   `scripts/check-reconcile.sh` is fatal on an artifact that is NOT in
#   `governance/reconcile/real-tree-baseline.json` and older than its 24h grace.
#   The branch pile is therefore a time bomb: every landed branch that lives past
#   the grace becomes a `NEW` violation with NO code change at all, so pristine
#   master reads `verify: FAIL (1 of 205 checks failed)` and `governance/landing`
#   refuses EVERY landing -- `verify` is the one signal it never attributes
#   (`signal-red-is-not-attributable`). Measured twice in one day: worktree
#   `ao-1178`, then branches `issue-1145-baseline` / `issue-1145-residue`. A gate
#   that reds for everyone because a scheduled rung cannot do half its job is not
#   a lint, it is an outage.
#
# WHAT IS PROVEN (against the real declaration, and against mutants of it)
#   1. the declaration still declares the rung: a job carrying the `ao-fleet-reap`
#      marker exists, is ENABLED, and its command still invokes the shipped tool
#      `scripts/prune-worktrees.sh` -- a rung repointed at another tool, or
#      dropped, is named rather than ignored;
#   2. the rung carries BOTH halves as ARGUMENTS (exact token match, never a
#      substring a comment could satisfy): `--branches`, without which the landed
#      branch pile rebuilds, and `--apply`, without which the rung reports and
#      reclaims nothing;
#   3. `--branches` is a flag THE TOOL ITSELF ACCEPTS. The declared argv form is
#      run against the tool's own argument parser (`--help` exits during parsing;
#      no worktree is scanned and nothing is removed) and must exit 0, while an
#      unknown flag must exit 2 -- so a schedule cannot be "fixed" by naming a
#      flag the tool would reject at 03:47, and the acceptance arm is provably
#      able to fail;
#   4. the line `fleet/cron.py` -- the manifest's SINGLE WRITER, the only thing
#      `install`/`reconcile` render from -- actually emits, carries `--branches`
#      and the `ao-fleet-reap` marker. Fixing the declaration but not the thing
#      that INSTALLS it is the failure this arm exists for: it is the half that
#      caught `reap_line()` re-spelling the old command during #1360;
#   5. three mutants are REFUSED BY NAME, each proven to have changed the
#      declaration's bytes first (sha256 before/after, so a mutation that never
#      applied cannot be reported as caught): `--branches` dropped, `--apply`
#      dropped, the rung dropped entirely. Each mutant is a SCRATCH COPY -- this
#      check never writes the declared file -- and the RENDER of the branchless
#      mutant is asserted to have lost the flag as well, so the render arm is
#      shown to follow the declaration rather than a constant;
#   6. the declared file is left BYTE-IDENTICAL: its sha256 is taken before and
#      after, both printed, and a difference fails the check. A missing
#      declaration is NOT-OK (the schedule's definition is gone); one that cannot
#      be parsed is CANNOT-ASSESS (rc 2), never a pass.
#
# THE ONE ARM THAT IS A REPORT, NOT A VERDICT
#   Whether the INSTALLED crontab has been refreshed is a fact about this box's
#   scheduler, and the operator's step is `python3 fleet/cron.py install`.
#   Failing the gate on it would red every lane on a box whose operator has not
#   run that yet -- the very outage this check exists to prevent -- so it is
#   printed as a NOTE and never sets an exit code. What the run reports is the
#   STATE (does the installed line carry the flag), never the line's text: an
#   installed crontab line can carry an inline credential, the same reason
#   `prune-worktrees.sh --schedule` counts its matches instead of echoing them.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Test seam: REAP_BRANCHES_MANIFEST=<path> assesses another declaration instead
# of `config/fleet-jobs.json` (the SG_*/CHECK_DENYLIST pattern the sibling gates
# use). The self-test uses it to point the SAME predicate at a mutant.
#
# Usage: bash scripts/check-reap-branches.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-reap-branches: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
fi

manifest="${REAP_BRANCHES_MANIFEST:-$root/config/fleet-jobs.json}"
if [ ! -f "$manifest" ]; then
  echo "check-reap-branches: NOT-OK -- reap-rung-declared: $manifest is missing, so the schedule's definition of the reap is gone" >&2
  exit 1
fi
if [ ! -f "$root/fleet/cron.py" ]; then
  echo "check-reap-branches: CANNOT-ASSESS -- fleet/cron.py is missing, so the rendered line cannot be read" >&2
  exit 2
fi
if [ ! -f "$root/scripts/prune-worktrees.sh" ]; then
  echo "check-reap-branches: NOT-OK -- reap-rung-runs-the-reaper: scripts/prune-worktrees.sh is missing" >&2
  exit 1
fi

# The scratch tree name is the sanctioned fleet idiom, built from the pid and the
# clock rather than from a template placeholder (a run of one letter is a match
# for this repo's own unfinished-marker scan). `mkdir` without `-p` refuses
# loudly instead of silently reusing another run's tree.
work="/tmp/ao1360-reap-branches.$$.$(date +%s%N)"
mkdir "$work" 2>/dev/null || {
  echo "check-reap-branches: CANNOT-ASSESS -- no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# The driver is the whole proof; this shell maps its rc onto the tri-state. It
# imports `cron` from THIS checkout's fleet/, so the renderer under review is what
# is measured, and it never writes outside the scratch tree.
python3 - "$root" "$manifest" "$work" <<'PY'
"""Prove the declared reap rung -- and the line it renders to -- drains landed branches."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
scratch = Path(sys.argv[3]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

sys.dont_write_bytecode = True
sys.path.insert(0, str(repo_root / "fleet"))

import cron  # noqa: E402

BRANCHES = "--branches"
APPLY = "--apply"
TOOL = "scripts/prune-worktrees.sh"

failures: list[str] = []


def probe(name, hold, detail=""):
    print("  probe %s: %s%s" % (name, "PASS" if hold else "FAIL", (" -- " + detail) if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def tokens(command) -> list[str]:
    return str(command or "").split()


def reap_job(manifest):
    for job in cron.manifest_jobs(manifest):
        if job.get("marker") == cron.REAP_MARKER:
            return job
    return None


def assess(manifest, label):
    """Every named refusal for ONE declaration. [] means the reap rung is whole."""
    problems = []
    job = reap_job(manifest)
    if job is None:
        return [
            "reap-rung-declared: no job in %s carries the %s marker, so nothing drains "
            "landed branches or stale worktrees on a schedule" % (label, cron.REAP_MARKER)
        ]
    if job.get("enabled") is not True:
        problems.append(
            "reap-rung-enabled: the %s rung is declared but not enabled, so no installer "
            "renders a line for it" % cron.REAP_MARKER
        )
    command = str(job.get("command") or "")
    argv = tokens(command)
    if TOOL not in argv:
        problems.append(
            "reap-rung-runs-the-reaper: the %s rung does not invoke %s (command: %s)"
            % (cron.REAP_MARKER, TOOL, command)
        )
    if BRANCHES not in argv:
        problems.append(
            "reap-rung-drains-branches: the %s rung does not pass %s, so the branch half of "
            "the reaper (#1118) never runs and every landed branch eventually ages past "
            "check-reconcile's 24h grace and reds the gate of record for every lane "
            "(issue #1360); command: %s" % (cron.REAP_MARKER, BRANCHES, command)
        )
    if APPLY not in argv:
        problems.append(
            "reap-rung-applies: the %s rung does not pass %s, so it reports and reclaims "
            "nothing at all; command: %s" % (cron.REAP_MARKER, APPLY, command)
        )
    return problems


def installer_line(manifest) -> str:
    """The line `fleet/cron.py install` writes for the reap rung -- from its own renderer."""
    for job in cron.enabled_jobs(manifest):
        if job.get("marker") == cron.REAP_MARKER:
            return cron.render_job(job)
    return ""


# --- 0. the declared file must be readable, or the gate cannot assess -------
try:
    declared_bytes = manifest_path.read_bytes()
except OSError as exc:
    print("check-reap-branches: NOT-OK -- reap-rung-declared: %s could not be read (%s)"
          % (manifest_path, exc), file=sys.stderr)
    sys.exit(1)
declared_sha = digest(declared_bytes)
try:
    manifest = json.loads(declared_bytes.decode("utf-8"))
except json.JSONDecodeError as exc:
    print("check-reap-branches: CANNOT-ASSESS -- %s is not valid JSON (%s)"
          % (manifest_path, exc), file=sys.stderr)
    sys.exit(2)

# --- 1 + 2. the real declaration declares the whole rung --------------------
real_problems = assess(manifest, str(manifest_path))
probe("REAL-RUNG-DECLARED-AND-DRAINS-BRANCHES", real_problems == [], "; ".join(real_problems) or "no problems")
for problem in real_problems:
    print("  refusal %s" % problem)

# --- 3. the tool accepts the flag the declaration names ---------------------
def tool_run(args):
    proc = subprocess.run(
        ["bash", str(repo_root / TOOL), *args],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


accepted_rc, accepted_out = tool_run([BRANCHES, "--help"])
usage_line = next(
    (line.strip() for line in accepted_out.splitlines() if TOOL in line and BRANCHES in line),
    next((line.strip() for line in accepted_out.splitlines() if BRANCHES in line), ""),
)
probe(
    "REAP-FLAG-ACCEPTED-BY-THE-TOOL",
    accepted_rc == 0,
    "bash %s %s --help -> rc=%s; the tool's own usage names it: %s"
    % (TOOL, BRANCHES, accepted_rc, usage_line or "(no usage line names it)"),
)
unknown_rc, unknown_out = tool_run(["--this-flag-does-not-exist", "--help"])
unknown_line = next(iter(unknown_out.strip().splitlines()), "")
probe(
    "REAP-FLAG-ACCEPTANCE-CAN-FAIL",
    unknown_rc == 2 and "unknown argument" in unknown_line,
    "bash %s --this-flag-does-not-exist --help -> rc=%s %s" % (TOOL, unknown_rc, unknown_line),
)

# --- 4. the single writer renders the flag into the line it installs --------
rendered = installer_line(manifest)
probe(
    "REAP-RENDER-CARRIES-BRANCHES",
    bool(rendered)
    and BRANCHES in tokens(rendered)
    and rendered.rstrip().endswith("# " + cron.REAP_MARKER),
    rendered or "no enabled %s rung to render" % cron.REAP_MARKER,
)

# --- 5. the mutants the gate must refuse, by name ---------------------------
def mutant(label, expected, edit):
    """Write a mutant SCRATCH COPY through the same reader, then refuse it by name."""
    if digest(declared_bytes) != declared_sha:
        probe("MUTANT-%s-APPLIED" % label, False, "the declared file changed under the check")
        return None
    clone = json.loads(declared_bytes.decode("utf-8"))
    edit(clone)
    path = scratch / ("declaration-%s.json" % label)
    path.write_bytes(json.dumps(clone, indent=2).encode("utf-8") + b"\n")
    mutant_bytes = path.read_bytes()
    if digest(mutant_bytes) == declared_sha:
        probe("MUTANT-%s-APPLIED" % label, False,
              "the mutation did not change the declaration (the flag is already absent)")
        return None
    probe("MUTANT-%s-APPLIED" % label, True,
          "sha256 %s -> %s" % (declared_sha[:12], digest(mutant_bytes)[:12]))
    reloaded = json.loads(mutant_bytes.decode("utf-8"))
    named = [p for p in assess(reloaded, str(path)) if p.startswith(expected + ":")]
    probe(
        "MUTANT-%s-REFUSED" % label,
        bool(named),
        named[0] if named else "expected a %s refusal, ACTUAL: %s" % (expected, assess(reloaded, str(path)) or "no refusal"),
    )
    return reloaded


def without_flag(flag):
    def edit(clone):
        for job in clone["jobs"]:
            if job.get("marker") == cron.REAP_MARKER:
                job["command"] = " ".join(t for t in tokens(job.get("command")) if t != flag)
    return edit


branchless = mutant("branches-dropped", "reap-rung-drains-branches", without_flag(BRANCHES))
mutant("apply-dropped", "reap-rung-applies", without_flag(APPLY))
mutant(
    "rung-dropped",
    "reap-rung-declared",
    lambda clone: clone.__setitem__(
        "jobs", [j for j in clone["jobs"] if j.get("marker") != cron.REAP_MARKER]
    ),
)

# The render arm must FOLLOW the declaration, not a constant: take the mutant
# that still has a rung but no `--branches` and show the rendered line loses it.
mutant_line = installer_line(branchless) if branchless is not None else ""
probe(
    "MUTANT-BRANCHLESS-RENDER-LOSES-BRANCHES",
    bool(mutant_line) and BRANCHES not in tokens(mutant_line) and APPLY in tokens(mutant_line),
    mutant_line or "no rung left in the mutant to render",
)

# --- 6. the declared file is left byte-identical ----------------------------
after_sha = digest(manifest_path.read_bytes())
print("  declaration sha256 before: %s" % declared_sha)
print("  declaration sha256 after:  %s" % after_sha)
probe("DECLARATION-LEFT-BYTE-IDENTICAL", after_sha == declared_sha, str(manifest_path))

if failures:
    print("check-reap-branches: FAILED probe(s): %s" % ", ".join(failures))
    sys.exit(1)
print("check-reap-branches: all probes PASS")
PY

rc=$?
if [ "$rc" -eq 2 ]; then
  echo "check-reap-branches: CANNOT-ASSESS -- the declaration could not be read or the driver could not assess" >&2
  exit 2
fi
if [ "$rc" -ne 0 ]; then
  echo "check-reap-branches: NOT-OK -- the scheduled reap rung is not whole; see the refusal(s) above" >&2
  exit 1
fi

# The installation state of THIS box, reported and never enforced (see the header).
# The line's TEXT is never echoed: an installed crontab line can carry an inline
# credential, the same reason `prune-worktrees.sh --schedule` counts its matches
# instead of quoting them. The containment test is BASH-NATIVE -- `case` over a
# space-padded copy -- and never a pipe into a quiet `grep`: `grep -q` exits on
# its first match, so under `set -o pipefail` the producer's SIGPIPE becomes the
# status of the whole test, i.e. a verdict about the report's SIZE rather than
# about the schedule (scripts/check-verdict-contains.sh, #843/#931).
crontab_text="$(crontab -l 2>/dev/null || true)"
installed_reap=""
if [ -n "$crontab_text" ]; then
  installed_reap="$(printf '%s\n' "$crontab_text" | grep -F -- '# ao-fleet-reap' || true)"
fi
if [ -n "$installed_reap" ]; then
  installed_count="$(printf '%s\n' "$installed_reap" | wc -l | tr -d ' ')"
  carries="NO"
  case " $installed_reap " in
    *" --branches "*) carries="yes" ;;
  esac
  if [ "$carries" = "yes" ]; then
    echo "  note installed ao-fleet-reap line carries --branches: yes (${installed_count} installed line(s))"
  else
    echo "  note installed ao-fleet-reap line carries --branches: NO (${installed_count} installed line(s)) -- the operator's step is: python3 fleet/cron.py install"
  fi
fi

echo "check-reap-branches: OK -- the declared ao-fleet-reap rung and the line fleet/cron.py renders from it both pass --branches (and --apply); the branchless mutant is refused by name"
exit 0
