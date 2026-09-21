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
#      supplied path is; a `live-dependent` entry (#1410) is honoured BY NAME
#      while the LIVE mechanism it names cannot answer, is NOT stale when its
#      check assesses, and is REFUSED the moment its check FAILS, because a
#      failing check is a FINDING and not a skip -- and its own validation is
#      provoked too (an entry naming no mechanism, naming no issue, or carrying
#      a precondition is refused by the run's own loader); a command-shaped
#      declaration, a precondition of neither form, an entry naming a check that
#      was not discovered, a missing record (fail-closed) and a malformed one
#      (CANNOT-ASSESS) are each refused by name -- never a silent "no exemptions
#      needed", which is how a control turns into a formality (GR-12).
#   2. THE RECORD IS REAL. The committed `scripts/skip-budget.json` is loaded
#      through the SAME loader the run uses (no second copy of the rule), and
#      every entry must name a check this tree actually discovers -- an
#      exemption for a check that does not exist is stale by name. Every
#      `{command: ...}` precondition must additionally be a command THIS TREE
#      RUNS: the record may not invent a probe nothing else measures, and a
#      planted phantom probe is refused by name so that rule cannot match
#      nothing.
#
#      AND EVERY ENTRY IS LEASED TO AN OPEN TRACKER (#1499). Each entry carries
#      `tracked_by` plus the quarantine's `tracking` block, and the committed
#      record is asserted by census over it -- each entry spelling its tracker
#      once (tracked_by == '#' + issue) and leased to an OPEN issue. Both halves
#      of the rule are PROVED to have a failing path through the run's OWN code,
#      never a second copy: its loader refuses a planted entry carrying no lease,
#      and its `evaluate()` refuses a planted CLOSED tracker BY NAME -- with the
#      SAME entry at state `open` as the positive control, because a rule that
#      refuses the record wholesale is not the rule under test. The census's own
#      failing path is proved by running it over the record with every tracker
#      flipped to `closed`. The per-entry re-point DECISION (#1499 ask 1) is
#      asserted the same way: a row for every committed entry, the row's `now`
#      being that entry's `tracked_by` and differing from `was`, with a row
#      REMOVED as the failing path.
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
#   5. THE THIRD KIND IS LOAD-BEARING (#1410). Sections 1-4 assert that the
#      kinds' rules hold; they would still hold for a ratchet that narrated a
#      LIVE-dependent skip as something else, because the mutant that does
#      exactly that keeps rc 0. So the gate DELETES the honouring from a copy of
#      the ratchet and requires the fixture that passed to red BY NAME -- and it
#      prints how the mutant changed (it falls back to the standing-gap
#      narration, the mislabel #1410 removes) rather than only that it did. A
#      control that reports "it failed" without naming what changed would pass on
#      a coincidence.
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
#
# ---knowledge---
# module_id: scripts.check-skip-ratchet
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, no-false-green, named-refusal, lane-isolation, schema-validation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1164", "#1176", "#1189", "#1199"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
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

# The summary quotes how many fixtures were provoked, and the number is READ from
# the battery's own output rather than hard-coded: a literal beside a battery that
# grew is a number nothing measured, which is the class of claim this repository
# refuses.
selftest_cases="$(sed -n 's/.*self-test: OK -- \([0-9][0-9]*\) provoked case(s).*/\1/p' "$work/self-test.txt" | tail -1)"
check "the battery reports how many fixtures it provoked" \
  "$([ -n "$selftest_cases" ] && echo 0 || echo 1)" \
  "no fixture count could be read from the self-test output, so any number quoted in the summary would be one nothing measured"
if [ -z "$selftest_cases" ]; then selftest_cases="?"; fi

if [ "$self_test_only" -eq 1 ]; then
  echo "check-skip-ratchet: OK -- the ratchet's rules are provable (--self-test only)"
  exit 0
fi

# --- 2. the committed record is real -----------------------------------------
echo "== the committed record (scripts/skip-budget.json) =="
record_rc=0
python3 - "$root" "$budget" "$work" > "$work/record.txt" 2>&1 <<'PY' || record_rc=$?
"""Load the committed budget through the RUN's own loader, and require every
entry to name a check this tree discovers.

Reusing `load_budget` is the point: a second copy of the rule here could disagree
with the rule the run applies, and this gate would then be asserting a fiction.
"""
import importlib.util
import json
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

