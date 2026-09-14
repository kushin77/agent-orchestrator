#!/usr/bin/env bash
# check-gate-coverage.sh — the gate that fails when a delivered artifact is
# invoked by NO gate (issue #526, RCA of EPIC #524).
#
# THE DEFECT THIS EXISTS FOR
#   Gate coverage in this repo is OPT-IN. `scripts/verify.sh` holds an explicit
#   `checks=()` array, so a new `scripts/check-*.sh` that nobody registers is
#   inert: it exists, it looks like a gate, and no gate ever runs it. The same
#   is true of the pytest corpus: `scripts/pytest-suites.txt` declares 71
#   suites, but the gate of record only ever runs the ones a gate NAMES.
#   Nothing complained when the count of unwired check scripts went from 0 (at
#   the first measurement, 2026-09-14 morning) to 8 (re-measured the same day at
#   ffb2688, when EPIC #499's chat surface landed six of them). A class that can
#   grow with no detector is the whole argument for this check.
#
# THE GATE-INVOCATION UNIVERSE (explicit, by design — never a glob)
#   A file counts as a gate if and only if it is one of these five:
#     1. scripts/verify.sh        — the gate of record (`make verify`)
#     2. Makefile                 — every `make <target>` entry point
#     3. scripts/gate.sh          — `make gate` (full QA gate, issue #29)
#     4. scripts/merge-gate.sh    — the pre-merge contract (issue #29)
#     5. scripts/qa-loop.sh       — the fix -> verify -> re-check loop
#   The list is literal on purpose: a glob (`scripts/*.sh`) would make the
#   universe grow silently, which is the very failure mode being checked.
#
# WHAT IS MEASURED (two artifact classes, two rules)
#
#   1. CHECK SCRIPTS — every delivered `scripts/check-*.sh` and
#      `scripts/check-*.py` must be invoked BY PATH from a gate file. The
#      candidate list comes from the FILESYSTEM (minus git-ignored paths), not
#      from `git ls-files`: a lane's just-delivered, still-untracked check script
#      would otherwise be invisible and report green -- observed while
#      mutation-proving this very check.
#      Match by PATH, never by check name: the check NAMES in `scripts/verify.sh` differ
#      from the FILENAMES for real checks (`check-python-syntax.sh` runs as the
#      `python-syntax` check, `check-board-gate.sh` as `board-gate`), so a
#      name-based match invents findings that are not there. It also cannot
#      manufacture a pass: only a literal repo-relative path occurrence counts,
#      so a script mentioned in prose is still unwired. (Comment stripping was
#      measured to change nothing here: WIRED=70 either way.)
#
#   2. DECLARED PYTEST SUITES — every suite declared in
#      `scripts/pytest-suites.txt` must be NAMED by a pytest invocation in the
#      gate surface (`<suite>` or `<suite>/tests` as the target of a pytest
#      command line). A suite that is only ever run because
#      `scripts/run-pytest-suites.sh` sweeps the manifest is NOT covered:
#      the artifact declares itself into existence, so a suite added by a lane
#      that no gate ever runs looks exactly like a covered one. The gate
#      surface is the five gate files PLUS the check scripts they invoke by
#      path (bounded: the check scripts are the gate's own instrument set), so a
#      suite run from inside a check (`check-paperclip-auth.sh` runs
#      `integrations/paperclip/auth/tests`) counts as covered. Logical lines are
#      joined across `\` continuations and simple literal `VAR=<path>`
#      assignments in the same file are substituted, so `"$api_dir/tests"` is
#      read as the path it is. Granularity is the suite DIRECTORY as a pytest
#      target: a gate that runs one module file out of a suite
#      (`check-codeidx-backend.sh` runs `gateway/mcp/tests/test_...py`) does not
#      cover the suite, and that suite stays reported.
#
# THE BASELINE, AND WHY IT CANNOT ROT INTO A FICTION
#   Rules 1 and 2 are red on master today (8 unwired check scripts, and the
#   suites only the manifest sweep reaches). A gate that is red on master is
#   either disabled or ignored — strictly worse than no gate. So the accepted
#   exceptions live in ONE committed, explicit, per-path file
#   (`scripts/gate-coverage-baseline.txt`), and the honest part is what keeps it
#   from becoming a permanent excuse:
#     * a NEWLY declared suite or a NEWLY delivered `scripts/check-*.sh` is
#       NEVER grandfathered — it fails immediately, naming the path;
#     * the baseline is checked in BOTH directions: an entry whose artifact is
#       now wired, or whose artifact no longer exists, is a STALE entry and
#       fails, naming it. Removing an entry is the only way to shrink the list,
#       and a stale entry cannot sit there pretending to be a live exception;
#     * the reason field is a closed vocabulary (`swept-only` for a suite, and
#       `uninvoked` for a check script), so the baseline cannot drift into prose
#       that explains nothing, and a malformed line fails rather than being
#       skipped;
#     * a duplicate entry fails (it would silently excuse the same path twice);
#     * entries are per path — no globs, no wildcard prefixes — because a glob
#       would absorb a newly declared artifact under that prefix and quietly
#       restore the opt-in hole.
#
#   PROVENANCE (#603) — the rules above cannot bind without it. A lane that
#   delivers a new check and cannot wire it (`scripts/verify.sh` is a
#   single-writer file) used to be able to plant a row for its OWN new check,
#   indistinguishable from a legitimate legacy row. So:
#     * EVERY row carries the issue that will retire it (`#<n>`) AND the 40-hex
#       commit at which the row was added. A row whose sha is missing, is not
#       40-hex, or does not resolve to a real git object is MALFORMED and fails.
#     * a SCRIPT row (the `uninvoked` deferral) whose wiring issue is CLOSED
#       while the artifact is still unwired fails, naming both — a deferral to
#       an open lane is honest, a deferral to a closed one is a permanent
#       excuse. Issue state is read OFFLINE from the committed board snapshot
#       `.board/snapshot.json` (never the network); a tracker ABSENT from the
#       snapshot fails too, because a deferral to an unknown issue cannot be
#       trusted. The `swept-only` SUITE rows are exempt: they record a permanent
#       state (run only by the manifest sweep), not a deferred wiring, so a
#       closed epic that established them does not make them dishonest.
#     * a row for an artifact that did not exist at the baseline's OWN
#       last-touched commit fails — "newly delivered" is now computable.
#       Offline: `git log -1 --format=%H -- scripts/gate-coverage-baseline.txt`
#       names that commit, and `git cat-file -e <sha>:<path>` proves the
#       artifact predates it. A row for an artifact that does not is a planted
#       grandfathered row and is refused, naming the path.
#     * every ACCEPTED script deferral is REPORTED by name (path, tracker) in
#       the output below — never silently accepted.
#   REJECTED ALTERNATIVE: a single mode flag ("allow: manifest-swept suites"),
#   which is far shorter than 74 explicit lines but admits new artifacts
#   silently — the one property this gate exists to deny. Second rejected
#   alternative: demanding everything be wired on day one, which is red on
#   master and therefore ignored.
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed not redefined)
#   0  OK               every artifact is wired or explicitly baselined, and
#                       every baseline entry is live and unique
#   1  NOT-OK           an unwired artifact is not baselined, a baseline entry
#                       is stale / duplicated / malformed / newly delivered, a
#                       script deferral names a closed or unknown wiring issue,
#                       or a scan is refused
#   2  CANNOT-ASSESS    the question cannot be answered (a gate file is missing,
#                       the manifest is missing or declares no suites, python3
#                       or git is unavailable) — never a pass
#
# Offline, deterministic, no network, no containers.
#
# Usage: bash scripts/check-gate-coverage.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-gate-coverage: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

