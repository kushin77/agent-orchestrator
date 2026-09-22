#!/usr/bin/env bash
# check-fleet-bootstrap-scheduled.sh -- a `checkout-behind` park must be paired with a
# SCHEDULED bring-forward (issue #1795).
#
# THE DEFECT THIS EXISTS FOR
#   `fleet/watchdog.py`'s `checkout-behind` case (#773, AO-GR-25) has one correct remedy
#   -- bring the stale checkout forward -- and #780 shipped it as the `bootstrap` verb.
#   But a remedy nothing runs is a description, not a fix: `crontab -l | grep -c
#   bootstrap` was 0, so a `checkout-behind` park was TERMINAL until a human noticed.
#   Measured 2026-09-21 on this box: 12 artifacts in `.fleet/watchdog/escalations/`
#   = 6 incidents in 5 days, every one ending `phase: "parked"` (`attempts: 3`,
#   `respawns: [2]`) -- the watchdog bounding correctly (#773) against a remedy nothing
#   scheduled. Five of the six occurred AFTER #780 was measured, which is the point:
#   closing the issue did not stop the recurrence.
#
# THE INVARIANT, AND WHY IT IS ASSERTED HERE RATHER THAN ONLY IN #780's GATE
#   `scripts/check-checkout-bootstrap.sh` (#780) proves the MECHANISM: that a
#   deliberately stale checkout is brought forward by code that lives outside it. It says
#   nothing about whether that mechanism is REACHED from what the box actually runs. This
#   check is the other half: the DECLARATION (`config/fleet-jobs.json`, the single source
#   `fleet/cron.py` renders the crontab from) must pair the rung that owns the shared
#   checkout with a bring-forward, and the pairing must be MEASURED -- the declared verb
#   is run against a real repository left strictly behind and must perform the move.
#
# WHAT IS PROVEN (against the real declaration and the real module, never a re-implementation)
#   1. DECLARED. The `ao-fleet-watchdog` rung exists, is ENABLED, carries a schedule, and
#      its command invokes the watchdog tool; the same job declares `bring_forward` with a
#      `command` (the tool plus a verb), a `verb`, and the `reached_from` verb of its own
#      command that reaches it. A missing key is refused BY NAME, naming the file and the
#      key (`config/fleet-jobs.json jobs[name=watchdog].bring_forward`).
#   2. THE DECLARED COMMAND IS RUN. Its argv is accepted by the TOOL'S OWN parser (`--help`
#      and an unknown verb must disagree: rc 0 vs rc 2, so this arm can fail) and, driven
#      against a real checkout left one commit BEHIND, its own handler MOVES it.
#   3. THE DECLARED `reached_from` REACHES IT. Driving the preflight of that verb against
#      the same stale checkout performs the move and re-execs onto the arrived code, bounded
#      to one re-exec -- so the SCHEDULED verb, not just the standalone one, performs it.
#   4. MUTANTS REFUSED BY NAME, each proven to have changed its input first (sha256 before
#      != after), so a mutation that never landed cannot be reported as caught: the
#      declaration's `bring_forward` dropped; the rung disabled; the declared verb
#      repointed at a verb the tool rejects; the rung dropped entirely; and the MODULE
#      mutated twice -- the preflight no longer moving the checkout, and `main` no longer
#      routing `run` to the preflight. The module mutants are built in a scratch tree that
#      the driver imports INSTEAD of the checkout, and each must flip a probe whose value
#      has to change.
#   5. the declared file is left BYTE-IDENTICAL (sha256 before and after, both printed).
#
# THE ONE ARM THAT IS A REPORT, NOT A VERDICT
#   Whether THIS box's installed crontab has been refreshed is a fact about its scheduler
#   and the operator's step is `python3 fleet/cron.py install`; failing a lane venue on it
#   is box-state, which the attestation venue owns (#1673). It is printed as a note, never
#   an exit code, and only the STATE (does an `ao-fleet-watchdog` line exist) is reported --
#   never the line's text, which can carry an inline credential.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Test seam: BOOTSTRAP_SCHEDULED_MANIFEST=<path> assesses another declaration instead of
# `config/fleet-jobs.json` -- the SG_*/CHECK_DENYLIST pattern the sibling gates use. The
# self-test uses it, and a scratch copy, to point the SAME predicate at a mutant.
#
# Usage: bash scripts/check-fleet-bootstrap-scheduled.sh
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- python3 not found" >&2
  exit 2