# --- the THIRD kind (#1410), which has its own census and its own three rules ---
# Each of the kind's validation rules is PROVED to have a failing path by running
# the plant through the RUN's OWN loader: a rule that stopped matching would
# otherwise be discovered only at the moment a live-dependent skip silently
# passed, which is the failure this kind exists to make visible. And the census
# itself is fail-able: the kind is declared in scripts/lib/skip-ratchet.py, so a
# committed record with no such entry means the kind exists in the code and
# nowhere in the record.
live_entries = [e for e in entries if e.get("kind") == "live-dependent"]
scratch = Path(sys.argv[3])
scratch.mkdir(parents=True, exist_ok=True)


def planted_loader_findings(candidate, label):
    """The run's OWN loader, on a planted record: can this rule fail at all?"""
    path = scratch / ("planted-%s.json" % label)
    path.write_text(
        json.dumps({"schema": module.BUDGET_SCHEMA, "entries": candidate}) + "\n",
        encoding="utf-8",
    )
    _entries, findings_here = module.load_budget(path)
    return findings_here


PLANTED_LIVE = (
    (
        "no-issue",
        {"check": "gate-status", "kind": "live-dependent",
         "mechanism": "a planted live mechanism", "reason": "planted"},
        "live-dependent with no open issue",
    ),
    (
        "no-mechanism",
        {"check": "gate-status", "kind": "live-dependent", "issue": 1410,
         "reason": "planted"},
        "is live-dependent with no 'mechanism'",
    ),
    (
        "with-precondition",
        {"check": "gate-status", "kind": "live-dependent", "issue": 1410,
         "mechanism": "a planted live mechanism",
         "precondition": {"command": "gh auth status"}, "reason": "planted"},
        "is live-dependent and declares a precondition",
    ),
)
for label, planted_entry, needle in PLANTED_LIVE:
    got = planted_loader_findings([planted_entry], label)
    if not any(needle in finding for finding in got):
        findings.append(
            "the planted live-dependent entry (%s) was ACCEPTED by the run's own "
            "loader -- this rule matches nothing, so the kind's validation has no "
            "failing path (expected a finding naming %r)" % (label, needle)
        )

def census_findings(candidate_entries):
    """The census as a FUNCTION, so the committed record AND a record with the kind
    stripped run through exactly the same code.

    A census only ever called on a record that already carries the kind is a
    formality (GR-12): it would keep passing after it stopped matching anything,
    and the only witness would be the record nobody checked. So its failing path
    is PROVED below, on the same function, with the kind stripped -- not claimed.
    """
    if [e for e in candidate_entries if e.get("kind") == "live-dependent"]:
        return []
    return [
        "the committed record carries NO live-dependent entry -- the third kind "
        "(#1410) is declared in scripts/lib/skip-ratchet.py, and the checks whose "
        "assessability is a property of the WORLD must be named with it, so a census "
        "of zero means the kind exists in the code and nowhere in the record"
    ]


findings.extend(census_findings(entries))
CENSUS_NEEDLE = "carries NO live-dependent entry"
planted_census = census_findings(
    [e for e in entries if e.get("kind") != "live-dependent"]
)
if not any(CENSUS_NEEDLE in finding for finding in planted_census):
    findings.append(
        "the planted record with every live-dependent entry STRIPPED was ACCEPTED by "
        "the census -- it matches nothing, so a record that names the third kind "
        "nowhere would pass it (expected a finding naming %r, got %r)"
        % (CENSUS_NEEDLE, planted_census or "no finding")
    )

