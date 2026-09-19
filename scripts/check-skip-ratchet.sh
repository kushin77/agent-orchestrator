#!/usr/bin/env bash
# check-skip-ratchet.sh -- the gate for the composite's skip ratchet (issue #1199).
#
# THE CLASS THIS EXISTS FOR
#   `scripts/verify.sh` records a check that answers rc 2 CANNOT-ASSESS as SKIP
#   and still printed `verify: PASS (... N skipped: <names>)`. Naming the names
#   is honest, and it is not enough: the SAME names can sit in that bucket on
#   every run, so a check that can never assess is indistinguishable, in the
#   verdict line, from one that assessed and passed. Measured on pristine
#   `origin/master` (`04ad55a`), the bucket held exactly the two checks that
#   could not reach a verdict, and each was hiding a filed defect it could not
#   report (`check-dispatch-queue` -> #1189, `check-paperclip-routines` ->
#   #1176) while the composite read PASS.
#
# WHAT IS PROVEN HERE (and why each half is needed)
#   1. THE RULES CAN FAIL. `scripts/lib/skip-ratchet.py --self-test` drives one
#      fixture per rule and asserts BOTH the exit code and the actual refusal
#      line: an unnamed skip is refused; a `standing-gap` entry goes stale the
#      moment its check assesses; a `venue` entry whose precondition IS present
#      while the check still cannot assess is refused as a defect of the CHECK
#      (#1176's argument); a `venue` precondition is MEASURED in BOTH forms (a
#      repo-relative path, and a command the venue must be able to run -- the
#      Cloud Build shape, #1361), so a supplied command is refused exactly as a
#      supplied path is; a command-shaped declaration, a precondition of neither
#      form, an entry naming a check that was not discovered, a missing record
#      (fail-closed) and a malformed one (CANNOT-ASSESS) are each refused by
#      name -- never a silent "no exemptions needed", which is how a control
#      turns into a formality (GR-12).
#   2. THE RECORD IS REAL. The committed `scripts/skip-budget.json` is loaded
#      through the SAME loader the run uses (no second copy of the rule), and
#      every entry must name a check this tree actually discovers -- an
#      exemption for a check that does not exist is stale by name. Every
#      `{command: ...}` precondition must additionally be a command THIS TREE
#      RUNS: the record may not invent a probe nothing else measures, and a
#      planted phantom probe is refused by name so that rule cannot match
#      nothing.
#   3. THE RATCHET IS WIRED, NOT INERT (#1164's class: a detector nothing calls
#      is advisory). The gate asserts that `scripts/verify.sh` INVOKES the
#      ratchet, consumes its exit code as a failure, carries its record into
#      `.verify/attestation.json`, and rides its note in the verdict line's own
#      parentheses. The negative control is a MUTATION: the block carrying the
#      invocation is stripped from a COPY of verify.sh and the same assertion
#      must then fail, naming what is missing.
#   4. THE RECORD CANNOT UNDER-REPORT. A fabricated attestation whose SKIP is
#      accounted for by nothing must be refused by
#      `scripts/lib/validate-attestation.py`; a well-formed one must pass, and an
#      attestation carrying a SKIP with no ratchet record at all must be refused
#      -- so the refusals are attributable rather than blanket.
#
# This check is auto-discovered by `scripts/discover-checks.sh` (a new
# `scripts/check-*.sh` is wired the moment it lands), so it needs no hand-edit to
# `scripts/verify.sh`'s array.
#
# Usage:
#   bash scripts/check-skip-ratchet.sh                # self-test + this tree
#   bash scripts/check-skip-ratchet.sh --self-test    # the provocations alone
#   bash scripts/check-skip-ratchet.sh --root DIR     # assert ANOTHER tree
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
self_test_only=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/check-skip-ratchet.sh                # self-test + this tree
  bash scripts/check-skip-ratchet.sh --self-test    # the provocations alone
  bash scripts/check-skip-ratchet.sh --root DIR     # assert ANOTHER tree
Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --self-test) self_test_only=1; shift ;;
    --root)
      root="${2:-}"
      if [ -z "$root" ]; then
        echo "check-skip-ratchet: CANNOT-ASSESS -- --root needs a directory" >&2
        exit 2
      fi
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