fi

manifest="${BOOTSTRAP_SCHEDULED_MANIFEST:-$root/config/fleet-jobs.json}"
if [ ! -f "$manifest" ]; then
  echo "check-fleet-bootstrap-scheduled: NOT-OK -- bring-forward-declared: $manifest is missing, so the schedule's definition of the checkout-owning rung is gone" >&2
  exit 1
fi
if [ ! -f "$root/fleet/watchdog.py" ]; then
  echo "check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- fleet/watchdog.py is missing, so the bring-forward cannot be measured" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- git is not on PATH, and the bring-forward under test IS git" >&2
  exit 2
fi

# The scratch name is the sanctioned fleet idiom: `$TMPDIR` is a shared, periodically
# cleaned cache on this box, so a plain `mktemp` template can vanish mid-run and report a
# failure that is not the code's. `mkdir` without `-p` refuses loudly rather than silently
# reusing another run's tree.
work="/tmp/ao1795-bootstrap-scheduled.$(date +%s%N).$$"
mkdir "$work" || {
  echo "check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# --- the driver -----------------------------------------------------------------
#
# Drives the REAL module against REAL repositories and prints MEASUREMENTS, never claims.
# It imports `watchdog` from the tree named on its own command line -- so the module
# mutant below is measured through the same code path as the real one -- and asserts the
# import did not escape that tree. `argv` is the DECLARATION's own argv, never a
# re-spelling: mode `declared` parses it with the module's own parser.
cat > "$work/drive.py" <<'DRIVER'
"""Drive the real watchdog: is the declared bring-forward DECLARED and REACHED?"""
import os
import subprocess
import sys
from pathlib import Path

mode = sys.argv[1]
tree = Path(sys.argv[2]).resolve()
scratch = Path(sys.argv[3]).resolve()
argv = sys.argv[4:]
scratch.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True

# The tree under test goes on the path FIRST, so a mutant in a scratch tree cannot be
# shadowed by the checkout's own modules.
sys.path.insert(0, str(tree / "fleet"))
sys.path.insert(0, str(tree))

import watchdog  # noqa: E402

if not Path(watchdog.__file__).resolve().is_relative_to(tree):
    print("IMPORT-ESCAPED %s" % watchdog.__file__, file=sys.stderr)
    sys.exit(9)

AGENT = ["-c", "user.email=agent1795@agents.invalid", "-c", "user.name=agent1795"]
#: Two revisions of the file the freshness read compares. The content is a stand-in --
#: what matters is that the working tree's blob differs from the remote's and that the
#: local HEAD is a strict ancestor of it, which is the `behind` verdict the bring-forward
#: exists to repair.
PRE = '"""the revision before the bring-forward was needed"""\nERA = "pre"\n'
FIX = '"""the revision the remote holds"""\nERA = "post"\n'


def sh(*args):
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def rev(repo, spec="HEAD"):
    return sh("git", "-C", str(repo), "rev-parse", spec).stdout.strip()


def commit_all(repo, message):
    sh("git", "-C", str(repo), "add", "-A")
    result = sh("git", "-C", str(repo), *AGENT, "commit", "-q", "-m", message)
    if result.returncode != 0:
        print("SCENARIO-BROKEN commit: %s" % result.stderr, file=sys.stderr)
        sys.exit(9)


def build(where):
    """A real origin, and a real clone of it left strictly one commit BEHIND."""
    where.mkdir(parents=True, exist_ok=True)
    origin = where / "origin.git"
    sh("git", "init", "--bare", "-q", str(origin))
    sh("git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/master")
    seed = where / "seed"
    sh("git", "clone", "-q", str(origin), str(seed))
    (seed / "fleet").mkdir(parents=True, exist_ok=True)
    (seed / "fleet" / "watchdog.py").write_text(PRE, encoding="utf-8")
    commit_all(seed, "before")
    behind = rev(seed)
    sh("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master")
    (seed / "fleet" / "watchdog.py").write_text(FIX, encoding="utf-8")
    commit_all(seed, "after")
    remote = rev(seed)
    sh("git", "-C", str(seed), "push", "-q", "origin", "HEAD:master")
    stale = where / "stale"
    sh("git", "clone", "-q", str(origin), str(stale))
    sh("git", "-C", str(stale), "reset", "-q", "--hard", behind)
    if rev(stale) != behind:
        print("SCENARIO-BROKEN the clone was not left behind", file=sys.stderr)
        sys.exit(9)
    return stale, behind, remote


def yesno(condition):
    return "yes" if condition else "no"


if mode == "reach":
    # The declared `reached_from` verb's OWN preflight, driven against a stale checkout.
    # `os.execv` is recorded rather than allowed to replace this process, so the routine
    # runs to its own conclusion and the `current` verdict it reports afterwards is real.
    verb = argv[0]
    stale, _behind, remote = build(scratch / "reach")
    os.environ.pop(watchdog.ENV_BOOTSTRAPPED, None)
    execs = []
    watchdog.os.execv = lambda path, ar: execs.append(list(ar))
    rc = watchdog.bootstrap_preflight([verb], root=stale)
    print("reached_from_preflight_reached_the_move=%s" % yesno(rev(stale) == remote))
    print("reached_from_reexec_calls=%d" % len(execs))
    print("reached_from_reexec_same_verb=%s" % yesno(bool(execs) and execs[0][2:] == [verb]))
    print("reached_from_rc_is_none=%s" % yesno(rc is None))
    sys.exit(0)

if mode == "declared":
    # The DECLARATION's own argv, parsed by the module's own parser, run on a stale
    # checkout. `--root` is appended so the drive happens in scratch, never on the box.
    stale, _behind, remote = build(scratch / "declared")
    parser = watchdog.build_parser()
    try:
        ns = parser.parse_args([*argv, "--root", str(stale)])
    except SystemExit as exc:
        print("declared_argv_accepted=no")
        print("declared_argv_rc=%s" % exc.code)
        sys.exit(0)
    print("declared_argv_accepted=yes")
    print("declared_handler=%s" % getattr(getattr(ns, "func", None), "__name__", "unknown"))
    before = rev(stale)
    rc = ns.func(ns)
    print("declared_rc=%s" % rc)
    print("declared_moved_the_checkout=%s" % yesno(rev(stale) != before and rev(stale) == remote))
    sys.exit(0)

print("UNKNOWN-MODE %s" % mode, file=sys.stderr)
sys.exit(9)
DRIVER

# --- the assessment ---------------------------------------------------------------
#
# The driver is the measurement; this program is the judgement. It imports nothing from
# the checkout for its static arms (AST over the real source, the manifest through json)
# except the tool itself, which is run as a subprocess.
python3 - "$root" "$manifest" "$work" <<'PY'
"""Assess the declared pairing, then falsify it against mutants of both halves."""
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
work = Path(sys.argv[3]).resolve()

TOOL = "fleet/watchdog.py"
WATCHDOG_MARKER = "ao-fleet-watchdog"
KEY = "jobs[name=watchdog].bring_forward"
SOURCE = root / "fleet" / "watchdog.py"

failures = []


def probe(name, hold, detail=""):
    print("  probe %s: %s%s" % (name, "PASS" if hold else "FAIL", (" -- " + detail) if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def tokens(command):
    return str(command or "").split()


def yesno(value):
    return "yes" if value else "no"


def driver(tree, mode, *argv):
    """Drive `<tree>`'s module in a subprocess and return its key=value measurements."""
    state = work / ("state-%s-%s" % (mode, digest(str(tree).encode())[:8]))
    state.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("AO_WATCHDOG_"):
            env.pop(name)
    env["AO_FLEET_DIR"] = str(state)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(work / "drive.py"), mode, str(tree), str(state / "scratch"), *argv],
        cwd=str(root), capture_output=True, text=True, env=env,
    )
    measured = {}
    for line in proc.stdout.splitlines():
        if "=" in line and not line.startswith("["):
            key_, _, value = line.partition("=")
            measured[key_.strip()] = value.strip()
    return proc, measured


def tool(argv):
    proc = subprocess.run(
        [sys.executable, str(SOURCE), *argv], cwd=str(root), capture_output=True, text=True
    )
    return proc.returncode, (proc.stdout + proc.stderr)


# --- 0. the declared file must be readable, or the gate cannot assess -------------
try:
    declared_bytes = manifest_path.read_bytes()
except OSError as exc:
    print("check-fleet-bootstrap-scheduled: NOT-OK -- bring-forward-declared: %s could not be read (%s)"
          % (manifest_path, exc), file=sys.stderr)
    sys.exit(1)
declared_sha = digest(declared_bytes)
try:
    manifest = json.loads(declared_bytes.decode("utf-8"))
except (json.JSONDecodeError, UnicodeDecodeError) as exc:
    print("check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- %s is not valid JSON (%s)"
          % (manifest_path, exc), file=sys.stderr)
    sys.exit(2)

try:
    source_text = SOURCE.read_text(encoding="utf-8")
    source_sha = digest(SOURCE.read_bytes())
except OSError as exc:
    print("check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- fleet/watchdog.py could not be read (%s)" % exc,
          file=sys.stderr)
    sys.exit(2)


# --- 1. every named refusal for ONE declaration ----------------------------------
def rung_of(declaration):
    for job in declaration.get("jobs") or []:
        if isinstance(job, dict) and job.get("marker") == WATCHDOG_MARKER:
            return job
    return None


def assess(declaration, label):
    """Every named refusal for one declaration. [] means the pairing is whole."""
    problems = []
    job = rung_of(declaration)
    if job is None:
        return [
            "bring-forward-rung-declared: no job in %s carries the %s marker, so the rung "
            "that owns the shared checkout -- and therefore the one that can park on "
            "`checkout-behind` -- is not scheduled at all" % (label, WATCHDOG_MARKER)
        ]
    if job.get("enabled") is not True:
        problems.append(
            "bring-forward-rung-scheduled: the %s rung is declared but not enabled, so no "
            "installer renders a line for it and nothing brings the checkout forward (%s)"
            % (WATCHDOG_MARKER, label)
        )
    if job.get("interval") is None and not str(job.get("schedule") or "").strip():
        problems.append(
            "bring-forward-rung-scheduled: the %s rung carries neither `interval` nor "
            "`schedule`, so it is declared but never runs (%s)" % (WATCHDOG_MARKER, label)
        )
    command = str(job.get("command") or "")
    argv = tokens(command)
    if TOOL not in argv:
        problems.append(
            "bring-forward-rung-runs-the-watchdog: the %s rung does not invoke %s, so the "
            "verb whose preflight performs the bring-forward is not the verb that runs "
            "(command: %s)" % (WATCHDOG_MARKER, TOOL, command)
        )
    bring_forward = job.get("bring_forward")
    if not isinstance(bring_forward, dict):
        problems.append(
            "bring-forward-declared: %s %s is missing, so the pairing this gate exists for "
            "-- a parked `checkout-behind` rung joined to a bring-forward -- is undeclared"
            % (label, KEY)
        )
        return problems
    verb = str(bring_forward.get("verb") or "")
    reached_from = str(bring_forward.get("reached_from") or "")
    bf_command = str(bring_forward.get("command") or "")
    bf_argv = tokens(bf_command)
    if not verb:
        problems.append("bring-forward-declared: %s %s.verb is missing" % (label, KEY))
    if not reached_from:
        problems.append("bring-forward-declared: %s %s.reached_from is missing" % (label, KEY))
    if TOOL not in bf_argv:
        problems.append(
            "bring-forward-command-runs-the-watchdog: %s %s.command does not invoke %s "
            "(command: %s)" % (label, KEY, TOOL, bf_command)
        )
    elif verb and verb not in bf_argv:
        problems.append(
            "bring-forward-command-names-the-verb: %s %s.command does not name its own "
            "declared verb %r (command: %s)" % (label, KEY, verb, bf_command)
        )
    if reached_from and reached_from not in argv:
        problems.append(
            "bring-forward-reached-from: %s %s.reached_from=%r is not a verb of the rung's "
            "own command, so nothing scheduled reaches the bring-forward (command: %s)"
            % (label, KEY, reached_from, command)
        )
    return problems


real_problems = assess(manifest, str(manifest_path))
probe("DECLARATION-PAIRS-THE-SCHEDULED-RUNG-WITH-A-BRING-FORWARD",
      real_problems == [], "; ".join(real_problems) or "no problems")
for problem in real_problems:
    print("  refusal %s" % problem)

real_job = rung_of(manifest) or {}
bring_forward = real_job.get("bring_forward") if isinstance(real_job.get("bring_forward"), dict) else {}
verb = str(bring_forward.get("verb") or "")
reached_from = str(bring_forward.get("reached_from") or "")
# The declared command's argv with its interpreter and tool stripped: what the tool is
# asked to DO. `--root <path>` is appended by the driver so the drive stays in scratch.
bf_rest = [t for t in tokens(bring_forward.get("command")) if t not in ("/usr/bin/python3", TOOL)]


# --- 2. the tool accepts the declared verb, and the acceptance can fail ----------
if verb:
    accepted_rc, _accepted_out = tool([verb, "--help"])
    probe(
        "DECLARED-VERB-ACCEPTED-BY-THE-TOOL",
        accepted_rc == 0,
        "python3 %s %s --help -> rc=%s" % (TOOL, verb, accepted_rc),
    )
    unknown_rc, unknown_out = tool(["not-a-verb-this-tool-does-not-have", "--help"])
    probe(
        "VERB-ACCEPTANCE-CAN-FAIL",
        unknown_rc == 2 and "invalid choice" in unknown_out,
        "python3 %s <unknown> --help -> rc=%s %s"
        % (TOOL, unknown_rc, (unknown_out.strip().splitlines() or [""])[-1][:90]),
    )
else:
    probe("DECLARED-VERB-ACCEPTED-BY-THE-TOOL", False, "no declared verb to accept")

# --- 3. the module's own source routes the declared pairing ----------------------
# A static measurement of the REAL source, so a mutant that removes the routing is caught
# by the same predicate the real tree is judged with. `run` must reach the preflight, the
# preflight must reach the move, and `main` must branch on the DECLARED reached_from.
def calls_named(node, name):
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            label = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if label == name:
                return True
    return False


def compares_command_to(node, verb_name):
    if node is None:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Compare) and any(
            isinstance(operand, ast.Constant) and operand.value == verb_name
            for operand in [child.left, *child.comparators]
        ):
            return True
    return False