# --- the LEASE (#1499), and the decision record it carries --------------------
# An exemption is honoured only while its tracker is OPEN. Two halves, and each is
# PROVED to have a failing path -- through the RUN's OWN code, never a second copy
# of the rule:
#   * the SHAPE half lives in `load_budget` (a missing or malformed lease is
#     CANNOT-ASSESS), so a planted entry with no `tracking` block is loaded to
#     show the rule can fail;
#   * the STATE half lives in `evaluate` (a state that is not `open` is REFUSED by
#     name), so a planted CLOSED tracker goes through `evaluate` and must come
#     back rc 1 naming it -- with the SAME entry at state `open` as the positive
#     control, because a rule that refuses everything proves nothing.
# The committed record is then asserted by census (every entry spells its tracker
# once and is leased to an OPEN one), and the census's own failing path is proved
# by running that same function over the record with one entry's state flipped.
doc = {}
try:
    doc = json.loads(budget_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    doc = {}


def lease_census(candidate_entries):
    """Every entry spells its tracker once and is leased to an OPEN one."""
    out = []
    for entry in candidate_entries:
        name = entry.get("check")
        tracked_by = entry.get("tracked_by")
        issue = entry.get("issue")
        if not isinstance(tracked_by, str) or tracked_by != "#%s" % issue:
            out.append(
                "entry '%s' does not spell its tracker once: tracked_by=%r, issue=%r"
                % (name, tracked_by, issue)
            )
        tracking = entry.get("tracking")
        if not isinstance(tracking, dict) or tracking.get("state") != "open":
            out.append(
                "entry '%s' is not leased to an OPEN tracker: tracking.state=%r "
                "(a closed tracker may not be honoured -- re-point it or retire it)"
                % (
                    name,
                    tracking.get("state") if isinstance(tracking, dict) else tracking,
                )
            )
    return out


findings.extend(lease_census(entries))
LEASE_NEEDLE = "is not leased to an OPEN tracker"
planted_lease_census = lease_census(
    [
        dict(e, tracking=dict(e["tracking"], state="closed"))
        if isinstance(e.get("tracking"), dict)
        else e
        for e in entries
    ]
)
if not any(LEASE_NEEDLE in finding for finding in planted_lease_census):
    findings.append(
        "the planted record with EVERY tracker CLOSED was ACCEPTED by the lease "
        "census -- it matches nothing, so a record leased to a closed issue would "
        "pass it (expected a finding naming %r, got %r)"
        % (LEASE_NEEDLE, planted_lease_census or "no finding")
    )

# The SHAPE half, through the run's own loader: a planted entry with no lease.
LEASE_SHAPE_NEEDLE = "carries no 'tracking' block"
if not any(
    LEASE_SHAPE_NEEDLE in finding
    for finding in planted_loader_findings(
        [
            {
                "check": "ao-lease-fixture-1499",
                "kind": "standing-gap",
                "issue": 1499,
                "reason": "planted",
            }
        ],
        "no-lease",
    )
):
    findings.append(
        "the planted entry carrying NO lease was ACCEPTED by the run's own loader -- "
        "this rule matches nothing, so an unleased exemption would be honoured "
        "(expected a finding naming %r)" % LEASE_SHAPE_NEEDLE
    )


def _planted_lease(state):
    return {
        "check": "ao-lease-fixture-1499",
        "kind": "standing-gap",
        "issue": 1361,
        "tracked_by": "#1361",
        "tracking": {
            "state": state,
            # A state that is not `open` is refused BEFORE the age is consulted, so
            # a literal instant is safe here and cannot go stale (the
            # clock/date-bomb class, #1025); the clock is passed in explicitly.
            "measured_at": "2026-09-20T13:14:34Z",
            "measured_by": "scripts/check-skip-ratchet.sh planted fixture",
            "measured_via": "fixture: the state half of the rule, at a pinned instant",
            "max_age_hours": 720,
        },
        "reason": "planted",
    }


_CLOSED_NEEDLE = "is closed, not open"
_PINNED = module.parse_utc("2026-09-20T13:14:34Z")
_closed_record, _closed_rc, _ = module.evaluate(
    root,
    [("ao-lease-fixture-1499", 2)],
    ["ao-lease-fixture-1499"],
    [_planted_lease("closed")],
    "planted",
    at=_PINNED,
)
if _closed_rc != 1 or not any(
    _CLOSED_NEEDLE in finding for finding in _closed_record["findings"]
):
    findings.append(
        "the planted entry whose tracker is CLOSED was not REFUSED by the run's own "
        "evaluate() (rc %r, findings %r) -- expected rc 1 naming %r"
        % (_closed_rc, _closed_record["findings"], _CLOSED_NEEDLE)
    )
_open_record, _open_rc, _ = module.evaluate(
    root,
    [("ao-lease-fixture-1499", 2)],
    ["ao-lease-fixture-1499"],
    [_planted_lease("open")],
    "planted",
    at=_PINNED,
)
if _open_rc != 0:
    findings.append(
        "the SAME planted entry with its tracker OPEN was REFUSED (rc %r: %r) -- a "
        "rule that refuses the record wholesale is not the rule under test"
        % (_open_rc, _open_record["findings"])
    )


# The re-point decision (issue #1499 ask 1) is LOAD-BEARING, not decorative: every
# committed entry must have a row, the row's `now` must BE that entry's
# `tracked_by`, and `now` must differ from `was` -- a row that re-points to itself
# records no decision at all.
def decision_findings(candidate_doc, candidate_entries):
    rows = (candidate_doc.get("tracking-repoint-2026-09-20") or {}).get("rows")
    if not isinstance(rows, list):
        return [
            "the record carries no tracking-repoint-2026-09-20.rows -- the per-entry "
            "decision issue #1499 asks for is not recorded anywhere"
        ]
    by = {e.get("check"): e for e in candidate_entries}
    named = set()
    out = []
    for row in rows:
        check = row.get("check")
        named.add(check)
        entry = by.get(check)
        if entry is None:
            out.append(
                "the re-point record names '%s', which is not an entry of this record"
                % check
            )
            continue
        if row.get("now") != entry.get("tracked_by"):
            out.append(
                "the re-point record says '%s' now names %r while the entry is leased "
                "to %r -- the decision and the entry must agree"
                % (check, row.get("now"), entry.get("tracked_by"))
            )
        if row.get("now") == row.get("was"):
            out.append(
                "the re-point record's row for '%s' is a no-op (%r): a row must record "
                "a change or be absent" % (check, row.get("now"))
            )
    for check in by:
        if check not in named:
            out.append(
                "entry '%s' has no row in the re-point record -- an unrecorded "
                "decision is the silent edit issue #1499 forbids" % check
            )
    return out


findings.extend(decision_findings(doc, entries))
DECISION_NEEDLE = "has no row in the re-point record"
planted_decision = decision_findings(
    {
        "tracking-repoint-2026-09-20": {
            "rows": [
                row
                for row in (doc.get("tracking-repoint-2026-09-20") or {}).get("rows", [])
                if row.get("check") != "cmr-pin"
            ]
        }
    },
    entries,
)
if not any(DECISION_NEEDLE in finding for finding in planted_decision):
    findings.append(
        "the planted re-point record with one row REMOVED was ACCEPTED -- the rule "
        "matches nothing, so an entry whose decision was never recorded would pass "
        "(expected a finding naming %r, got %r)"
        % (DECISION_NEEDLE, planted_decision or "no finding")
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
print(
    "  OK    %d live-dependent entry(ies), each naming the LIVE mechanism it depends "
    "on and the OPEN issue that tracks it, and the planted records that omit the "
    "issue, omit the mechanism and carry a precondition are each refused by the "
    "run's own loader, while a record with the kind STRIPPED is refused by the census "
    "itself (so a census of zero has a failing path, not only a passing one)"
    % len(live_entries)
)
print(
    "  OK    %d entry(ies) leased to an OPEN tracker, each spelling its tracker once "
    "(tracked_by == '#' + issue), and the planted record with one tracker CLOSED is "
    "refused by the lease census while the run's OWN loader refuses an entry carrying "
    "no lease and the run's OWN evaluate() refuses a CLOSED one by name -- with the "
    "same entry OPEN as the positive control" % len(entries)
)
print(
    "  OK    %d re-point decision(s) recorded for %d committed entry(ies) (issue "
    "#1499 ask 1), each row's `now` being its entry's tracked_by and differing from "
    "`was`; and a record with one row REMOVED is refused by that same rule"
    % (len(doc.get("tracking-repoint-2026-09-20", {}).get("rows", [])), len(entries))
)
for entry in entries:
    declared = entry.get("precondition")
    if isinstance(declared, dict):
        declared = "command: %s" % declared.get("command")
    elif declared is None and entry.get("mechanism"):
        declared = "mechanism: %s" % entry.get("mechanism")
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
echo "      (the mutated COPY is EXPECTED to be refused: the findings below are the negative control's evidence, not a failure of this run -- the mutation is what makes the assertion's failing path visible)"
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
#                          whatever the venue supplies -- the fail-open direction);
#   * a LIVE-DEPENDENT skip (the third kind, #1410) whose check cannot assess ->
#                          `verify: PASS` naming the LIVE mechanism that did not
#                          answer and the open issue, rc 0, never the check as the
#                          thing to repair;
#   * the SAME entry while its check ASSESSES (rc 0) -> rc 0, NOT stale: the not-
#                          biting entry is reported and the run stays green;
#   * the SAME entry while its check FAILS (rc 1) -> rc 1, refused by name -- a
#                          failing check is a finding, not a skip;
#   * a LIVE-DEPENDENT entry with no live mechanism -> rc 1, the record cannot be
#                          evaluated and the malformed entry is named.
#   * an entry whose tracking issue is CLOSED (#1499) -> rc 1, refused BY NAME
#                          with the two remedies, which is the defect the rule
#                          exists for measured end to end: all 13 entries of the
#                          committed record were in exactly this state when the
#                          issue was filed, and the venue read green.
#   * a MALFORMED lease -> rc 1 with the ratchet CANNOT-ASSESS, so a skip whose
#                          exemption could not be READ is never silently excused.
# The gate permit store is a private fixture dir: this is a control, not a
# competing gate (the box-wide cap is proved by scripts/check-gate-lock.sh).
echo "== the composite, end to end (fixture checks, the real scripts/verify.sh) =="
shim_rc=0
python3 - "$root" "$work" > "$work/shim.txt" 2>&1 <<'PY' || shim_rc=$?
"""Run the REAL scripts/verify.sh twelve times on a shim with a fixture check set."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
shim = work / "shim"

# Byte-identical copies of the orchestrator surface verify.sh needs, exactly as
# scripts/check-gate-lock.sh mounts its own scratch worktree.
#
# `scripts/lib/common.sh` is LOAD-TIME, not call-time: since #1753 both
# `scripts/gate-lock.sh` and `scripts/discover-checks.sh` `source` it at the top
# of the file for `find_repo_root`. A shim that omits it leaves `find_repo_root`
# undefined, so gate-lock.sh resolves an EMPTY root and verify.sh exits 2
# (CANNOT-ASSESS, "the gate permit store is unusable") instead of admitting the
# gate -- every scenario below would then read rc 2 regardless of its budget, and
# the whole composite section would prove nothing. Carrying it is the same remedy
# #1840 applied to the futureproof fixture tree.
ORCHESTRATOR = (
    "scripts/verify.sh",
    "scripts/gate-lock.sh",
    "scripts/discover-checks.sh",
    "scripts/lib/common.sh",
    "scripts/lib/skip-ratchet.py",
    "scripts/lib/validate-attestation.py",
    "governance/isolation/attestation.schema.json",
    "fleet/gatelock.py",
    "fleet/lease.py",
)
FIXTURE_A = "skip-ratchet-fixture-a"
FIXTURE_B = "skip-ratchet-fixture-b"

failures = []


def mount(fixture_rcs, ratchet_source=None):
    shutil.rmtree(shim, ignore_errors=True)
    for relative in ORCHESTRATOR:
        target = shim / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if ratchet_source is not None and relative == "scripts/lib/skip-ratchet.py":
            # Only the falsification control substitutes the ratchet, on purpose: a
            # shim that did it by default would stop testing the real one.
            shutil.copyfile(ratchet_source, target)
        else:
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


LEASE_STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def lease(entry):
    """Fill in the lease (#1499) for a fixture entry that does not declare one.

    Derived from the fixture's own clock and the entry's own issue: a fixture that
    pinned a date would be green the day it was written and refused every day
    after (the clock/date-bomb class, #1025). A scenario that WANTS a closed
    tracker -- or no lease at all -- declares it itself and is left untouched.
    """
    if "tracking" in entry or "tracked_by" in entry:
        return entry
    issue = entry.get("issue")
    if issue is None:
        return entry
    return dict(
        entry,
        tracked_by="#%s" % issue,
        tracking={
            "state": "open",
            "measured_at": LEASE_STAMP,
            "measured_by": "scripts/check-skip-ratchet.sh shim fixture",
            "measured_via": "fixture: the entry's own issue number, at the fixture's clock",
            "max_age_hours": 720,
        },
    )


def budget(entries):
    path = shim / "scripts" / "skip-budget.json"
    if entries is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(
        json.dumps(
            {
                "schema": "ao.verify.skip-budget/v1",
                "entries": [lease(e) for e in entries],
            },
            indent=2,
        )
        + "\n",
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
budget([{"check": FIXTURE_B, "kind": "venue", "issue": 1199, "precondition": "vendor/CMR/sync", "reason": "fixture"}])
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

# 7. a LIVE-DEPENDENT skip (#1410): the check cannot assess because the LIVE
#    mechanism it reads did not answer. The composite PASSES and NAMES the
#    mechanism and the open issue -- and it does NOT tell the operator to repair
#    the check, which is the mislabel the third kind exists to remove.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
LIVE_MECHANISM = "the fixture's LIVE source, read with a tool the world may not answer with"
budget([{"check": FIXTURE_B, "kind": "live-dependent", "issue": 1410,
         "mechanism": LIVE_MECHANISM, "reason": "fixture"}])
rc, text, att, verdict = run("live-dependent skip")
expect(
    "a LIVE-dependent skip is a PASS naming the mechanism, never the check",
    rc, 0, text,
    [
        "verify: PASS (1 of 2 checks, 1 skipped: %s" % FIXTURE_B,
        "verify: standing skip %s -- live-dependent: the LIVE mechanism" % FIXTURE_B,
        "a property of the WORLD, not a defect of the check",
        "honoured BY NAME rather than repaired",
        "the open issue that tracks the mechanism is #1410",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "OK"),
        (("skip_ratchet", "standing_skips", 0, "kind"), "live-dependent"),
        (("skip_ratchet", "standing_skips", 0, "mechanism"), LIVE_MECHANISM),
        (("skip_ratchet", "stale_entries"), []),
        (("skip_ratchet", "unused_live_entries"), []),
    ],
)

# 8. the SAME entry while its check ASSESSES: this kind does NOT go stale. The
#    run stays green and the entry is REPORTED as not biting -- without this arm
#    the kind would be free to fail a check that had come back, which is the
#    half of the rule a plain honoured-on-rc-2 entry cannot express.
mount(((FIXTURE_A, 0), (FIXTURE_B, 0)))
budget([{"check": FIXTURE_B, "kind": "live-dependent", "issue": 1410,
         "mechanism": LIVE_MECHANISM, "reason": "fixture"}])
rc, text, att, verdict = run("live-dependent assessed")
expect(
    "a live-dependent entry is NOT stale when its check assesses",
    rc, 0, text,
    [
        "verify: PASS (2 of 2 checks)",
        "1 live-dependent entr(y/ies) did not bite in this run",
        "this kind does NOT go stale",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "OK"),
        (("skip_ratchet", "stale_entries"), []),
        (("skip_ratchet", "unused_live_entries"), [FIXTURE_B]),
    ],
)

# 9. the SAME entry while its check FAILS (rc 1): refused by name. A failing
#    check is a FINDING, not a skip -- honouring it here would hide a defect
#    behind the name of a mechanism that answered.
mount(((FIXTURE_A, 0), (FIXTURE_B, 1)))
budget([{"check": FIXTURE_B, "kind": "live-dependent", "issue": 1410,
         "mechanism": LIVE_MECHANISM, "reason": "fixture"}])
rc, text, att, verdict = run("live-dependent check fails")
expect(
    "a live-dependent entry whose check FAILS is REFUSED by name",
    rc, 1, text,
    [
        "live-dependent entry '%s'" % FIXTURE_B,
        "a failing check is a FINDING, not a skip",
        # The ratchet's note rides in the SAME parenthesis as the counts, so the
        # line is asserted whole: a needle that stopped before the note would pass
        # while the note itself was missing.
        "verify: FAIL (1 of 2 checks failed; skip ratchet FAIL",
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "VIOLATION"),
        (("result",), "FAIL"),
    ],
)

# 10. a LIVE-DEPENDENT entry that names no live mechanism: the record cannot be
#     evaluated at all, so the run cannot attest a skip set it could not read.
mount(((FIXTURE_A, 0), (FIXTURE_B, 0)))
budget([{"check": FIXTURE_B, "kind": "live-dependent", "issue": 1410,
         "reason": "fixture, no mechanism named"}])
rc, text, att, verdict = run("live-dependent without a mechanism")
expect(
    "a live-dependent entry with no LIVE mechanism is CANNOT-ASSESS by name",
    rc, 1, text,
    [
        "malformed budget entry",
        "is live-dependent with no 'mechanism'",
        "skip ratchet CANNOT-ASSESS",
    ],
    att,
    [(("skip_ratchet", "verdict"), "CANNOT-ASSESS")],
)

# 11. the LEASE (#1499): the same record with one entry pointing at a CLOSED
#     tracker. The composite must FAIL by name -- this is the defect the rule
#     exists for, measured END TO END rather than only in the unit fixtures, and
#     it is the state the committed record was actually in when #1499 was filed
#     (all 13 entries named a tracker that had closed, and the venue read green).
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([{"check": FIXTURE_B, "kind": "venue", "issue": 1295,
         "precondition": "vendor/CMR/sync", "reason": "fixture",
         "tracked_by": "#1295",
         "tracking": {"state": "closed", "measured_at": LEASE_STAMP,
                      "measured_by": "gate fixture",
                      "measured_via": "fixture: a CLOSED tracker",
                      "max_age_hours": 720}}])
rc, text, att, verdict = run("closed tracker")
expect(
    "an entry whose tracking issue is CLOSED FAILS the composite, by name",
    rc, 1, text,
    [
        "is not leased to an open tracker",
        "the tracking issue #1295 is closed, not open",
        "re-point it at the live issue that now tracks the gap, or retire the entry",
        "verify: FAIL (0 of 2 checks failed, 1 skipped: %s" % FIXTURE_B,
    ],
    att,
    [
        (("skip_ratchet", "verdict"), "VIOLATION"),
        (("skip_ratchet", "standing_skips", 0, "tracked_by"), "#1295"),
        (("skip_ratchet", "standing_skips", 0, "tracking_state"), "closed"),
        (("result",), "FAIL"),
    ],
)

# 12. a MALFORMED lease: fail-closed, so the composite cannot attest a skip set
#     whose exemption it could not read. A missing or unreadable declaration is
#     never a silent "still excused" -- the direction the quarantine takes too.
mount(((FIXTURE_A, 0), (FIXTURE_B, 2)))
budget([{"check": FIXTURE_B, "kind": "venue", "issue": 1295,
         "precondition": "vendor/CMR/sync", "reason": "fixture",
         "tracked_by": "#1295", "tracking": {}}])
rc, text, att, verdict = run("lease with no state")
expect(
    "an entry with a malformed lease is CANNOT-ASSESS by name",
    rc, 1, text,
    [
        "malformed budget entry",
        "tracking.state must be a non-empty string",
        "skip ratchet CANNOT-ASSESS",
    ],
    att,
    [(("skip_ratchet", "verdict"), "CANNOT-ASSESS")],
)

if failures:
    print("  %d of 12 scenario(s) failed" % len(failures))
    raise SystemExit(1)
print(
    "  OK    twelve scenarios on the real composite: clean, named, unnamed, stale, a "
    "venue command the venue lacks, the same entry with it supplied, a "
    "live-dependent skip honoured by name, the same entry NOT stale when its check "
    "assesses, REFUSED when its check fails, CANNOT-ASSESS when it names no "
    "mechanism, an entry whose tracking issue is CLOSED FAILING by name (#1499) and "
    "a malformed lease CANNOT-ASSESS rather than silently excused"
)
PY
cat "$work/shim.txt"
check "the composite itself names a skip and fails on an unnamed one, the third kind is honoured, not stale and refused on failure, and an exemption whose tracker is CLOSED is refused" \
  "$([ "$shim_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the end-to-end shim exited $shim_rc"

# --- 6. the falsification control --------------------------------------------
# Sections 1-5 assert that the third kind's rules hold. This one proves they can
# FAIL: the branch that HONOURS a live-dependent skip is deleted from a copy of
# the ratchet, and the fixture that passed must then red BY NAME. Without it the
# arms above could be satisfied by a ratchet that narrated every live-dependent
# skip as something else -- and the report says HOW the mutant changed, because
# "it failed" is not the same claim as "it failed for this reason": measured, the
# mutant keeps rc 0 and narrates the entry as a STANDING GAP, which is exactly
# the mislabel #1410 exists to remove. That is why the naming, not the exit code,
# is the property under test here.
echo "== the falsification control (the kind's honouring is load-bearing) =="
falsify_rc=0
python3 - "$root" "$work" > "$work/falsify.txt" 2>&1 <<'PY' || falsify_rc=$?
"""Remove the live-dependent honouring from a COPY of the ratchet and require
this fixture to stop meeting its expectation, naming what changed."""
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])
work = Path(sys.argv[2])
real = root / "scripts" / "lib" / "skip-ratchet.py"
text = real.read_text(encoding="utf-8")
ANCHOR = (
    '        elif entry["kind"] == LIVE_DEPENDENT:\n'
    '            lines.append(live_dependent_line(name, record))\n'
)
if ANCHOR not in text:
    print(
        "  FAIL  the mutation's anchor moved: the live-dependent honouring is not "
        "where this control expects it, so the control would prove nothing"
    )
    raise SystemExit(1)
mutant = work / "mutant-lib" / "skip-ratchet.py"
mutant.parent.mkdir(parents=True, exist_ok=True)
mutant.write_text(text.replace(ANCHOR, "", 1), encoding="utf-8")
print("  OK    mutant written: %d byte(s) of the live-dependent honouring removed" % len(ANCHOR))

fixture = work / "falsify-root"
(fixture / "scripts").mkdir(parents=True, exist_ok=True)
(fixture / "results.tsv").write_text("gate-status\t2\t0\tgate-status.out\n", encoding="utf-8")
(fixture / "names.txt").write_text("gate-status\n", encoding="utf-8")
(fixture / "skip-budget.json").write_text(
    json.dumps(
        {
            "schema": "ao.verify.skip-budget/v1",
            "entries": [
                {
                    "check": "gate-status",
                    "kind": "live-dependent",
                    "issue": 1382,
                    "tracked_by": "#1382",
                    "tracking": {
                        "state": "open",
                        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "measured_by": "scripts/check-skip-ratchet.sh falsification fixture",
                        "measured_via": "fixture: clock-derived, so the arm cannot go stale",
                        "max_age_hours": 720,
                    },
                    "mechanism": "the GitHub commit statuses API, read with gh",
                    "reason": "the live source did not answer",
                }
            ],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)


def driven(lib):
    proc = subprocess.run(
        [sys.executable, str(lib), "--root", str(fixture),
         "--results", str(fixture / "results.tsv"),
         "--names", str(fixture / "names.txt"),
         "--budget", str(fixture / "skip-budget.json")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


rc_real, out_real = driven(real)
rc_mutant, out_mutant = driven(mutant)
HONOUR = "live-dependent: the LIVE mechanism"
MISLABEL = "standing gap tracked by #1382"
problems = []
if rc_real != 0 or HONOUR not in out_real:
    problems.append(
        "the REAL ratchet did not honour the live-dependent skip by name (rc %d)" % rc_real
    )
if HONOUR in out_mutant:
    problems.append(
        "the MUTANT still named the live mechanism, so the honouring is not what "
        "carries this behaviour"
    )
if MISLABEL not in out_mutant:
    problems.append(
        "the mutant did not fall back to the standing-gap narration, so this control "
        "does not show what removing the honouring actually does"
    )
print("  real   rc=%d" % rc_real)
for line in out_real.splitlines():
    if HONOUR in line:
        print("         %s" % line)
print("  mutant rc=%d" % rc_mutant)
for line in out_mutant.splitlines():
    if "standing skip" in line:
        print("         %s" % line)
if problems:
    for problem in problems:
        print("  FAIL  %s" % problem)
    raise SystemExit(1)
print(
    "  OK    the fixture that PASSED on the real ratchet REDS BY NAME once the "
    "honouring is removed, and rc stays %d -- which is the point: the mutant keeps "
    "the run green while narrating a LIVE-dependent skip as a standing gap, so the "
    "naming is the property under test, not the exit code" % rc_mutant
)
PY
cat "$work/falsify.txt"
check "the live-dependent honouring is load-bearing (removing it mislabels the skip)" \
  "$([ "$falsify_rc" -eq 0 ] && echo 0 || echo 1)" \
  "the falsification control exited $falsify_rc"

# --- verdict ------------------------------------------------------------------
if [ "$fails" -gt 0 ]; then
  echo "check-skip-ratchet: NOT-OK -- $fails of $checks assertion(s) failed" >&2
  exit 1
fi
echo "check-skip-ratchet: OK -- $checks assertion(s) held: the ratchet's rules are all provoked ($selftest_cases fixtures, each rc AND each refusal line), the committed record loads through the run's own loader, names discovered checks, leases every entry to an OPEN tracker (#1499) with both halves of that rule proved to have a failing path through the run's own code (its loader refuses an entry carrying no lease; its evaluate() refuses a CLOSED one by name, with the same entry OPEN as the positive control), records a per-entry re-point decision whose rows are asserted against the entries they describe, declares only venue command preconditions this tree actually runs (a planted phantom probe is refused by name) and carries the THIRD kind (#1410) with its own census -- a census of zero reds by name, and a record with the kind STRIPPED is refused by that same census, so it has a failing path rather than only a passing one, while the planted live-dependent records that omit the issue, omit the mechanism or carry a precondition are each refused by the same loader; scripts/verify.sh invokes the ratchet and fails the run on its verdict (proved by mutation); the attestation validator refuses a skip that nothing accounts for and a venue skip that declares no precondition; the REAL composite run on a shim fixture proves all TWELVE end states (clean PASS unchanged, a named standing skip, an unnamed skip FAILING by name, a stale exemption FAILING, a venue COMMAND precondition honoured, the same entry REFUSED once supplied, a live-dependent skip honoured BY NAME while its LIVE mechanism cannot answer and NOT stale when its check assesses, the same entry REFUSED the moment the check FAILS because a failing check is a finding, a live-dependent entry naming no mechanism CANNOT-ASSESS by name, an entry whose tracking issue is CLOSED FAILING by name with the two remedies, and a malformed lease CANNOT-ASSESS rather than silently excused); and the FALSIFICATION control deletes the honouring from a copy of the ratchet and requires the fixture that passed to red by name -- measured, the mutant stays green and narrates the LIVE skip as a standing gap, so the naming is the load-bearing property rather than the exit code"
exit 0