lib="$root/scripts/lib/skip-ratchet.py"
budget="$root/scripts/skip-budget.json"
verify_sh="$root/scripts/verify.sh"

if [ ! -f "$lib" ]; then
  echo "check-skip-ratchet: CANNOT-ASSESS -- $lib is missing (nothing to assert against)" >&2
  exit 2
fi

# All scratch lives OUTSIDE the tree (this gate must leave the tree byte-clean)
# and carries an explicit /tmp/<name>. template: mktemp's own default lands in
# the shared, periodically-cleaned TMPDIR and can vanish mid-run (SP-9).
work="$(mktemp -d /tmp/ao-skip-ratchet."$(printf '%s' XXXXXXXXXX)")"
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

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

# --- 1. the rules can fail ----------------------------------------------------
echo "== the rules, provoked (the ratchet's own fixtures) =="
self_rc=0
python3 "$lib" --self-test > "$work/self-test.txt" 2>&1 || self_rc=$?
cat "$work/self-test.txt"
check "every rule is provoked and every refusal line is asserted" \
  "$([ "$self_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the provoked battery exited $self_rc"
if [ "$self_rc" -ne 0 ]; then
  echo "check-skip-ratchet: NOT-OK -- a rule of the ratchet is not provokable" >&2
  exit 1
fi

if [ "$self_test_only" -eq 1 ]; then
  echo "check-skip-ratchet: OK -- the ratchet's rules are provable (--self-test only)"
  exit 0
fi

# --- 2. the committed record is real -----------------------------------------
echo "== the committed record (scripts/skip-budget.json) =="
record_rc=0
python3 - "$root" "$budget" > "$work/record.txt" 2>&1 <<'PY' || record_rc=$?
"""Load the committed budget through the RUN's own loader, and require every
entry to name a check this tree discovers.

Reusing `load_budget` is the point: a second copy of the rule here could disagree
with the rule the run applies, and this gate would then be asserting a fiction.
"""
import importlib.util
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
budget_path = Path(sys.argv[2])