exec python3 - "$root" <<'PY'
"""Gate-coverage detector (#526, provenance #603): fail by name when an artifact is ungated."""
import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()

MANIFEST = "scripts/pytest-suites.txt"
BASELINE = "scripts/gate-coverage-baseline.txt"
SNAPSHOT = ".board/snapshot.json"

# The gate-invocation universe. Explicit by design (see the header comment).
GATE_FILES = (
    "scripts/verify.sh",
    "Makefile",
    "scripts/gate.sh",
    "scripts/merge-gate.sh",
    "scripts/qa-loop.sh",
)

CHECK_GLOB = re.compile(r"^scripts/check-.*\.(sh|py)$")

# The self-wiring marker (#698): scripts/verify.sh sources this helper, which
# auto-discovers every `scripts/check-*.sh`. When a gate file carries the marker,
# a delivered check script is wired by construction (unless it is denylisted).
DISCOVERY_MARKER = "scripts/discover-checks.sh"
DENYLIST = "scripts/check-denylist.txt"

# Closed reason vocabulary. A baseline line may not invent a reason.
REASONS = {"suite": {"swept-only"}, "script": {"uninvoked"}}

REASON_TEXT = {
    "swept-only": "run only by the manifest sweep (scripts/run-pytest-suites.sh); no gate names it",
    "uninvoked": "delivered but invoked by no gate file",
}