def routing_arm(text, verb_name):
    """The routing predicate, as a function of the source: `main` must reach the preflight
    when the command is the DECLARED `reached_from` verb. Kept as a function so the SAME
    predicate is applied to the real source and to a mutant of it -- a mutation that is
    only ever exercised through a different code path proves nothing."""
    try:
        parsed = ast.parse(text)
    except SyntaxError:
        return False
    defs = {
        node.name: node
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return calls_named(defs.get("main"), "bootstrap_preflight") and compares_command_to(
        defs.get("main"), verb_name
    )


try:
    parsed_source = ast.parse(source_text)
except SyntaxError as exc:
    print("check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- fleet/watchdog.py does not parse (%s)" % exc,
          file=sys.stderr)
    sys.exit(2)

_top = {
    node.name: node
    for node in parsed_source.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}
probe(
    "MODULE-MAIN-ROUTES-THE-DECLARED-VERB-TO-THE-PREFLIGHT",
    routing_arm(source_text, reached_from),
    "main calls bootstrap_preflight and branches on args.command == %r" % reached_from,
)
probe(
    "MODULE-PREFLIGHT-PERFORMS-THE-BRING-FORWARD",
    calls_named(_top.get("bootstrap_preflight"), "bootstrap_checkout"),
    "bootstrap_preflight calls bootstrap_checkout",
)

# --- 4. the measured reachability: the declared halves, RUN against a stale checkout --
if verb and reached_from and not real_problems:
    proc, measured = driver(root, "reach", reached_from)
    if proc.returncode != 0:
        probe("SCHEDULED-VERB-REACHES-THE-BRING-FORWARD", False,
              "the driver exited %s: %s" % (proc.returncode, (proc.stderr or "").strip()[:120]))
    else:
        holds = (
            measured.get("reached_from_preflight_reached_the_move") == "yes"
            and measured.get("reached_from_rc_is_none") == "yes"
            and measured.get("reached_from_reexec_same_verb") == "yes"
            and measured.get("reached_from_reexec_calls") == "1"
        )
        probe("SCHEDULED-VERB-REACHES-THE-BRING-FORWARD", holds, json.dumps(measured, sort_keys=True))
    proc, measured = driver(root, "declared", *bf_rest)
    if proc.returncode != 0:
        probe("DECLARED-COMMAND-MOVES-A-STALE-CHECKOUT", False,
              "the driver exited %s: %s" % (proc.returncode, (proc.stderr or "").strip()[:120]))
    else:
        holds = (
            measured.get("declared_argv_accepted") == "yes"
            and measured.get("declared_moved_the_checkout") == "yes"
            and measured.get("declared_rc") == "0"
        )
        probe("DECLARED-COMMAND-MOVES-A-STALE-CHECKOUT", holds, json.dumps(measured, sort_keys=True))
else:
    probe("SCHEDULED-VERB-REACHES-THE-BRING-FORWARD", False, "the declaration does not name a verb to drive")
    probe("DECLARED-COMMAND-MOVES-A-STALE-CHECKOUT", False, "the declaration does not name a command to drive")

# --- 5. the mutants the gate must refuse, by name -------------------------------
def scratch_tree(name, mutate):
    """A scratch tree whose `fleet/` is symlinks to the real one EXCEPT the mutated file,
    and whose `governance/` is a symlink. Cheap, and the mutant cannot be shadowed: the
    driver asserts the module it imported lives inside the tree it was handed."""
    tree = work / name
    (tree / "fleet").mkdir(parents=True, exist_ok=True)
    for entry in (root / "fleet").iterdir():
        if entry.name in ("__pycache__", "watchdog.py"):
            continue
        link = tree / "fleet" / entry.name
        if not link.exists():
            link.symlink_to(entry)
    if not (tree / "governance").exists():
        (tree / "governance").symlink_to(root / "governance")
    (tree / "fleet" / "watchdog.py").write_text(mutate(source_text), encoding="utf-8")
    return tree


def manifest_mutant(label, expected, edit):
    """A SCRATCH COPY of the declaration, refused by name, with the landing proved.

    Only ever called on a WHOLE declaration (see the guard below): a mutant is a claim
    that the gate catches a change to something that was there, and there is nothing for
    it to break when the input is already deficient -- the refusals above say so, by name.
    """
    if digest(manifest_path.read_bytes()) != declared_sha:
        probe("MUTANT-%s-APPLIED" % label, False, "the declared file changed under the check")
        return
    clone = json.loads(declared_bytes.decode("utf-8"))
    edit(clone)
    path = work / ("declaration-%s.json" % label)
    path.write_bytes(json.dumps(clone, indent=2).encode("utf-8") + b"\n")
    mutant_sha = digest(path.read_bytes())
    if mutant_sha == declared_sha:
        probe("MUTANT-%s-APPLIED" % label, False, "the mutation did not change the declaration")
        return
    probe("MUTANT-%s-APPLIED" % label, True, "sha256 %s -> %s" % (declared_sha[:12], mutant_sha[:12]))
    reloaded = json.loads(path.read_bytes().decode("utf-8"))
    named = [p for p in assess(reloaded, str(path)) if p.startswith(expected + ":")]
    probe(
        "MUTANT-%s-REFUSED" % label,
        bool(named),
        named[0] if named else "expected a %s refusal, ACTUAL: %s" % (expected, assess(reloaded, str(path)) or "no refusal"),
    )


if real_problems:
    # The declaration is already deficient, so it is named above and there is no whole
    # version of it for a mutant to differ from. Printed, never silent, and the check
    # still exits NOT-OK on the refusals that were found.
    print(
        "  note MUTANT-ARMS-SKIPPED: the declaration under test is already deficient, so a "
        "mutant of either half would be judged against a broken fixture; the refusal(s) "
        "above name the file and the key"
    )
else:
    manifest_mutant(
        "bring-forward-dropped", "bring-forward-declared",
        lambda clone: rung_of(clone).pop("bring_forward", None),
    )
    manifest_mutant(
        "rung-disabled", "bring-forward-rung-scheduled",
        lambda clone: rung_of(clone).__setitem__("enabled", False),
    )
    manifest_mutant(
        "verb-repointed", "bring-forward-command-names-the-verb",
        lambda clone: rung_of(clone)["bring_forward"].__setitem__("verb", "no-such-verb"),
    )
    manifest_mutant(
        "rung-dropped", "bring-forward-rung-declared",
        lambda clone: clone.__setitem__(
            "jobs", [j for j in clone["jobs"] if j.get("marker") != WATCHDOG_MARKER]
        ),
    )

def write_mutant(label, edit):
    """A mutant of the REAL source in a scratch tree; returns (text, target) or None.
    The landing is proved by sha256, so a mutation that never applied cannot be
    reported as caught -- the same discipline the manifest mutants use. Skipped, by
    name, when the declaration under test is already deficient: a mutant is a claim
    about a change to something that was whole, and there is nothing whole here."""
    if real_problems:
        print(
            "  note MUTANT-%s-SKIPPED: the declaration under test is already deficient, so a "
            "module mutation would be judged against a broken fixture" % label
        )
        return None, None
    text = edit(source_text)
    mutant = scratch_tree("mutant-%s" % label, lambda _text: text)
    target = mutant / "fleet" / "watchdog.py"
    mutant_sha = digest(target.read_bytes())
    if mutant_sha == source_sha or text == source_text:
        probe("MUTANT-%s-APPLIED" % label, False, "the mutation did not change the module")
        return None, None
    probe("MUTANT-%s-APPLIED" % label, True, "sha256 %s -> %s" % (source_sha[:12], mutant_sha[:12]))
    return text, mutant


# (a) the PREFLIGHT no longer moves the checkout -- driven through the verb the rung
# runs, so the arm that must flip is `reached_from_preflight_reached_the_move`.
_text, mutant = write_mutant(
    "preflight-no-longer-moves",
    lambda text: text.replace(
        '    freshness = self_freshness(root)\n'
        '    print(f"[watchdog] source: {freshness_line(freshness)}", flush=True)\n'
        '    verdict = freshness["verdict"]\n',
        "    return None\n",
        1,
    ),
)
if mutant is not None:
    proc, measured = driver(mutant, "reach", reached_from)
    holds = (
        measured.get("reached_from_preflight_reached_the_move") == "yes"
        and measured.get("reached_from_rc_is_none") == "yes"
    )
    probe(
        "MUTANT-preflight-no-longer-moves-REFUSED",
        proc.returncode == 0 and not holds,
        "expected the scheduled verb to stop reaching the move, ACTUAL %s"
        % json.dumps(measured, sort_keys=True),
    )

# (b) `main` no longer routes the declared verb to the preflight: the same regression one
# level up. Measured with the SAME routing predicate the real tree is judged with.
mutant_text, _mutant = write_mutant(
    "main-no-longer-routes",
    lambda text: text.replace('if args.command == "run":', "if False:", 1),
)
if mutant_text is not None:
    probe(
        "MUTANT-main-no-longer-routes-REFUSED",
        not routing_arm(mutant_text, reached_from),
        "expected the routing predicate to stop holding on the mutant",
    )

# (c) the DECLARED command no longer moves anything -- the arm that must flip is the
# `declared` drive of the declaration's own argv.
_text2, mutant2 = write_mutant(
    "declared-command-no-longer-moves",
    lambda text: text.replace(
        "    moved, _head, detail = bootstrap_checkout(root)\n"
        '    print(f"[watchdog] bootstrap: {detail}", flush=True)\n'
        "    after = self_freshness(root)\n",
        '    moved, _head, detail = False, "?", "mutated"\n'
        '    print(f"[watchdog] bootstrap: {detail}", flush=True)\n'
        "    after = self_freshness(root)\n",
        1,
    ),
)
if mutant2 is not None:
    proc, measured = driver(mutant2, "declared", *bf_rest)
    probe(
        "MUTANT-declared-command-no-longer-moves-REFUSED",
        proc.returncode == 0 and measured.get("declared_moved_the_checkout") == "no",
        "expected the declared command to stop moving the checkout, ACTUAL %s"
        % json.dumps(measured, sort_keys=True),
    )

# --- 6. the declared file is left byte-identical --------------------------------
after_sha = digest(manifest_path.read_bytes())
print("  declaration sha256 before: %s" % declared_sha)
print("  declaration sha256 after:  %s" % after_sha)
probe("DECLARATION-LEFT-BYTE-IDENTICAL", after_sha == declared_sha, str(manifest_path))

if failures:
    print("check-fleet-bootstrap-scheduled: FAILED probe(s): %s" % ", ".join(failures))
    sys.exit(1)
print("check-fleet-bootstrap-scheduled: all probes PASS")
PY

rc=$?
if [ "$rc" -eq 2 ]; then
  echo "check-fleet-bootstrap-scheduled: CANNOT-ASSESS -- the declaration or the module could not be read" >&2
  exit 2
fi
if [ "$rc" -ne 0 ]; then
  echo "check-fleet-bootstrap-scheduled: NOT-OK -- the scheduled rung is not paired with a reachable bring-forward; see the refusal(s) above" >&2
  exit 1
fi

# This box's installation state, reported and never enforced (#1673: box-state is the
# attestation venue's). Only the STATE is printed -- an installed crontab line can carry
# an inline credential, which is why the sibling reap gate counts rather than quotes too.
crontab_text="$(crontab -l 2>/dev/null || true)"
if [ -n "$crontab_text" ]; then
  installed="$(printf '%s\n' "$crontab_text" | grep -cF -- '# ao-fleet-watchdog' || true)"
  if [ "${installed:-0}" -gt 0 ]; then
    echo "  note installed ao-fleet-watchdog line(s): ${installed} -- the rung is scheduled on this box"
  else
    echo "  note installed ao-fleet-watchdog line(s): 0 -- the operator's step is: python3 fleet/cron.py install"
  fi
fi

echo "check-fleet-bootstrap-scheduled: OK -- config/fleet-jobs.json pairs the scheduled ao-fleet-watchdog rung with the bootstrap bring-forward, the declared command moves a real stale checkout, the scheduled verb reaches it through the preflight, and every mutant of either half is refused by name"
exit 0