spec = importlib.util.spec_from_file_location(
    "skip_ratchet", root / "scripts" / "lib" / "skip-ratchet.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

findings = []
if not budget_path.is_file():
    findings.append(
        "scripts/skip-budget.json is missing (a missing record means no exemptions, "
        "so every skip is refused)"
    )
    entries = []
else:
    entries, shape = module.load_budget(budget_path)
    findings.extend(shape)

# The tree's discovered check names: the explicit array in scripts/verify.sh plus
# every `scripts/check-*.sh` the discovery layer would wire. The denylist is
# excluded: a denylisted check never runs, so an entry for it is stale.
verify_text = (root / "scripts" / "verify.sh").read_text(encoding="utf-8")
array_names = set(re.findall(r"^\s*'([A-Za-z0-9._-]+)\|", verify_text, re.M))
denylist = set()
denylist_path = root / "scripts" / "check-denylist.txt"
if denylist_path.is_file():
    for line in denylist_path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if item and not item.startswith("#"):
            denylist.add(item)
glob_names = set()
for script in sorted((root / "scripts").glob("check-*.sh")):
    name = script.name[len("check-"):-len(".sh")]
    if name in denylist or script.name in denylist:
        continue
    glob_names.add(name)
discovered = array_names | glob_names

def findings_for(entries):
    """The rule as a function, so the committed record AND a planted violation go
    through exactly the same code: with an EMPTY record this assertion would
    otherwise be vacuously true, which is the formality this repository rejects.
    """
    out = []
    for entry in entries:
        name = entry.get("check")
        if not name:
            continue
        if name not in discovered:
            out.append("entry '%s' names a check this tree does not discover" % name)
        if name in denylist:
            out.append(
                "entry '%s' names a DENYLISTED check (it never runs, so the entry is stale)"
                % name
            )
    return out


findings.extend(findings_for(entries))
planted = [
    {"check": "no-such-check-1199", "kind": "standing-gap", "issue": 1199, "reason": "planted"}
]
if not findings_for(planted):
    findings.append(
        "the planted entry 'no-such-check-1199' was ACCEPTED -- this rule matches nothing, "
        "so it would accept an exemption for a check that does not exist"
    )

# A `{"command": ...}` venue precondition is a MEASUREMENT of the venue, so it
# must be a command THIS REPOSITORY ALREADY RUNS -- the record may not invent a
# probe nobody else measures. Ratified against the tree, EXCLUDING the ratchet's
# own files: a rule that read the record, or the ratchet's own source, would
# certify itself, and a borrow that reads its own values can never fail.
RATCHET_FILES = (
    "scripts/skip-budget.json",
    "scripts/lib/skip-ratchet.py",
    "scripts/check-skip-ratchet.sh",
)


def tree_texts():
    """Every readable file under scripts/, minus the ratchet's own three."""
    texts = {}
    for path in sorted((root / "scripts").rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        if relative in RATCHET_FILES:
            continue
        try:
            if path.stat().st_size > 4_000_000:
                continue
            texts[relative] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return texts


def command_findings(candidate_entries, texts):
    """Declared venue command preconditions that no file under scripts/ runs."""
    out = []
    for entry in candidate_entries:
        if entry.get("kind") != "venue" or not isinstance(
            entry.get("precondition"), dict
        ):
            continue
        command = entry["precondition"].get("command")
        if not any(command and command in text for text in texts.values()):
            out.append(
                "entry '%s' declares the venue command precondition %r, which NO "
                "file under scripts/ runs -- a precondition the record invents "
                "cannot be measured by anything" % (entry.get("check"), command)
            )
    return out


texts = tree_texts()
findings.extend(command_findings(entries, texts))
planted_command = [
    {
        "check": "branch-protection",
        "kind": "venue",
        "issue": 1361,
        "precondition": {"command": "ao-phantom-probe-1361 --nope"},
        "reason": "planted",
    }
]
if not command_findings(planted_command, texts):
    findings.append(
        "the planted venue command precondition 'ao-phantom-probe-1361 --nope' was "
        "ACCEPTED -- this rule matches nothing, so any invented probe would pass"
    )

for finding in findings:
    print("  FAIL  %s" % finding)
if findings:
    raise SystemExit(1)
print(
    "  OK    %d committed entry(ies), every one naming a discovered check, and the "
    "planted entry for a non-existent check is refused by name" % len(entries)
)
print(
    "  OK    %d venue command precondition(s), each one a command this tree runs, "
    "and the planted phantom probe is refused by name"
    % len(
        [
            e
            for e in entries
            if e.get("kind") == "venue" and isinstance(e.get("precondition"), dict)
        ]
    )
)
for entry in entries:
    declared = entry.get("precondition")
    if isinstance(declared, dict):
        declared = "command: %s" % declared.get("command")
    print(
        "        %s [%s] %s  (issue #%s)"
        % (
            entry["check"],
            entry["kind"],
            declared,
            entry.get("issue"),
        )
    )
PY
cat "$work/record.txt"
check "the committed record loads through the run's own loader and names real checks" \
  "$([ "$record_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the record assertion exited $record_rc"

# --- 3. the ratchet is wired into the composite -------------------------------
echo "== the wiring in scripts/verify.sh (a detector nothing calls is advisory) =="
cat > "$work/assert-wiring.py" <<'PY'
"""Assert the things that make the ratchet bite, each by name.

Structural, not prose: a mention inside a comment must not satisfy it, so the
needles are asserted against the CODE of the file (whole-line comments stripped).
The needle set is the ratchet's whole contract with the composite -- it is
invoked, it is handed the run's own check names and the committed record, its
exit code FAILS the run, its record rides in the attestation and its note rides
in the verdict line's parentheses.
"""
import re
import sys
from pathlib import Path

target = Path(sys.argv[1])
if not target.is_file():
    print("  FAIL  %s is missing" % target)
    raise SystemExit(1)

raw = target.read_text(encoding="utf-8")
code = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))