# A simple literal assignment (no expansion, no command substitution): the only
# indirection resolved, and only because it is deterministic and offline.
ASSIGN = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)="
    r"(?:\"([^\"$`\\]*)\"|'([^'$`\\]*)'|([^\s;#]+))\s*;?\s*$"
)

TRACKER = re.compile(r"^#\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")


def cannot_assess(message):
    print("check-gate-coverage: CANNOT-ASSESS — %s" % message, file=sys.stderr)
    sys.exit(2)


def read(path):
    return (root / path).read_text(encoding="utf-8", errors="replace")


def run_git(args):
    """Run an offline git command; CANNOT-ASSESS when git cannot even start."""
    try:
        return subprocess.run(["git"] + args, cwd=root,
                              capture_output=True, text=True)
    except OSError as exc:
        cannot_assess("git could not run (%s)" % exc)


def git_object_exists(spec):
    """True when `spec` (a sha, or `<sha>:<path>`) names an existing object."""
    return run_git(["cat-file", "-e", spec]).returncode == 0


def baseline_last_commit():
    """The commit that last touched the baseline — the provenance anchor."""
    proc = run_git(["log", "-1", "--format=%H", "--", BASELINE])
    if proc.returncode != 0 or not proc.stdout.strip():
        cannot_assess("git log cannot name the last commit that touched %s"
                      % BASELINE)
    return proc.stdout.strip().splitlines()[0]


def tracker_states():
    """Issue number -> state, read OFFLINE from the committed board snapshot."""
    snap = root / SNAPSHOT
    if not snap.is_file():
        cannot_assess("board snapshot %s is missing -- the closed-wiring-issue "
                      "rule cannot run offline without it" % SNAPSHOT)
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
    except ValueError as exc:
        cannot_assess("board snapshot %s is not valid JSON (%s)"
                      % (SNAPSHOT, exc))
    issues = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        cannot_assess("board snapshot %s has no 'issues' list" % SNAPSHOT)
    states = {}
    for entry in issues:
        if not isinstance(entry, dict) or entry.get("number") is None:
            continue
        try:
            number = int(entry["number"])
        except (TypeError, ValueError):
            continue
        states[number] = str(entry.get("state") or "").strip().lower()
    return states


def logical_lines(text):
    """Yield (first_lineno, logical_line) with backslash continuations joined."""
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        first = index + 1
        buffer = lines[index]
        while buffer.rstrip().endswith("\\") and index + 1 < len(lines):
            index += 1
            buffer = buffer.rstrip()[:-1] + " " + lines[index]
        yield first, buffer
        index += 1


