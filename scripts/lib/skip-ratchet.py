#!/usr/bin/env python3
"""skip-ratchet.py -- the composite gate's skip ratchet (issue #1199).

THE DEFECT THIS EXISTS FOR
  `scripts/verify.sh` records a check that answers rc 2 CANNOT-ASSESS as `SKIP`
  and still prints `verify: PASS (... N skipped: <names>)`. The names are there,
  which is honest -- but nothing made a *permanently* skipped check stop reading
  as part of a green board. A check that can never assess is not a pass that
  happens to be skipped; it is a check that does not exist, wearing the
  composite's green. Measured on pristine `origin/master` (`04ad55a`), two live
  witnesses sat in that bucket while the composite read PASS, and each hid a
  filed defect it could not report: `check-paperclip-routines` (rc 2, hiding
  #1176) and `check-dispatch-queue` (rc 2, hiding #1189).

WHICH SHAPE, AND WHY (the issue offered two)
  This is shape 2 -- a NAMED, SHRINK-ONLY exemption record -- not shape 1 (a
  per-check budget over N consecutive attested runs). Shape 1 has no substrate
  here, and that was measured rather than assumed: `.verify/` is gitignored
  (`.gitignore:39`), lives per WORKTREE, and `scripts/verify.sh` truncates the
  run's own results at the start of every run. Every lane in this fleet runs the
  gate in its own fresh worktree, so a "consecutive runs" counter would restart
  at 1 in the place where it matters and could never reach N. A committed record
  is deterministic, shared by every venue, and -- the property that makes it a
  ratchet rather than a description -- it can be PROVOKED offline in
  milliseconds. It is also the idiom this repository already uses for legacy
  drift: `governance/futureproof/known-gaps.json` (#1193/#1200),
  `scripts/gate-coverage-baseline.txt` (#526), `governance/lifecycle/baseline.json`.

THE RULES (rc-2 semantics themselves are NOT changed)
  A per-check rc 2 stays exactly what it was: status SKIP, verdict WARN, never
  OK and never a per-check failure. What changes is whether the BOARD can read
  the composite as green:

  * NAMED      every skipped check must be named in `scripts/skip-budget.json`.
               A name carries either a `standing-gap` entry (the inputs ARE
               present and it still cannot assess -- #1176's argument -- so it
               must point at the open issue that tracks it) or a `venue` entry
               (it cannot assess because a NAMED PRECONDITION of this venue is
               not supplied: either a repo-relative PATH that is absent, e.g.
               `vendor/CMR/sync` in a worktree whose submodule was never
               initialised, or a COMMAND the venue must be able to run, e.g.
               `{"command": "gh auth status"}` in a container that installs no
               `gh` -- #1361). A skipped check with no entry is REFUSED by
               name: the composite will not publish a PASS whose skip set is
               narrated by nobody.
  * SHRINKING  a `standing-gap` entry is STALE the moment its check assesses --
               the run FAILS naming the entry, and the entry must be deleted.
               The list can only shrink and cannot outlive its fix.
  * PRECISE    a `venue` entry is honoured only while its precondition is NOT
               supplied -- measured, not asserted, for both forms (a path is
               absent; a command exits non-zero or cannot be run). If the
               precondition IS supplied and the check still cannot assess, that
               is a defect of the CHECK, not of the venue, and it is refused by
               name. A `venue` entry that did not bite in this run is reported
               (`unused_venue_entries`), never silently ignored.

  Fail-closed everywhere: a MISSING budget file means "no exemptions" (every
  skip is then unnamed and refused); a MALFORMED one is CANNOT-ASSESS, never a
  silent no-exemption; an entry naming a check that is not discovered is
  refused by name.

Usage:
  python3 scripts/lib/skip-ratchet.py --results .verify/.results.tsv \
      --names .verify/.check-names.txt [--budget scripts/skip-budget.json] \
      [--root DIR] [--json-out FILE] [--note-out FILE]
  python3 scripts/lib/skip-ratchet.py --self-test

Exit codes: 0 OK (every skip named, no stale entry) / 1 RATCHET VIOLATION
(refused by name) / 2 CANNOT-ASSESS (the skip set could not be evaluated).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_BUDGET = "scripts/skip-budget.json"
BUDGET_SCHEMA = "ao.verify.skip-budget/v1"

STANDING_GAP = "standing-gap"
VENUE = "venue"
KINDS = (STANDING_GAP, VENUE)

# --- venue preconditions (issues #1199/#1351, #1361) --------------------------
#
# A `venue` precondition is a NAMED, MEASURED property the check needs and this
# venue does not supply. It has exactly two forms, and the second exists because
# the first cannot express the Cloud Build verify container (#1361), which is not
# a worktree missing a path but a bare image missing TOOLS:
#
#   "precondition": "vendor/CMR/sync"              a repo-relative PATH
#   "precondition": {"command": "gh auth status"}  a command the venue must RUN
#
# The command form is the check's OWN probe, quoted verbatim -- the budget may
# not invent a precondition nobody else measures, which
# `scripts/check-skip-ratchet.sh` ratifies against the tree. Both forms are
# MEASUREMENTS, never assertions: they are taken here, and the precondition
# counts as supplied only when the measurement says so.
VENUE_COMMAND_TIMEOUT = 10

# A command precondition is an ARGUMENT VECTOR, never a shell line: no shell is
# involved in running it. Refusing these characters keeps that true, and turns a
# shell-shaped declaration into a malformed-budget finding -- the fail-closed
# direction, never a silently-unparsed probe.
_SHELL_METACHARACTERS = "|&;<>(){}`$\\\"'\n\r"

OK = 0
VIOLATION = 1
CANNOT_ASSESS = 2


class CannotAssess(Exception):
    """The ratchet could not evaluate the skip set -- never a pass."""


# --- the budget (the named, shrink-only record) -------------------------------

def load_budget(path: Path) -> Tuple[List[dict], List[str]]:
    """The declared entries, plus shape findings.

    A MISSING file is an empty list, never a bypass: no exemptions means every
    skip is unnamed and therefore refused (the fail-closed direction). A file
    that cannot be READ or PARSED raises CannotAssess -- an exemption record
    that cannot be understood must never be read as "nothing needed".
    """
    if not path.exists():
        return [], []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CannotAssess("%s is not readable JSON (%s)" % (path, exc))
    if not isinstance(doc, dict):
        raise CannotAssess("%s is not a JSON object" % path)
    schema = doc.get("schema")
    if schema != BUDGET_SCHEMA:
        raise CannotAssess(
            "%s declares schema %r, expected %r" % (path, schema, BUDGET_SCHEMA)
        )
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise CannotAssess("%s has no 'entries' list" % path)

    findings: List[str] = []
    seen: Dict[str, int] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            findings.append("entry[%d] is not an object" % i)
            continue
        name = entry.get("check")
        if not isinstance(name, str) or not name:
            findings.append("entry[%d] has no check name" % i)
            continue
        if name in seen:
            findings.append("entry[%d] names %s twice (one entry per check)" % (i, name))
        seen[name] = i
        kind = entry.get("kind")
        if kind not in KINDS:
            findings.append(
                "entry[%d] (%s) has kind %r, expected one of %s"
                % (i, name, kind, "|".join(KINDS))
            )
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            findings.append("entry[%d] (%s) carries no reason" % (i, name))
        if kind == STANDING_GAP:
            issue = entry.get("issue")
            if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
                findings.append(
                    "entry[%d] (%s) is a standing-gap with no open issue number"
                    % (i, name)
                )
        else:
            findings.extend(precondition_findings(i, name, entry.get("precondition")))
            if "issue" in entry and not (
                isinstance(entry["issue"], int)
                and not isinstance(entry["issue"], bool)
                and entry["issue"] > 0
            ):
                findings.append(
                    "entry[%d] (%s) names issue %r, which is not a positive integer "
                    "(a venue entry MAY name the open issue that tracks the gap)"
                    % (i, name, entry["issue"])
                )
    return entries, findings


def precondition_findings(index: int, name: str, value) -> List[str]:
    """Shape findings for one venue `precondition`. An empty list is acceptable."""
    if value is None:
        return ["entry[%d] (%s) is a venue with no precondition" % (index, name)]
    if isinstance(value, dict):
        if list(value.keys()) != ["command"]:
            return [
                "entry[%d] (%s) precondition object must carry exactly one key, "
                "'command' (got %s)"
                % (index, name, ", ".join(sorted(str(k) for k in value.keys())) or "none")
            ]
        command = value.get("command")
        if not isinstance(command, str) or not command.strip():
            return [
                "entry[%d] (%s) precondition command must be a non-empty string"
                % (index, name)
            ]
        bad = sorted({char for char in command if char in _SHELL_METACHARACTERS})
        if bad:
            return [
                "entry[%d] (%s) precondition command %r carries shell metacharacter(s) "
                "%s -- a precondition is an argument vector, never a shell line"
                % (index, name, command, " ".join(repr(char) for char in bad))
            ]
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return [
                "entry[%d] (%s) precondition command %r does not parse (%s)"
                % (index, name, command, exc)
            ]
        if not argv:
            return [
                "entry[%d] (%s) precondition command %r parses to an empty command"
                % (index, name, command)
            ]
        return []
    if not isinstance(value, str) or not value:
        return [
            "entry[%d] (%s) precondition must be a repo-relative path or a "
            "{\"command\": ...} object" % (index, name)
        ]
    if os.path.isabs(value) or ".." in Path(value).parts:
        return [
            "entry[%d] (%s) precondition %r must be a repo-relative path"
            % (index, name, value)
        ]
    return []


def precondition_argv(value) -> List[str]:
    """The argv a `{"command": ...}` precondition runs (already shape-checked)."""
    return shlex.split(value["command"])


def precondition_kind(value) -> str:
    """`path` for a repo-relative path, `command` for a command the venue runs."""
    return "command" if isinstance(value, dict) else "path"


def precondition_label(value) -> str:
    """How a declared precondition reads in a verdict line and in the record."""
    return value["command"] if isinstance(value, dict) else value


def precondition_present(value, root: Path) -> bool:
    """Is this venue's declared precondition SUPPLIED here? A measurement.

    An unreadable path or an unrunnable/timed-out command counts as NOT
    supplied -- the same direction the path form has always taken (`exists()` is
    False when the stat itself fails), and the direction that keeps the refusal
    honest: the entry is honoured now, and the moment the venue supplies the
    precondition the check must assess or the run is refused by name.
    """
    if isinstance(value, dict):
        try:
            proc = subprocess.run(
                precondition_argv(value),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=VENUE_COMMAND_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0
    try:
        return (root / value).exists()
    except OSError:
        return False


def entry_record(entry: dict, root: Path) -> dict:
    """One entry as it appears in the run's record.

    The probe happens HERE, and `evaluate` calls this only for a check that
    actually SKIPPED -- so a venue precondition is measured only when the
    blindness it excuses is real, and an entry that did not bite costs nothing.
    """
    kind = entry["kind"]
    declared = entry.get("precondition") if kind == VENUE else None
    return {
        "check": entry["check"],
        "kind": kind,
        "issue": entry.get("issue") if isinstance(entry.get("issue"), int) else None,
        "precondition": precondition_label(declared) if declared is not None else None,
        "precondition_kind": precondition_kind(declared) if declared is not None else None,
        "precondition_present": (
            precondition_present(declared, root) if declared is not None else None
        ),
        "reason": entry.get("reason", ""),
    }


# --- the results (what the run actually measured) -----------------------------

def read_results(path: Path) -> List[Tuple[str, int]]:
    """The `name<TAB>rc` columns of verify.sh's `.results.tsv`, in run order."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CannotAssess("cannot read the run's results %s (%s)" % (path, exc))
    rows: List[Tuple[str, int]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        try:
            rows.append((fields[0], int(fields[1])))
        except ValueError:
            continue
    if not rows:
        raise CannotAssess("the run's results %s carry no rows" % path)
    return rows


def read_names(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CannotAssess("cannot read the check-name list %s (%s)" % (path, exc))
    names = [line.strip() for line in text.splitlines() if line.strip()]
    if not names:
        raise CannotAssess("the check-name list %s is empty" % path)
    return names


# --- the ratchet --------------------------------------------------------------

def evaluate(
    root: Path,
    results: Sequence[Tuple[str, int]],
    names: Sequence[str],
    entries: Sequence[dict],
    budget_label: str,
) -> Tuple[dict, int, List[str]]:
    """Apply the rules. Returns (record, rc, stdout lines)."""
    discovered = set(names)
    skipped = [(n, rc) for n, rc in results if rc == 2]
    observed = {n for n, _ in results}
    by_check = {e["check"]: e for e in entries}

    standing: List[dict] = []
    unbudgeted: List[str] = []
    stale: List[dict] = []
    unused_venue: List[str] = []
    refused: List[str] = []
    lines: List[str] = []

    for name, _rc in skipped:
        entry = by_check.get(name)
        if entry is None:
            unbudgeted.append(name)
            refused.append(
                "unnamed skip '%s' -- a check that cannot assess is a standing gap "
                "for the board: repair it, or name it in %s against the open issue "
                "that tracks it (never a silent skip)" % (name, budget_label)
            )
            continue
        record = entry_record(entry, root)
        standing.append(record)
        if entry["kind"] == VENUE:
            precondition = record["precondition"]
            if record["precondition_present"]:
                supplied = (
                    "the venue can run %r" % precondition
                    if record["precondition_kind"] == "command"
                    else "its inputs ARE present (%s)" % precondition
                )
                refused.append(
                    "venue precondition present but '%s' still cannot assess -- %s, so "
                    "this is a defect of the check, not of the venue (#1176); repair "
                    "the check rather than widening %s"
                    % (name, supplied, budget_label)
                )
                continue
            tracked = (
                " (the open issue that tracks it is #%s)" % record["issue"]
                if record.get("issue")
                else ""
            )
            if record["precondition_kind"] == "command":
                lines.append(
                    "verify: standing skip %s -- venue: cannot run %r (the declared "
                    "precondition of this venue), so no verdict is available here%s"
                    % (name, precondition, tracked)
                )
            else:
                lines.append(
                    "verify: standing skip %s -- venue: %s absent (the declared "
                    "precondition of this venue), so no verdict is available here%s"
                    % (name, precondition, tracked)
                )
        else:
            lines.append(
                "verify: standing skip %s -- standing gap tracked by #%s: %s"
                % (name, record["issue"], record["reason"])
            )

    for entry in entries:
        name = entry["check"]
        if name not in discovered and name not in observed:
            refused.append(
                "entry names '%s', which is not a check this run discovered -- a "
                "stale exemption must be deleted from %s (the list can only shrink)"
                % (name, budget_label)
            )
            continue
        if name in [n for n, _ in skipped]:
            continue
        rc = next((rc for n, rc in results if n == name), None)
        if entry["kind"] == STANDING_GAP:
            stale.append(
                {
                    "check": name,
                    "why": "assesses now (rc %s) -- the gap is closed" % rc,
                }
            )
            refused.append(
                "stale skip exemption '%s': it assesses now (rc %s), so the entry "
                "must be DELETED from %s -- the list can only shrink and cannot "
                "outlive its fix" % (name, rc, budget_label)
            )
        else:
            unused_venue.append(name)

    rc = VIOLATION if refused else OK
    record = {
        "budget": budget_label,
        "budget_entries": len(entries),
        "verdict": "VIOLATION" if refused else "OK",
        "standing_skips": standing,
        "unbudgeted_skips": unbudgeted,
        "stale_entries": stale,
        "unused_venue_entries": unused_venue,
        "findings": refused,
    }
    return record, rc, lines


def note_for(record: dict) -> str:
    """The suffix verify.sh appends inside its verdict line's parentheses."""
    standing = record["standing_skips"]
    unknown = record["unbudgeted_skips"]
    stale = record["stale_entries"]
    findings = record.get("findings") or []
    if findings:
        bits = []
        if unknown:
            bits.append("%d unnamed skip" % len(unknown))
        if stale:
            bits.append("%d stale exemption" % len(stale))
        if not bits:
            bits.append("%d finding(s)" % len(findings))
        return "; skip ratchet FAIL: %s -- named above" % ", ".join(bits)
    if standing:
        return "; %d standing skip(s) named in %s" % (len(standing), record["budget"])
    return ""


def run(
    root: Path,
    results_path: Path,
    names_path: Path,
    budget_path: Path,
    json_out: Optional[Path],
    note_out: Optional[Path],
) -> int:
    results = read_results(results_path)
    names = read_names(names_path)
    try:
        budget_label = str(budget_path.relative_to(root))
    except ValueError:
        budget_label = str(budget_path)
    entries, shape_findings = load_budget(budget_path)

    if shape_findings:
        for finding in shape_findings:
            print("verify: skip ratchet FAIL -- malformed budget entry: %s" % finding, file=sys.stderr)
        record = {
            "budget": budget_label,
            "budget_entries": len(entries),
            "verdict": "CANNOT-ASSESS",
            "standing_skips": [],
            "unbudgeted_skips": [],
            "stale_entries": [],
            "unused_venue_entries": [],
            "findings": shape_findings,
        }
        _write(json_out, record)
        _write_text(note_out, "; skip ratchet FAIL: the exemption record is malformed")
        print(
            "verify: skip ratchet CANNOT-ASSESS -- %s cannot be evaluated; the gate "
            "will not certify a skip set it could not read" % budget_label,
            file=sys.stderr,
        )
        return CANNOT_ASSESS

    record, rc, lines = evaluate(root, results, names, entries, budget_label)
    for line in lines:
        print(line)
    for finding in record["findings"]:
        print("verify: skip ratchet FAIL -- %s" % finding, file=sys.stderr)
    if record["unused_venue_entries"]:
        print(
            "verify: skip ratchet note -- %d venue entr(y/ies) did not bite in this "
            "run (their precondition is present here): %s"
            % (len(record["unused_venue_entries"]), ", ".join(record["unused_venue_entries"]))
        )
    _write(json_out, record)
    _write_text(note_out, note_for(record))
    return rc


def _write(path: Optional[Path], record: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Optional[Path], text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- the provocation battery --------------------------------------------------
# One fixture per rule, each asserting the EXIT CODE and the ACTUAL line beside
# the expectation -- a needle that only exists on the failure path, or a
# mismatch that shows only as "expected rc 1, got rc 1", is how an arm list ends
# up reading as a red on a gate that behaved correctly.

def _fixture(
    scratch: Path,
    label: str,
    results: Sequence[Tuple[str, int]],
    names: Sequence[str],
    entries: Optional[List[dict]] = None,
    budget: Optional[dict] = None,
    precondition_path: Optional[str] = None,
    write_budget: bool = True,
) -> Tuple[Path, Path, Path]:
    root = scratch / label
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    results_path = root / "results.tsv"
    results_path.write_text(
        "".join("%s\t%d\t0\t%s.out\n" % (n, rc, n) for n, rc in results), encoding="utf-8"
    )
    names_path = root / "names.txt"
    names_path.write_text("".join("%s\n" % n for n in names), encoding="utf-8")
    budget_path = root / "skip-budget.json"
    if write_budget:
        doc = budget if budget is not None else {"schema": BUDGET_SCHEMA, "entries": entries or []}
        budget_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    if precondition_path:
        target = root / precondition_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("present\n", encoding="utf-8")
    return root, results_path, names_path


def _case(scratch: Path, label: str, expected_rc: int, needle: Optional[str], **kwargs) -> bool:
    """Run one fixture. A needle of None means "the ratchet must print nothing"."""
    root, results_path, names_path = _fixture(scratch, label, **kwargs)
    budget_path = root / "skip-budget.json"
    json_out = root / "record.json"
    note_out = root / "note.txt"
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--root", str(root),
            "--results", str(results_path),
            "--names", str(names_path),
            "--budget", str(budget_path),
            "--json-out", str(json_out),
            "--note-out", str(note_out),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    actual = (proc.stdout + proc.stderr).strip().splitlines()
    if needle is None:
        matched = not actual
        expectation = "silence"
    else:
        matched = any(needle in line for line in actual)
        expectation = repr(needle)
    ok = proc.returncode == expected_rc and matched
    print(
        "  %-4s %-42s expect rc=%d naming %s -> rc=%d%s"
        % (
            "OK" if ok else "NO",
            label,
            expected_rc,
            expectation,
            proc.returncode,
            "" if matched else " (no line met the expectation)",
        )
    )
    if not ok:
        for line in actual:
            print("         actual: %s" % line)
    return ok


def self_test() -> int:
    scratch = Path(
        tempfile.mkdtemp(
            prefix="ao1199-skip-ratchet.",
            dir=os.environ.get("AO_SKIP_RATCHET_SCRATCH", "/tmp"),
        )
    )
    results: List[bool] = []
    try:
        print("skip-ratchet self-test (issue #1199) -- every rule provoked:")
        results.append(_case(scratch, "clean-all-assessed", 0, None, results=[("a", 0), ("b", 0)], names=["a", "b"]))
        results.append(
            _case(
                scratch, "named-standing-gap-pass", 0, "standing gap tracked by #1176",
                results=[("paperclip-routines", 2)], names=["paperclip-routines"],
                entries=[{"check": "paperclip-routines", "kind": STANDING_GAP, "issue": 1176, "reason": "the dropped-entry tree"}],
            )
        )
        results.append(
            _case(
                scratch, "named-venue-absent-precondition", 0, "venue: vendor/CMR/sync absent",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{"check": "module-registry", "kind": VENUE, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
            )
        )
        # The COMMAND form of a venue precondition (#1361): the Cloud Build
        # container is not a worktree missing a path -- it is an image missing
        # TOOLS. Both directions are asserted, because one direction alone would
        # leave the guard free to pass regardless of what the venue supplies.
        results.append(
            _case(
                scratch, "venue-command-precondition-not-supplied", 0,
                "cannot run 'ao-no-such-tool-1361'",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{"check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "ao-no-such-tool-1361"},
                          "reason": "no gh in this container"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-precondition-names-its-issue", 0,
                "the open issue that tracks it is #1361",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{"check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "ao-no-such-tool-1361"},
                          "reason": "no gh in this container"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-precondition-supplied-refused", 1,
                "venue precondition present but 'branch-protection' still cannot assess",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{"check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "true"},
                          "reason": "the command IS runnable here"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-metacharacter-refused", 2,
                "carries shell metacharacter(s)",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{"check": "branch-protection", "kind": VENUE,
                          "precondition": {"command": "gh auth status | tee /tmp/x"},
                          "reason": "a shell line, not an argument vector"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-precondition-of-the-wrong-type-refused", 2,
                "must be a repo-relative path or a",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{"check": "module-registry", "kind": VENUE,
                          "precondition": 42, "reason": "neither form"}],
            )
        )
        results.append(_case(scratch, "unnamed-skip-refused", 1, "unnamed skip 'newsurface'", results=[("newsurface", 2)], names=["newsurface"]))
        results.append(
            _case(
                scratch, "stale-standing-gap-refused", 1, "stale skip exemption 'dispatch-queue'",
                results=[("dispatch-queue", 0)], names=["dispatch-queue"],
                entries=[{"check": "dispatch-queue", "kind": STANDING_GAP, "issue": 1189, "reason": "six queued-but-closed issues"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-precondition-present-but-blind-refused", 1,
                "venue precondition present but 'module-registry' still cannot assess",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{"check": "module-registry", "kind": VENUE, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
                precondition_path="vendor/CMR/sync",
            )
        )
        results.append(
            _case(
                scratch, "entry-for-undiscovered-check-refused", 1, "not a check this run discovered",
                results=[("a", 0)], names=["a"],
                entries=[{"check": "ghost", "kind": STANDING_GAP, "issue": 42, "reason": "a check that no longer exists"}],
            )
        )
        results.append(
            _case(
                scratch, "malformed-budget-is-cannot-assess", 2, "has no 'entries' list",
                results=[("a", 2)], names=["a"], budget={"schema": BUDGET_SCHEMA, "entries": "not-a-list"},
            )
        )
        results.append(
            _case(
                scratch, "wrong-schema-budget-is-cannot-assess", 2, "declares schema",
                results=[("a", 0)], names=["a"], budget={"entries": []},
            )
        )
        results.append(
            _case(
                scratch, "missing-budget-is-fail-closed", 1, "unnamed skip 'a'",
                results=[("a", 2)], names=["a"], write_budget=False,
            )
        )
        results.append(
            _case(
                scratch, "unused-venue-entry-reported-not-failed", 0, "did not bite in this run",
                results=[("module-registry", 0)], names=["module-registry"],
                entries=[{"check": "module-registry", "kind": VENUE, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
            )
        )
        results.append(
            _case(
                scratch, "standing-gap-without-issue-refused", 2, "standing-gap with no open issue",
                results=[("a", 2)], names=["a"],
                entries=[{"check": "a", "kind": STANDING_GAP, "reason": "no ticket named"}],
            )
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    failed = [i for i, ok in enumerate(results) if not ok]
    if failed:
        print("skip-ratchet self-test: NOT-OK -- %d of %d provoked cases failed" % (len(failed), len(results)), file=sys.stderr)
        return VIOLATION
    print("skip-ratchet self-test: OK -- %d provoked case(s), each rc and each refusal line asserted" % len(results))
    return OK


# --- CLI ----------------------------------------------------------------------

def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--root", default=".")
    parser.add_argument("--results")
    parser.add_argument("--names")
    parser.add_argument("--budget")
    parser.add_argument("--json-out")
    parser.add_argument("--note-out")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    if not args.results or not args.names:
        print("usage: skip-ratchet.py --results R.tsv --names N.txt [--budget B.json]", file=sys.stderr)
        return CANNOT_ASSESS
    budget_path = Path(args.budget) if args.budget else root / DEFAULT_BUDGET
    try:
        return run(
            root,
            Path(args.results),
            Path(args.names),
            budget_path,
            Path(args.json_out) if args.json_out else None,
            Path(args.note_out) if args.note_out else None,
        )
    except CannotAssess as exc:
        print("verify: skip ratchet CANNOT-ASSESS -- %s" % exc, file=sys.stderr)
        _write(
            Path(args.json_out) if args.json_out else None,
            {
                "budget": str(budget_path),
                "budget_entries": 0,
                "verdict": "CANNOT-ASSESS",
                "standing_skips": [],
                "unbudgeted_skips": [],
                "stale_entries": [],
                "unused_venue_entries": [],
                "findings": [str(exc)],
            },
        )
        _write_text(
            Path(args.note_out) if args.note_out else None,
            "; skip ratchet CANNOT-ASSESS -- the skip set could not be evaluated",
        )
        return CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