NEEDLES = (
    ("the ratchet is INVOKED", r"scripts/lib/skip-ratchet\.py"),
    ("the run's own check names are handed to it", r'--names\s+"\$check_names_file"'),
    ("the committed record is the budget", r'--budget\s+"[^"]*scripts/skip-budget\.json"'),
    ("its record and note are consumed", r"--json-out[\s\S]{0,200}--note-out"),
    ("a non-zero ratchet exit FAILS the run", r'if \[ "\$ratchet_rc" -ne 0 \]; then'),
    ("the record rides in the attestation", r'"skip_ratchet":\s*ratchet'),
    ("the note rides in the verdict line", r'skip_note="\$\{skip_note\}\$\{ratchet_note\}"'),
)
findings = []
for label, needle in NEEDLES:
    if not re.search(needle, code):
        findings.append("%s (no match for %s)" % (label, needle))

# The exit code must be consumed on the FAILURE path, not merely compared: the
# block that tests it has to set overall=1.
block = re.search(r'if \[ "\$ratchet_rc" -ne 0 \]; then([\s\S]*?)\nfi\n', code)
if block is None:
    findings.append("the ratchet exit code is not tested in its own block")
elif "overall=1" not in block.group(1):
    findings.append("the ratchet's non-zero exit does not set overall=1")

for finding in findings:
    print("  FAIL  %s" % finding)
if findings:
    raise SystemExit(1)
print(
    "  OK    invoked, handed the run's check names and the committed record, its "
    "exit code fails the run, its record reaches the attestation and its note "
    "reaches the verdict line"
)
PY

wiring_rc=0
python3 "$work/assert-wiring.py" "$verify_sh" > "$work/wiring.txt" 2>&1 || wiring_rc=$?
cat "$work/wiring.txt"
check "verify.sh invokes the ratchet and consumes its verdict" \
  "$([ "$wiring_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the wiring assertion exited $wiring_rc"

# The negative control: strip the block from a COPY and require the SAME
# assertion to fail. Without it, the assertion above could be satisfied by a file
# that merely mentions the ratchet somewhere.
mkdir -p "$work/mutant/scripts"
cp "$verify_sh" "$work/mutant/scripts/verify.sh"
mutate_rc=0
python3 - "$work/mutant/scripts/verify.sh" > "$work/mutate.txt" 2>&1 <<'PY' || mutate_rc=$?
import sys
from pathlib import Path

target = Path(sys.argv[1])
text = target.read_text(encoding="utf-8")
start_marker = "# --- skip ratchet (issue #1199)"
end_marker = 'ratchet_note="$(cat "$ratchet_note_file")"\n'
start = text.find(start_marker)
end = text.find(end_marker)
if start < 0 or end < 0:
    print(
        "  FAIL  the mutation's anchor moved: the ratchet block is not where this "
        "control expects it, so the control would prove nothing"
    )
    raise SystemExit(1)
mutated = text[:start] + text[end + len(end_marker):]
target.write_text(mutated, encoding="utf-8")
print("  OK    mutant written: %d byte(s) of the ratchet block removed" % (len(text) - len(mutated)))
PY
cat "$work/mutate.txt"
mutant_wiring_rc=0
python3 "$work/assert-wiring.py" "$work/mutant/scripts/verify.sh" > "$work/mutant-wiring.txt" 2>&1 || mutant_wiring_rc=$?
sed 's/^/      /' "$work/mutant-wiring.txt"
if [ "$mutate_rc" -ne 0 ]; then
  check "a verify.sh without the invocation is refused by the wiring assertion" 1 \
    "the mutation itself failed, so the control proved nothing"
elif [ "$mutant_wiring_rc" -eq 0 ]; then
  check "a verify.sh without the invocation is refused by the wiring assertion" 1 \
    "the mutant PASSED the wiring assertion, so the assertion has no failing path"
else
  check "a verify.sh without the invocation is refused by the wiring assertion" 0 ""
fi