def literal_vars(text):
    """Literal VAR=<path> assignments in a file (no expansion, no substitution)."""
    found = {}
    for _, line in logical_lines(text):
        match = ASSIGN.match(line)
        if not match:
            continue
        value = match.group(2) or match.group(3) or match.group(4)
        if value:
            found[match.group(1)] = value
    return found


def expand(line, variables):
    def repl(match):
        return variables.get(match.group(1) or match.group(2), match.group(0))

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                  repl, line)


def pytest_tokens(text):
    """Path arguments of pytest invocations in `text` (expanded, de-quoted)."""
    variables = literal_vars(text)
    tokens = []
    for _, raw in logical_lines(text):
        line = raw
        if line.lstrip().startswith("#"):
            continue
        line = re.sub(r"(?<=\s)#.*$", "", line)
        if not re.search(r"\bpytest\b", line):
            continue
        for token in re.split(r"[\s]+", expand(line, variables)):
            token = token.strip("\"'`();,")
            if "/" in token:
                tokens.append(token)
    return tokens


def discover_check_scripts():
    """Delivered `scripts/check-*.sh|py`, discovered on the FILESYSTEM.

    `git ls-files` would be a false green here: a check script a lane has just
    delivered but not yet committed is untracked, hence invisible, hence never
    reported. Scanning the working tree catches it at once. Paths git ignores
    are excluded -- an ignored file is not a delivered artifact. (This is why
    the detector still needs git: `git check-ignore` is the exclusion oracle,
    run once, offline, over a fixed candidate list.)
    """
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        cannot_assess("scripts/ directory is missing")
    paths = [
        "scripts/%s" % entry.name
        for entry in sorted(scripts_dir.iterdir())
        if entry.is_file() and CHECK_GLOB.match("scripts/%s" % entry.name)
    ]
    if not paths:
        return paths
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=root, input="\n".join(paths) + "\n",
            capture_output=True, text=True,
        )
    except OSError as exc:
        cannot_assess("git check-ignore could not run (%s)" % exc)
    if proc.returncode not in (0, 1):
        cannot_assess("git check-ignore exited %d -- not a git work tree?" % proc.returncode)
    ignored = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return [path for path in paths if path not in ignored]


def read_denylist():
    """Check names/basenames disabled by name (scripts/check-denylist.txt, #698)."""
    path = root / DENYLIST
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line)
    return names


def self_check():
    """Negative controls: the rule must not read coverage out of prose.

    Both directions are asserted on synthetic input, so a regression in the
    matcher fails the gate instead of quietly manufacturing WIRED (a hidden
    finding) or inventing UNWIRED (a false failure).
    """
    problems = []
    prose = "# the suite lives at zz-probe/tests and is run by python3 -m pytest zz-probe/tests\n"
    if "zz-probe/tests" in pytest_tokens(prose):
        problems.append("a pytest target named only in a comment was read as coverage")
    emitted = 'echo "== pytest (zz-probe/tests) =="\n'
    if "zz-probe/tests" not in pytest_tokens(emitted):
        problems.append("a pytest target a check script names was NOT read as coverage")
    usage = "Usage: scripts/verify.sh [verify|gate]\n"
    if any(token.startswith("scripts/verify.sh/") for token in pytest_tokens(usage)):
        problems.append("a non-pytest usage line was read as coverage")
    indirection = 'suite_dir="zz-probe"\npython3 -m pytest -q "$suite_dir/tests"\n'
    if "zz-probe/tests" not in pytest_tokens(indirection):
        problems.append("a literal $VAR pytest target was NOT resolved")
    return problems