# --- 4. the record cannot under-report ---------------------------------------
echo "== the attestation record (a skip that nothing accounts for is refused) =="
acct_rc=0
python3 - "$root" > "$work/accounting.txt" 2>&1 <<'PY' || acct_rc=$?
"""Three fixtures through the attestation validator: one that accounts for its
SKIP (must pass), one whose SKIP is recorded as unbudgeted (must be refused by
name), and one carrying a SKIP with no ratchet record at all (must be refused)."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
validator = root / "scripts" / "lib" / "validate-attestation.py"
schema = root / "governance" / "isolation" / "attestation.schema.json"

base = {
    "run_id": "run-1",
    "git_sha": "a" * 40,
    "overall_verdict": "WARN",
    "checks": [
        {
            "name": "a",
            "rc": 0,
            "status": "PASS",
            "verdict": "OK",
            "duration": 1.0,
            "evidence_tail": "",
        },
        {
            "name": "b",
            "rc": 2,
            "status": "SKIP",
            "verdict": "WARN",
            "duration": 1.0,
            "evidence_tail": "",
        },
    ],
    "skipped": 1,
    "skipped_checks": ["b"],
}
accounted = dict(base)
accounted["skip_ratchet"] = {
    "budget": "scripts/skip-budget.json",
    "budget_entries": 1,
    "verdict": "OK",
    "standing_skips": [
        {
            "check": "b",
            "kind": "venue",
            "precondition": "vendor/CMR/sync",
            "issue": None,
            "reason": "x",
        }
    ],
    "unbudgeted_skips": [],
    "stale_entries": [],
    "unused_venue_entries": [],
    "findings": [],
}
unaccounted = dict(base)
unaccounted["skip_ratchet"] = {
    "budget": "scripts/skip-budget.json",
    "budget_entries": 0,
    "verdict": "VIOLATION",
    "standing_skips": [],
    "unbudgeted_skips": ["b"],
    "stale_entries": [],
    "unused_venue_entries": [],
    "findings": ["unnamed skip 'b'"],
}
ratchet_absent = dict(base)

# The FABRICATION this validator exists to refuse (#882's class): the same
# unbudgeted skip, but the record claims its own verdict is OK.
fabricated = dict(base)
fabricated["skip_ratchet"] = dict(
    unaccounted["skip_ratchet"], verdict="OK", findings=[]
)

# A skip that NEITHER list mentions, while the record claims OK: the record
# under-reports by naming a different check entirely.
underreported = dict(base)
underreported["skip_ratchet"] = {
    "budget": "scripts/skip-budget.json",
    "budget_entries": 1,
    "verdict": "OK",
    "standing_skips": [{"check": "c", "kind": "venue", "precondition": "x"}],
    "unbudgeted_skips": [],
    "stale_entries": [],
    "unused_venue_entries": [],
    "findings": [],
}

# A `venue` standing skip that declares NO precondition: the shape the ratchet
# itself cannot write (its loader refuses such an entry), so a record carrying
# one was not produced by the run it claims to describe.
venue_without_precondition = dict(base)
venue_without_precondition["skip_ratchet"] = {
    "budget": "scripts/skip-budget.json",
    "budget_entries": 1,
    "verdict": "OK",
    "standing_skips": [{"check": "b", "kind": "venue"}],
    "unbudgeted_skips": [],
    "stale_entries": [],
    "unused_venue_entries": [],
    "findings": [],
}

scratch = Path(tempfile.mkdtemp(prefix="ao1199-skip-ratchet-acct.", dir="/tmp"))
findings = []


def run(doc, label):
    path = scratch / ("%s.json" % label)
    path.write_text(json.dumps(doc), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(validator), str(path), str(schema)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def tail(text):
    lines = text.splitlines()
    return lines[-1] if lines else "(no output)"


for doc, label in ((accounted, "accounted"), (unaccounted, "unbudgeted-but-honest")):
    rc, out = run(doc, label)
    if rc != 0:
        findings.append("an HONEST record was refused by the validator (%s): %s" % (label, tail(out)))

for doc, label, needle in (
    (ratchet_absent, "ratchet-absent", "skip-ratchet-missing"),
    (fabricated, "fabricated-ok", "verdict-ok-while-carrying-findings"),
    (underreported, "underreported", "skip-not-accounted"),
    (
        venue_without_precondition,
        "venue-with-no-precondition",
        "venue-skip-with-no-precondition",
    ),
):
    rc, out = run(doc, label)
    if rc == 0:
        findings.append("the %s fixture PASSED the validator (a false green)" % label)
    elif needle not in out:
        findings.append("the %s fixture was refused for the wrong reason: %s" % (label, tail(out)))

if findings:
    for finding in findings:
        print("  FAIL  %s" % finding)
    print("        fixtures kept in %s" % scratch)
    raise SystemExit(1)
shutil.rmtree(scratch, ignore_errors=True)
print(
    "  OK    an accounted skip and an honestly-red unbudgeted skip both pass, while a "
    "missing record, a fabricated OK, an under-reported skip and a venue skip that "
    "declares no precondition are each refused by name"
)
PY
cat "$work/accounting.txt"
check "the attestation record cannot under-report a skip" \
  "$([ "$acct_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the accounting assertion exited $acct_rc"

# --- 5. the composite, end to end on a shim ----------------------------------
# Sections 1-4 prove the RULES, the RECORD, the WIRING and the ATTESTATION. They
# would all still hold if `scripts/verify.sh` forgot to render the note, or if
# its verdict line swallowed the rail -- so the composite itself is run here, for
# real, on a shim carrying byte-identical copies of the orchestrator files, with
# its explicit `checks=()` array neutered so ONLY the fixture checks run (the
# discovery layer then wires exactly those). Four scenarios, each asserted on the
# REAL exit code, the REAL verdict line and the REAL `.verify/attestation.json`:
#   * no skip            -> `verify: PASS`, no skip talk at all, ratchet OK;
#   * a NAMED venue skip -> `verify: PASS` NAMING the standing skip, rc 0, and
#                           the skip inside attestation.json;
#   * an UNNAMED skip    -> rc 1, refused by name, with the remedy;
#   * a STALE exemption  -> rc 1 the moment the check assesses, by name;
#   * a venue COMMAND precondition the venue LACKS -> `verify: PASS` naming it, rc
#                          0, the shape the Cloud Build container produces (#1361);
#   * the SAME entry once the venue SUPPLIES that command -> rc 1, refused by name
#                          (without this arm the guard would be free to pass
#                          whatever the venue supplies -- the fail-open direction).
# The gate permit store is a private fixture dir: this is a control, not a
# competing gate (the box-wide cap is proved by scripts/check-gate-lock.sh).
echo "== the composite, end to end (fixture checks, the real scripts/verify.sh) =="
shim_rc=0
python3 - "$root" "$work" > "$work/shim.txt" 2>&1 <<'PY' || shim_rc=$?
"""Run the REAL scripts/verify.sh six times on a shim with a fixture check set."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
shim = work / "shim"

# Byte-identical copies of the orchestrator surface verify.sh needs, exactly as
# scripts/check-gate-lock.sh mounts its own scratch worktree.
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
FIXTURE_A = "skip-ratchet-fixture-a"
FIXTURE_B = "skip-ratchet-fixture-b"

failures = []


def mount(fixture_rcs):
    shutil.rmtree(shim, ignore_errors=True)
    for relative in ORCHESTRATOR:
        target = shim / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    verify = shim / "scripts" / "verify.sh"
    text = verify.read_text(encoding="utf-8")
    start = text.index("\nchecks=(\n")
    end = text.index("\n)\n", start)
    verify.write_text(
        text[:start] + "\nchecks=()\n" + text[end + 3:], encoding="utf-8"
    )
    for name, rc in fixture_rcs:
        (shim / "scripts" / ("check-%s.sh" % name)).write_text(
            "#!/usr/bin/env bash\nset -u\necho 'check-%s: fixture, rc %d'\nexit %d\n"
            % (name, rc, rc),
            encoding="utf-8",
        )


def budget(entries):
    path = shim / "scripts" / "skip-budget.json"
    if entries is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(
        json.dumps({"schema": "ao.verify.skip-budget/v1", "entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )


def run(label):
    env = dict(os.environ)
    env["AO_GATE_LOCK_ROOT"] = str(work / "permits")
    env["AO_GATE_MAX_CONCURRENT"] = "1"
    env["AO_AGENT_ID"] = "skip-ratchet-fixture"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        ["bash", "scripts/verify.sh", "verify"],
        cwd=str(shim),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    text = proc.stdout + proc.stderr
    attestation = {}
    att_path = shim / ".verify" / "attestation.json"
    if att_path.is_file():
        attestation = json.loads(att_path.read_text(encoding="utf-8"))
    verdict = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("verify: PASS") or line.startswith("verify: FAIL")
    ]
    return proc.returncode, text, attestation, verdict


def expect(label, got_rc, want_rc, text, needles, attestation, att_needles):
    problems = []
    if got_rc != want_rc:
        problems.append("rc %d != %d" % (got_rc, want_rc))
    for needle in needles:
        if needle not in text:
            problems.append("no line contains %r" % needle)
    for path, want in att_needles:
        node = attestation
        for key in path:
            if isinstance(node, dict):
                node = node.get(key)
            elif isinstance(node, list) and isinstance(key, int) and key < len(node):
                node = node[key]
            else:
                node = None
        if node != want:
            problems.append(
                "attestation %s is %r, wanted %r"
                % (".".join(str(key) for key in path), node, want)
            )
    if problems:
        failures.append((label, problems))
        print("  FAIL  %s: %s" % (label, "; ".join(problems)))
        for line in text.splitlines():
            if "skip ratchet" in line or line.startswith("verify: "):
                print("          actual: %s" % line[:150])
    else:
        print("  OK    %s -> rc %d" % (label, got_rc))
    return not problems


# 1. every fixture assesses: the composite must read exactly as it always did.
mount(((FIXTURE_A, 0), (FIXTURE_B, 0)))
budget([])
rc, text, att, verdict = run("clean")
expect(
    "no skip -- the verdict line carries no skip talk at all",
    rc, 0, text,
    ["verify: PASS (2 of 2 checks)"],
    att, [(("skip_ratchet", "verdict"), "OK"), (("skipped",), 0)],
)
if not verdict or "skipped" in verdict[0]:
    failures.append(("clean verdict line", ["the verdict line talks about skips: %s" % verdict]))

# 2. a NAMED venue skip: PASS, but the standing skip is named in the line and
#    recorded. The precondition is the committed record's own -- absent here.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([{"check": FIXTURE_B, "kind": "venue", "precondition": "vendor/CMR/sync", "reason": "fixture"}])
rc, text, att, verdict = run("named venue skip")
expect(
    "a NAMED venue skip is a PASS that names the standing skip",
    rc, 0, text,
    [
        "verify: PASS (1 of 2 checks, 1 skipped: %s" % FIXTURE_B,
        "1 standing skip(s) named in scripts/skip-budget.json",
        "verify: standing skip %s -- venue: vendor/CMR/sync absent" % FIXTURE_B,
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "OK"),
        (("skipped",), 1),
        (("skip_ratchet", "standing_skips", 0, "check"), FIXTURE_B),
        (("skip_ratchet", "unbudgeted_skips"), []),
    ],
)

# 3. an UNNAMED skip: the composite refuses it by name and FAILS. The verdict
#    line says so plainly -- `0 of 2 checks failed` with the run reddened by the
#    ratchet, never a green quote over a blindness nobody narrated.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([])
rc, text, att, verdict = run("unnamed skip")
expect(
    "an UNNAMED skip FAILS the composite, refused by name",
    rc, 1, text,
    [
        "unnamed skip '%s'" % FIXTURE_B,
        "verify: FAIL (0 of 2 checks failed, 1 skipped: %s" % FIXTURE_B,
        "skip ratchet FAIL",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "VIOLATION"),
        (("skip_ratchet", "unbudgeted_skips"), [FIXTURE_B]),
        (("result",), "FAIL"),
    ],
)