def main():
    gate_text = {}
    for name in GATE_FILES:
        if not (root / name).is_file():
            cannot_assess("gate file %s is missing -- the invocation universe is incomplete" % name)
        gate_text[name] = read(name)

    # --- class 1: delivered check scripts ------------------------------------
    checks = discover_check_scripts()
    wired_checks = {
        path for path in checks
        if any(path in text for text in gate_text.values())
    }
    # #698 self-wiring: when a gate file carries the discovery marker,
    # scripts/verify.sh auto-discovers every `scripts/check-*.sh`, so a delivered
    # check script is wired by construction. A denylisted one (disabled by name
    # in scripts/check-denylist.txt) is NOT auto-wired: it must be invoked by
    # some other gate file, or it is refused as an unwired artifact below.
    if any(DISCOVERY_MARKER in text for text in gate_text.values()):
        denylist = read_denylist()
        for path in checks:
            if not path.endswith(".sh"):
                continue
            base = path.rsplit("/", 1)[-1]
            name = base[len("check-"):-len(".sh")]
            if name in denylist or base in denylist:
                continue
            wired_checks.add(path)
    unwired_checks = [path for path in checks if path not in wired_checks]

    # --- class 2: declared pytest suites -------------------------------------
    if not (root / MANIFEST).is_file():
        cannot_assess("suite manifest %s is missing (no declared suites)" % MANIFEST)
    suites = [
        line.strip()
        for line in read(MANIFEST).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not suites:
        cannot_assess("suite manifest %s declares no suites" % MANIFEST)

    surface = dict(gate_text)
    for path in sorted(wired_checks):
        surface[path] = read(path)
    tokens = set()
    for text in surface.values():
        tokens.update(pytest_tokens(text))

    wired_suites = {
        suite for suite in suites
        if suite in tokens or "%s/tests" % suite in tokens
    }
    unwired_suites = [suite for suite in suites if suite not in wired_suites]

    # --- the baseline --------------------------------------------------------
    entries = []
    malformed = []
    sha_cache = {}
    if (root / BASELINE).is_file():
        for lineno, raw in enumerate(read(BASELINE).splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                malformed.append("%s:%d (expected 5 tab-separated fields — kind, "
                                 "path, reason, tracker, sha — found %d)"
                                 % (BASELINE, lineno, len(parts)))
                continue
            kind, path, reason, tracker, sha = (part.strip() for part in parts)
            if kind not in REASONS:
                malformed.append("%s:%d (unknown kind %r)" % (BASELINE, lineno, kind))
                continue
            if reason not in REASONS[kind]:
                malformed.append("%s:%d (reason %r is not in the closed set %s for kind %s)"
                                 % (BASELINE, lineno, reason,
                                    "/".join(sorted(REASONS[kind])), kind))
                continue
            if not TRACKER.match(tracker):
                malformed.append("%s:%d (%s: tracker %r is not a #<issue> wiring "
                                 "reference)" % (BASELINE, lineno, path, tracker))
                continue
            if not SHA.match(sha):
                malformed.append("%s:%d (%s: sha %r is not a 40-hex commit "
                                 "reference)" % (BASELINE, lineno, path, sha))
                continue
            if sha not in sha_cache:
                sha_cache[sha] = git_object_exists(sha)
            if not sha_cache[sha]:
                malformed.append("%s:%d (%s: sha %r does not resolve to an "
                                 "object in this repository)"
                                 % (BASELINE, lineno, path, sha))
                continue
            entries.append((kind, path, reason, tracker, sha))
    else:
        print("check-gate-coverage: NOTE — %s is absent; no exception is accepted"
              % BASELINE)

    baselined_suites = {path for kind, path, _, _, _ in entries if kind == "suite"}
    baselined_checks = {path for kind, path, _, _, _ in entries if kind == "script"}
    entry_keys = [(kind, path) for kind, path, _, _, _ in entries]
    duplicates = sorted({key for key in entry_keys if entry_keys.count(key) > 1})

    # --- findings ------------------------------------------------------------
    findings = ["self-check: %s" % problem for problem in self_check()]
    for path in unwired_checks:
        if path not in baselined_checks:
            findings.append("script %s (invoked by no gate file: %s)"
                            % (path, ", ".join(GATE_FILES)))
    for suite in unwired_suites:
        if suite not in baselined_suites:
            findings.append("suite %s (no gate file or invoked check names it as a pytest target)"
                            % suite)
    for kind, path in duplicates:
        findings.append("baseline %s %s (duplicate entry)" % (kind, path))
    for path in sorted(baselined_checks):
        if not (root / path).is_file():
            findings.append("baseline script %s (stale: no such delivered check script)" % path)
        elif path in wired_checks:
            findings.append("baseline script %s (stale: a gate invokes it now — remove the entry)" % path)
    for suite in sorted(baselined_suites):
        if suite not in suites:
            findings.append("baseline suite %s (stale: no longer declared in %s)" % (suite, MANIFEST))
        elif suite in wired_suites:
            findings.append("baseline suite %s (stale: a gate names it now — remove the entry)" % suite)

    # --- provenance (#603): a deferral must be accountable -------------------
    if entries:
        live_scripts = [row for row in entries
                        if row[0] == "script" and row[1] in unwired_checks]
        states = tracker_states() if live_scripts else {}
        base_sha = baseline_last_commit()
        for kind, path, reason, tracker, sha in entries:
            if kind == "script" and path in unwired_checks:
                issue = int(tracker[1:])
                state = states.get(issue)
                if state is None:
                    findings.append(
                        "baseline script %s (%s: wiring issue #%d is absent from "
                        "the board snapshot — a deferral to an unknown issue "
                        "cannot be trusted)" % (path, tracker, issue))
                elif state != "open":
                    findings.append(
                        "baseline script %s (wiring issue #%d is %s while the "
                        "artifact is still unwired — a deferral to a closed "
                        "issue is a permanent excuse)" % (path, issue, state.upper()))
            if kind == "script" and not (root / path).is_file():
                continue  # stale: no such delivered check script (reported above)
            if kind == "suite" and path not in suites:
                continue  # stale: no longer declared (reported above)
            if not git_object_exists("%s:%s" % (base_sha, path)):
                findings.append(
                    "baseline %s %s (newly delivered: %s did not exist at %s, "
                    "the baseline's last-touched commit — a row for a new "
                    "artifact is never grandfathered)"
                    % (kind, path, path, base_sha[:12]))

    unlisted_checks = [p for p in unwired_checks if p not in baselined_checks]
    unlisted_suites = [s for s in unwired_suites if s not in baselined_suites]

    print("check-gate-coverage: universe = %s" % ", ".join(GATE_FILES))
    print("check-gate-coverage: check scripts WIRED=%d UNWIRED=%d (baselined %d)"
          % (len(wired_checks), len(unwired_checks),
             len(unwired_checks) - len(unlisted_checks)))
    print("check-gate-coverage: suites declared=%d WIRED=%d UNWIRED=%d (baselined %d)"
          % (len(suites), len(wired_suites), len(unwired_suites),
             len(unwired_suites) - len(unlisted_suites)))
    print("check-gate-coverage: baseline entries=%d" % len(entries))
    for kind, path, _, tracker, _ in entries:
        if kind == "script" and path in unwired_checks:
            print("check-gate-coverage: deferral script %s -> %s (unwired; the "
                  "named issue retires this row by wiring it)" % (path, tracker))

    if malformed:
        findings.extend("baseline line %s" % item for item in malformed)

    if findings:
        for finding in findings:
            print("  FAIL  %s" % finding, file=sys.stderr)
        print("check-gate-coverage: NOT-OK — %d finding(s)" % len(findings), file=sys.stderr)
        return 1

    print("check-gate-coverage: OK — every unwired artifact is explicitly baselined "
          "with a live, unique, reasoned, provenanced entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