# 4. a STALE exemption: the check assesses, so the entry must go -- rc 1.
mount(((FIXTURE_A, 0), (FIXTURE_B, 0)))
budget([{"check": FIXTURE_A, "kind": "standing-gap", "issue": 1199, "reason": "fixture"}])
rc, text, att, verdict = run("stale exemption")
expect(
    "a STALE exemption FAILS the composite the moment the check assesses",
    rc, 1, text,
    [
        "stale skip exemption '%s'" % FIXTURE_A,
        "the list can only shrink",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "VIOLATION"),
        (("skip_ratchet", "stale_entries", 0, "check"), FIXTURE_A),
    ],
)

# 5. a `{"command": ...}` venue precondition this venue does NOT supply: the
#    exemption bites, so the run is a PASS that NAMES it -- the Cloud Build shape
#    (#1361), where the tool is absent and the check itself is not the defect.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([{"check": FIXTURE_B, "kind": "venue", "issue": 1361,
         "precondition": {"command": "ao-no-such-tool-1361"}, "reason": "fixture"}])
rc, text, att, verdict = run("venue command not supplied")
expect(
    "a venue COMMAND precondition the venue lacks is honoured, by name",
    rc, 0, text,
    [
        "verify: PASS (1 of 2 checks, 1 skipped: %s" % FIXTURE_B,
        "verify: standing skip %s -- venue: cannot run 'ao-no-such-tool-1361'" % FIXTURE_B,
        "the open issue that tracks it is #1361",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "OK"),
        (("skip_ratchet", "standing_skips", 0, "precondition_kind"), "command"),
        (("skip_ratchet", "standing_skips", 0, "precondition_present"), False),
    ],
)

# 6. the SAME entry once the venue SUPPLIES that command: the exemption must stop
#    biting and the run must REFUSE by name. Without this arm the command form
#    would be a statement the entry makes about itself, not a measurement.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([{"check": FIXTURE_B, "kind": "venue", "issue": 1361,
         "precondition": {"command": "true"}, "reason": "fixture"}])
rc, text, att, verdict = run("venue command supplied")
expect(
    "the same entry is REFUSED once the venue supplies its command",
    rc, 1, text,
    [
        "venue precondition present but '%s' still cannot assess" % FIXTURE_B,
        "repair the check rather than widening",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "VIOLATION"),
        (("skip_ratchet", "standing_skips", 0, "precondition_present"), True),
    ],
)

if failures:
    print("  %d of 6 scenario(s) failed" % len(failures))
    raise SystemExit(1)
print(
    "  OK    six scenarios on the real composite: clean, named, unnamed, stale, a "
    "venue command the venue lacks, and the same entry with it supplied"
)
PY
cat "$work/shim.txt"
check "the composite itself names a standing skip and fails on an unnamed one" \
  "$([ "$shim_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the end-to-end shim exited $shim_rc"

# --- verdict ------------------------------------------------------------------
if [ "$fails" -gt 0 ]; then
  echo "check-skip-ratchet: NOT-OK -- $fails of $checks assertion(s) failed" >&2
  exit 1
fi
echo "check-skip-ratchet: OK -- $checks assertion(s) held: the ratchet's rules are all provoked (17 fixtures, each rc AND each refusal line), the committed record loads through the run's own loader, names discovered checks, and declares only venue command preconditions this tree actually runs (a planted phantom probe is refused by name), scripts/verify.sh invokes the ratchet and fails the run on its verdict (proved by mutation), the attestation validator refuses a skip that nothing accounts for and a venue skip that declares no precondition, and the REAL composite run on a shim fixture proves all six end states: clean PASS unchanged, a named standing skip named in the verdict line and recorded in attestation.json, an unnamed skip FAILING by name, a stale exemption FAILING the moment its check assesses, a venue COMMAND precondition the venue lacks honoured inside a PASS, and the same entry REFUSED once the venue supplies it"
exit 0
