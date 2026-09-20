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
               A name carries a `standing-gap` entry (the inputs ARE present and
               it still cannot assess -- #1176's argument -- so it must point at
               the open issue that tracks it), a `venue` entry (it cannot assess
               because a NAMED PRECONDITION of this venue is not supplied: either
               a repo-relative PATH that is absent, e.g. `vendor/CMR/sync` in a
               worktree whose submodule was never initialised, or a COMMAND the
               venue must be able to run, e.g. `{"command": "gh auth status"}` in
               a container that installs no `gh` -- #1361), or a
               `live-dependent` entry (its ASSESSABILITY is a property of the
               WORLD -- the LIVE mechanism it reads did not answer, and the
               entry's `mechanism` names that mechanism -- #1410, below). A
               skipped check with no entry is REFUSED by name: the composite
               will not publish a PASS whose skip set is narrated by nobody.
  * HONEST     a `live-dependent` entry is honoured BY NAME while its check
               cannot assess for a LIVE-dependent reason, and it SAYS SO: the
               standing line names the LIVE mechanism that did not answer and the
               open issue that tracks it, and it declares the CHECK not the thing
               to repair (#1410). That declaration is the whole reason the kind
               exists -- once a `venue` precondition WAS supplied and the check
               still could not assess, the refusal told the operator to repair a
               check that was behaving correctly, which is a mislabel wearing a
               diagnosis, not a diagnosis. Two consequences, both mechanical: a
               `live-dependent` entry whose check ASSESSES is NOT stale (it is
               reported as not biting, never failed -- the mechanism answered,
               which is the entry's own remedy), while one whose check FAILS
               (rc 1) is REFUSED by name, because a FAILING check is a FINDING,
               not a skip, and honouring it would hide a defect behind the name
               of a mechanism that answered. It declares no `precondition`: a
               precondition is a named thing THIS VENUE supplies, and this kind's
               blindness is not this venue's to supply. The shrink-only property
               is kept -- it must name the OPEN issue that tracks the mechanism.
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

  * LEASED     every entry carries `tracked_by` (the `#<n>` spelling of its
               `issue`) and a `tracking` block -- `state`, `measured_at`,
               `measured_by`, `measured_via`, `max_age_hours` -- and is honoured
               only while that block says the tracking issue is OPEN and the
               measurement is younger than its lease (#1499). The shape is
               BORROWED, not invented: `governance/reconcile/
               real-tree-quarantine.json` + `QuarantineLease`
               (`real_tree_baseline.py`) carry exactly this pair and honour
               nothing when it does not hold, which is what let #1338 retire all
               six of its exemptions the moment the evidence resolved them. The
               defect it closes here was MEASURED: all 13 of this record's
               entries named an issue as their open tracker (#1361, then #1382)
               and both had been CLOSED while the composite still read PASS, so
               the gate was honouring an exemption whose tracker was shut. A
               `state` that is not `open`, and a measurement older than its
               declared lease, are each REFUSED by name; a MISSING or MALFORMED
               block is CANNOT-ASSESS, because a declaration that cannot be read
               is never read as "still excused". The lease is checked on EVERY
               entry, not only on the ones that bit: the record's claim is "this
               exemption is leased to an OPEN issue", and a claim that is false
               is false whether or not this venue happened to exercise it.

  Fail-closed everywhere: a MISSING budget file means "no exemptions" (every
  skip is then unnamed and refused); a MALFORMED one is CANNOT-ASSESS, never a
  silent no-exemption; an entry naming a check that is not discovered is
  refused by name.

Usage:
  python3 scripts/lib/skip-ratchet.py --results .verify/.results.tsv \
      --names .verify/.check-names.txt [--budget scripts/skip-budget.json] \
      [--root DIR] [--json-out FILE] [--note-out FILE] [--now ISO8601]
  python3 scripts/lib/skip-ratchet.py --self-test

Exit codes: 0 OK (every skip named, no stale entry, no lapsed lease) / 1 RATCHET
VIOLATION (refused by name) / 2 CANNOT-ASSESS (the skip set could not be
evaluated).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_BUDGET = "scripts/skip-budget.json"
BUDGET_SCHEMA = "ao.verify.skip-budget/v1"

STANDING_GAP = "standing-gap"
VENUE = "venue"
# The third kind (#1410): the check's ASSESSABILITY depends on a LIVE mechanism
# that must answer. An authenticated-but-unreadable GitHub source is a property
# of the WORLD, not a defect of the check -- so the refusal must not tell the
# operator to repair a check that is behaving correctly, which is exactly what
# the `venue` kind did once its named precondition WAS supplied (#1394's measured
# residual: gh answered, the API read did not, and the red was right while the
# reason was wrong). Honoured BY NAME, because there is no precondition left to
# measure; NOT stale when the check assesses; still REFUSED when the check FAILS,
# because a failing check is a finding, not a skip.
LIVE_DEPENDENT = "live-dependent"
KINDS = (STANDING_GAP, VENUE, LIVE_DEPENDENT)

# --- the lease on an exemption (issue #1499) ----------------------------------
#
# An exemption is a claim about an OPEN issue. Until #1499 nothing held the
# "open" half of that claim: `scripts/skip-budget.json` could name an issue that
# had been CLOSED for a day and the composite still read PASS. Measured: all 13
# entries named a tracker (#1361 for the 11 `venue` entries, #1382 for the two
# `live-dependent` ones) and BOTH were closed -- #1361 at 2026-09-19T01:03:11Z,
# #1382 at 2026-09-19T15:50:08Z -- while the venue's run was green.
#
# The fix BORROWS the shape this repository already solved it with rather than
# inventing a second vocabulary: `governance/reconcile/real-tree-quarantine.json`
# pairs a `tracked_by` with a `tracking` block, and
# `governance/reconcile/real_tree_baseline.py`'s `QuarantineLease.refusal()`
# honours nothing unless `state == "open"` and the measurement is younger than
# `max_age_hours`. The same two conditions, in the same order, with the same
# remedies -- a recorded state with no term is a memory, and a memory cannot be
# wrong.
TRACKING_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
#: A lease inside its last quarter is REPORTED on every run (a note, never a
#: failure): the term is coming, and the honest moment to re-measure is before
#: it lapses rather than after the gate has already refused.
LEASE_RENEW_NOTE_FRACTION = 0.25
TRACKED_BY_PATTERN = re.compile(r"#([0-9]+)")

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


# --- the lease: the two conditions, borrowed from the quarantine (#1499) -------

def parse_utc(text: str) -> float:
    """Seconds since the epoch for a `%Y-%m-%dT%H:%M:%SZ` stamp.

    The format is the QUARANTINE's (`real_tree_baseline.parse_lease`), not a new
    one: the record must not carry two spellings of the same instant.
    """
    stamp = datetime.datetime.strptime(text, TRACKING_TIME_FORMAT)
    return stamp.replace(tzinfo=datetime.timezone.utc).timestamp()


def format_utc(at: float) -> str:
    """The `%Y-%m-%dT%H:%M:%SZ` spelling of `at` -- the write half of parse_utc."""
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime(
        TRACKING_TIME_FORMAT
    )


def tracked_issue(entry: dict) -> Optional[int]:
    """The issue `tracked_by` names, or None when it names none."""
    value = entry.get("tracked_by")
    if not isinstance(value, str):
        return None
    match = TRACKED_BY_PATTERN.fullmatch(value.strip())
    return int(match.group(1)) if match else None


def tracking_age_hours(entry: dict, *, at: float) -> float:
    """How old the lease's measurement is. Only callable on a shape-checked entry."""
    return (at - parse_utc(entry["tracking"]["measured_at"])) / 3600.0


def lease_refusal(entry: dict, *, at: float) -> str:
    """``""`` when the lease holds; else the reason no entry may be honoured.

    The same two conditions and the same remedies as
    ``real_tree_baseline.QuarantineLease.refusal()``: the tracker must be OPEN,
    and the measurement of that fact must be younger than the declared lease.
    Only callable once `load_budget` has accepted the entry's shape -- a
    malformed lease never reaches here, it is a CANNOT-ASSESS finding instead.
    """
    tracked_by = entry["tracked_by"]
    state = str(entry["tracking"]["state"]).strip().lower()
    if state != "open":
        return (
            "the tracking issue %s is %s, not open -- an exemption is leased to the "
            "OPEN issue that tracks it, so one whose tracker is shut may not be "
            "honoured: re-point it at the live issue that now tracks the gap, or "
            "retire the entry and let the venue red on its own missing "
            "preconditions" % (tracked_by, state or "unstated")
        )
    age_hours = tracking_age_hours(entry, at=at)
    max_age_hours = float(entry["tracking"]["max_age_hours"])
    if age_hours > max_age_hours:
        return (
            "the tracking measurement is %.1fh old (> %gh lease); re-measure %s and "
            "record it, or retire the exemption"
            % (age_hours, max_age_hours, tracked_by)
        )
    return ""


def lease_note(entry: dict, *, at: float) -> str:
    """A NOTE for a lease inside its last quarter -- never a failure."""
    max_age_hours = float(entry["tracking"]["max_age_hours"])
    age_hours = tracking_age_hours(entry, at=at)
    if age_hours < max_age_hours * (1.0 - LEASE_RENEW_NOTE_FRACTION):
        return ""
    return "%s (%s, %.0fh of %gh)" % (
        entry["check"],
        entry["tracked_by"],
        age_hours,
        max_age_hours,
    )


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
                issue = None
        elif kind == VENUE:
            findings.extend(precondition_findings(i, name, entry.get("precondition")))
            issue = entry.get("issue")
            if not (
                isinstance(issue, int) and not isinstance(issue, bool) and issue > 0
            ):
                # `issue` was OPTIONAL for a venue entry until #1499 and is not
                # any more: the kind's own contract already named "the open issue
                # that tracks it", and an exemption with no tracker is exactly the
                # record that could be honoured after its tracker closed.
                findings.append(
                    "entry[%d] (%s) is a venue with no open issue number -- every "
                    "exemption is leased to the OPEN issue that tracks the gap, so "
                    "the number is required here too (issue #1499)" % (i, name)
                )
                issue = None
        else:
            findings.extend(live_dependent_findings(i, name, entry))
            declared = entry.get("issue")
            issue = (
                declared
                if isinstance(declared, int) and not isinstance(declared, bool)
                else None
            )
        findings.extend(tracked_by_findings(i, name, entry, issue))
        findings.extend(tracking_findings(i, name, entry))
    return entries, findings


def tracked_by_findings(
    index: int, name: str, entry: dict, issue: Optional[int]
) -> List[str]:
    """The tracker this exemption is leased to, and its agreement with `issue`.

    One tracker per entry, spelled once: `tracked_by` is the quarantine's key and
    has to agree with the entry's own `issue`, so a re-point cannot half-land
    (#1499). That coherence rule is the whole reason the borrowed key does not
    become a second vocabulary here.
    """
    tracked_by = entry.get("tracked_by")
    if not isinstance(tracked_by, str) or not tracked_by.strip():
        return [
            "entry[%d] (%s) carries no 'tracked_by' -- an exemption must name the "
            "OPEN issue that tracks it, spelled the quarantine's way ('#<n>'), or "
            "it cannot be leased to anything (issue #1499)" % (index, name)
        ]
    # The ONE parse of the tracker (tracked_issue), so the agreement rule below
    # cannot read a different number than the rest of the module.
    declared_issue = tracked_issue(entry)
    if declared_issue is None:
        return [
            "entry[%d] (%s) declares tracked_by %r, which is not a '#<n>' issue "
            "reference" % (index, name, tracked_by)
        ]
    if issue is not None and declared_issue != issue:
        return [
            "entry[%d] (%s) declares tracked_by %r while naming issue %s -- one "
            "tracker per entry, spelled once: the two halves must agree, so a "
            "re-point cannot half-land (issue #1499)"
            % (index, name, tracked_by, issue)
        ]
    return []


def tracking_findings(index: int, name: str, entry: dict) -> List[str]:
    """Shape findings for one entry's lease (#1499).

    Borrowed wholesale from the quarantine: the declaration is `tracked_by` plus
    a `tracking` block, and a MISSING or MALFORMED one is a FINDING -- never a
    silent "still excused". `real_tree_baseline.py` raises
    `QuarantineUnavailable` for exactly this family of shapes; the direction is
    the same one every CANNOT-ASSESS decision in this package takes.
    """
    tracking = entry.get("tracking")
    if not isinstance(tracking, dict):
        return [
            "entry[%d] (%s) carries no 'tracking' block -- an exemption is HONOURED "
            "only while its tracking issue is measured OPEN and that measurement is "
            "younger than its lease, so a missing declaration is a finding rather "
            "than a silent 'still excused' (the quarantine's shape, issue #1499)"
            % (index, name)
        ]
    findings: List[str] = []
    state = tracking.get("state")
    if not isinstance(state, str) or not state.strip():
        findings.append(
            "entry[%d] (%s) tracking.state must be a non-empty string naming the "
            "issue's state AS IT WAS READ (e.g. 'open') -- the lease is refused "
            "when it is not 'open', so an unstated state cannot be honoured"
            % (index, name)
        )
    measured_at = tracking.get("measured_at")
    if not isinstance(measured_at, str) or not measured_at.strip():
        findings.append(
            "entry[%d] (%s) tracking.measured_at must be the UTC timestamp of the "
            "read, formatted '%s'" % (index, name, TRACKING_TIME_FORMAT)
        )
    else:
        try:
            parse_utc(measured_at)
        except ValueError:
            findings.append(
                "entry[%d] (%s) tracking.measured_at %r is not '%s' -- a lease "
                "cannot be measured from a timestamp nothing can parse"
                % (index, name, measured_at, TRACKING_TIME_FORMAT)
            )
    for key, why in (
        ("measured_by", "WHO took the measurement"),
        ("measured_via", "HOW it was taken (the read whose answer is recorded)"),
    ):
        value = tracking.get(key)
        if not isinstance(value, str) or not value.strip():
            findings.append(
                "entry[%d] (%s) tracking.%s must be a non-empty string naming %s"
                % (index, name, key, why)
            )
    max_age_hours = tracking.get("max_age_hours")
    if (
        not isinstance(max_age_hours, (int, float))
        or isinstance(max_age_hours, bool)
        or max_age_hours <= 0
    ):
        findings.append(
            "entry[%d] (%s) tracking.max_age_hours must be a positive number -- the "
            "term of the lease, after which the recorded state must be re-measured"
            % (index, name)
        )
    return findings


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


def live_dependent_findings(index: int, name: str, entry: dict) -> List[str]:
    """Shape findings for one `live-dependent` entry (#1410).

    Three rules, each of which the run REFUSES rather than narrates: the entry
    must name the OPEN issue that tracks the mechanism (the shrink-only
    property), it must name WHICH live mechanism failed to answer (what makes the
    honouring honest instead of a shrug), and it must NOT carry a precondition --
    a precondition is a named thing THIS VENUE supplies, so an entry carrying one
    is a `venue` entry mislabelled, and this kind's whole point is that its
    blindness is not this venue's to supply.
    """
    findings: List[str] = []
    issue = entry.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        findings.append(
            "entry[%d] (%s) is live-dependent with no open issue -- the kind is "
            "honoured BY NAME while the LIVE mechanism cannot answer, so it must "
            "name the OPEN issue that tracks that mechanism (the list can only "
            "shrink, and it cannot outlive its fix)" % (index, name)
        )
    mechanism = entry.get("mechanism")
    if not isinstance(mechanism, str) or not mechanism.strip():
        findings.append(
            "entry[%d] (%s) is live-dependent with no 'mechanism' -- the honest "
            "expression of this kind is WHICH live mechanism did not answer (e.g. "
            "'the GitHub commit statuses API, read with gh'), so the standing line "
            "can name the world that is silent instead of the check that is right"
            % (index, name)
        )
    if "precondition" in entry:
        findings.append(
            "entry[%d] (%s) is live-dependent and declares a precondition %r -- "
            "this kind is honoured BY NAME, not by a measured precondition of this "
            "venue: a check whose blindness is a named thing the venue supplies is "
            "a `venue` entry" % (index, name, entry["precondition"])
        )
    return findings


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
    tracking = entry.get("tracking") if isinstance(entry.get("tracking"), dict) else {}
    return {
        "check": entry["check"],
        "kind": kind,
        "issue": entry.get("issue") if isinstance(entry.get("issue"), int) else None,
        # The lease (#1499): the borrowed pair, carried into the attestation so
        # the board reads WHY the exemption was honoured from the record itself
        # rather than from the prose around it, and so a reader can see the
        # tracker's state and the age of the read that established it.
        "tracked_by": entry.get("tracked_by"),
        "tracking_state": tracking.get("state"),
        "tracking_measured_at": tracking.get("measured_at"),
        "tracking_max_age_hours": tracking.get("max_age_hours"),
        # The LIVE mechanism this entry names (#1410): WHICH part of the world did
        # not answer. Carried into the attestation so the board reads why a check
        # sat in the skip bucket from the record rather than from prose.
        "mechanism": entry.get("mechanism") if kind == LIVE_DEPENDENT else None,
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

def live_dependent_line(name: str, record: dict) -> str:
    """The honoured-by-name line for a `live-dependent` skip (#1410).

    It names the LIVE mechanism that did not answer and the open issue that tracks
    it, and it says plainly that the CHECK is not the thing to repair -- that
    declaration is the reason this kind exists. The `venue` kind's refusal, once
    its precondition WAS supplied, told the operator to repair the check (#1176's
    own wording); for a check whose blindness is the WORLD's, that is a mislabel
    wearing a diagnosis.
    """
    tracked = (
        " (the open issue that tracks the mechanism is #%s)" % record["issue"]
        if record.get("issue")
        else ""
    )
    return (
        "verify: standing skip %s -- live-dependent: the LIVE mechanism %r did not "
        "answer here, so the check cannot assess -- a property of the WORLD, not a "
        "defect of the check, and the check is honoured BY NAME rather than "
        "repaired%s" % (name, record.get("mechanism") or "?", tracked)
    )


def evaluate(
    root: Path,
    results: Sequence[Tuple[str, int]],
    names: Sequence[str],
    entries: Sequence[dict],
    budget_label: str,
    *,
    at: float,
) -> Tuple[dict, int, List[str]]:
    """Apply the rules. Returns (record, rc, stdout lines).

    `at` is the clock the leases are measured against, passed in rather than read
    here so a fixture can pin the moment it is evaluating (the determinism rule
    the clock/date-bomb class taught, #1025).
    """
    discovered = set(names)
    skipped = [(n, rc) for n, rc in results if rc == 2]
    observed = {n for n, _ in results}
    by_check = {e["check"]: e for e in entries}

    standing: List[dict] = []
    unbudgeted: List[str] = []
    stale: List[dict] = []
    unused_venue: List[str] = []
    unused_live: List[str] = []
    refused: List[str] = []
    lease_notes: List[str] = []
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
        elif entry["kind"] == LIVE_DEPENDENT:
            lines.append(live_dependent_line(name, record))
        else:
            lines.append(
                "verify: standing skip %s -- standing gap tracked by #%s: %s"
                % (name, record["issue"], record["reason"])
            )

    for entry in entries:
        name = entry["check"]
        # The lease (#1499), checked FIRST and on EVERY entry -- not only on the
        # ones that bit. The record's claim is "this exemption is leased to an
        # OPEN issue"; a claim that is false is false whether or not this venue
        # happened to exercise it, and a tracker that closed is precisely the
        # fact nobody notices while only the skipping entries are inspected.
        lease_problem = lease_refusal(entry, at=at)
        if lease_problem:
            refused.append(
                "entry '%s' is not leased to an open tracker: %s" % (name, lease_problem)
            )
        else:
            renewing = lease_note(entry, at=at)
            if renewing:
                lease_notes.append(renewing)
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
        elif entry["kind"] == LIVE_DEPENDENT:
            # It did NOT skip, so its LIVE mechanism answered. The split here is
            # the point of the kind (#1410): an ASSESSING check (rc 0) does NOT
            # stale the entry -- the mechanism came back, which is the entry's own
            # remedy -- while a FAILING check is a FINDING and is refused by name,
            # because honouring it would hide a defect behind the name of a
            # mechanism that answered.
            if rc == 0:
                unused_live.append(name)
            else:
                record_here = entry_record(entry, root)
                refused.append(
                    "live-dependent entry '%s' -- its check does not skip here (%s): "
                    "the LIVE mechanism it names answered, so the blindness this "
                    "entry declares is over and a failing check is a FINDING, not a "
                    "skip; delete the entry from %s and repair the finding (the "
                    "mechanism %r is tracked by #%s)"
                    % (
                        name,
                        "rc %s" % rc if rc is not None else "no verdict row",
                        budget_label,
                        record_here.get("mechanism") or "?",
                        record_here.get("issue"),
                    )
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
        "unused_live_entries": unused_live,
        "lease_notes": lease_notes,
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
    at: Optional[float] = None,
) -> int:
    # The lease clock (#1499). Read ONCE, here, so every entry is judged against
    # the same instant and a long run cannot straddle a lease boundary.
    if at is None:
        at = time.time()
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
            "unused_live_entries": [],
            "lease_notes": [],
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

    record, rc, lines = evaluate(root, results, names, entries, budget_label, at=at)
    for line in lines:
        print(line)
    for finding in record["findings"]:
        print("verify: skip ratchet FAIL -- %s" % finding, file=sys.stderr)
    if record["lease_notes"]:
        print(
            "verify: skip ratchet note -- %d entr(y/ies) are inside the last %d%% of "
            "their tracking lease; re-measure the tracking issue and record it before "
            "the lease lapses (they are REFUSED the moment it does): %s"
            % (
                len(record["lease_notes"]),
                int(LEASE_RENEW_NOTE_FRACTION * 100),
                ", ".join(record["lease_notes"]),
            )
        )
    if record["unused_venue_entries"]:
        print(
            "verify: skip ratchet note -- %d venue entr(y/ies) did not bite in this "
            "run (their precondition is present here): %s"
            % (len(record["unused_venue_entries"]), ", ".join(record["unused_venue_entries"]))
        )
    if record["unused_live_entries"]:
        print(
            "verify: skip ratchet note -- %d live-dependent entr(y/ies) did not bite "
            "in this run (their check assessed, so the LIVE mechanism answered and "
            "this kind does NOT go stale -- the entry is reported, never a failure, "
            "and is deleted when the issue that tracks the mechanism closes): %s"
            % (len(record["unused_live_entries"]), ", ".join(record["unused_live_entries"]))
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


def _lease(
    issue: int,
    *,
    state: str = "open",
    age_hours: float = 0.0,
    max_age_hours: float = 720.0,
) -> dict:
    """The borrowed lease block, derived from the LIVE clock.

    Derived rather than written as a date literal: a fixture that pins a date
    while the subject reads the live clock is green the day it is written and red
    every day after (the clock/date-bomb class, #1025). `age_hours` is relative to
    now, so even the EXPIRY arm is deterministic on every day it runs.
    """
    return {
        "tracked_by": "#%d" % issue,
        "tracking": {
            "state": state,
            "measured_at": format_utc(time.time() - age_hours * 3600.0),
            "measured_by": "scripts/lib/skip-ratchet.py self-test fixture",
            "measured_via": "fixture: the entry's own issue number, read at the fixture's clock",
            "max_age_hours": max_age_hours,
        },
    }


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
                entries=[{**_lease(1176), "check": "paperclip-routines", "kind": STANDING_GAP, "issue": 1176, "reason": "the dropped-entry tree"}],
            )
        )
        results.append(
            _case(
                scratch, "named-venue-absent-precondition", 0, "venue: vendor/CMR/sync absent",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295), "check": "module-registry", "kind": VENUE, "issue": 1295, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
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
                entries=[{**_lease(1361), "check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "ao-no-such-tool-1361"},
                          "reason": "no gh in this container"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-precondition-names-its-issue", 0,
                "the open issue that tracks it is #1361",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{**_lease(1361), "check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "ao-no-such-tool-1361"},
                          "reason": "no gh in this container"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-precondition-supplied-refused", 1,
                "venue precondition present but 'branch-protection' still cannot assess",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{**_lease(1361), "check": "branch-protection", "kind": VENUE, "issue": 1361,
                          "precondition": {"command": "true"},
                          "reason": "the command IS runnable here"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-command-metacharacter-refused", 2,
                "carries shell metacharacter(s)",
                results=[("branch-protection", 2)], names=["branch-protection"],
                entries=[{**_lease(1295), "check": "branch-protection", "kind": VENUE,
                          "precondition": {"command": "gh auth status | tee /tmp/x"},
                          "reason": "a shell line, not an argument vector"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-precondition-of-the-wrong-type-refused", 2,
                "must be a repo-relative path or a",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295), "check": "module-registry", "kind": VENUE,
                          "precondition": 42, "reason": "neither form"}],
            )
        )
        results.append(_case(scratch, "unnamed-skip-refused", 1, "unnamed skip 'newsurface'", results=[("newsurface", 2)], names=["newsurface"]))
        results.append(
            _case(
                scratch, "stale-standing-gap-refused", 1, "stale skip exemption 'dispatch-queue'",
                results=[("dispatch-queue", 0)], names=["dispatch-queue"],
                entries=[{**_lease(1189), "check": "dispatch-queue", "kind": STANDING_GAP, "issue": 1189, "reason": "six queued-but-closed issues"}],
            )
        )
        results.append(
            _case(
                scratch, "venue-precondition-present-but-blind-refused", 1,
                "venue precondition present but 'module-registry' still cannot assess",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295), "check": "module-registry", "kind": VENUE, "issue": 1295, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
                precondition_path="vendor/CMR/sync",
            )
        )
        results.append(
            _case(
                scratch, "entry-for-undiscovered-check-refused", 1, "not a check this run discovered",
                results=[("a", 0)], names=["a"],
                entries=[{**_lease(42), "check": "ghost", "kind": STANDING_GAP, "issue": 42, "reason": "a check that no longer exists"}],
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
                entries=[{**_lease(1295), "check": "module-registry", "kind": VENUE, "issue": 1295, "precondition": "vendor/CMR/sync", "reason": "the pin is unreadable here"}],
            )
        )
        results.append(
            _case(
                scratch, "standing-gap-without-issue-refused", 2, "standing-gap with no open issue",
                results=[("a", 2)], names=["a"],
                entries=[{"check": "a", "kind": STANDING_GAP, "reason": "no ticket named"}],
            )
        )
        # --- the THIRD kind (#1410): the check's assessability is the world's -----
        # Its whole reason for existing is that a REFUSAL was mislabelling a check
        # that was behaving correctly, so the rules are provoked in both
        # directions: the honoured-by-name line (which must name the LIVE
        # mechanism AND the open issue), the NOT-stale half (an assessing check is
        # never a stale entry for this kind), and the refusal when the check itself
        # FAILS -- a failing check is a finding, not a skip.
        LIVE_ENTRY = {
            **_lease(1382),
            "check": "gate-status",
            "kind": LIVE_DEPENDENT,
            "issue": 1382,
            "mechanism": "the GitHub commit statuses API, read with gh",
            "reason": "an authenticated-but-unreadable source is a property of the world",
        }
        results.append(
            _case(
                scratch, "live-dependent-skip-honoured-by-name", 0,
                "live-dependent: the LIVE mechanism",
                results=[("gate-status", 2)], names=["gate-status"], entries=[LIVE_ENTRY],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-skip-names-the-world", 0,
                "a property of the WORLD, not a defect of the check",
                results=[("gate-status", 2)], names=["gate-status"], entries=[LIVE_ENTRY],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-skip-names-the-open-issue", 0,
                "the open issue that tracks the mechanism is #1382",
                results=[("gate-status", 2)], names=["gate-status"], entries=[LIVE_ENTRY],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-assessing-is-NOT-stale", 0,
                "1 live-dependent entr(y/ies) did not bite",
                results=[("gate-status", 0)], names=["gate-status"], entries=[LIVE_ENTRY],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-failing-check-refused", 1,
                "a failing check is a FINDING, not a skip",
                results=[("gate-status", 1)], names=["gate-status"], entries=[LIVE_ENTRY],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-without-issue-refused", 2,
                "live-dependent with no open issue",
                results=[("gate-status", 2)], names=["gate-status"],
                entries=[{"check": "gate-status", "kind": LIVE_DEPENDENT,
                          "mechanism": "the live statuses source",
                          "reason": "no ticket named"}],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-without-mechanism-refused", 2,
                "is live-dependent with no 'mechanism'",
                results=[("gate-status", 2)], names=["gate-status"],
                entries=[{**_lease(1382), "check": "gate-status", "kind": LIVE_DEPENDENT,
                          "issue": 1382, "reason": "no mechanism named"}],
            )
        )
        results.append(
            _case(
                scratch, "live-dependent-with-a-precondition-refused", 2,
                "is live-dependent and declares a precondition",
                results=[("gate-status", 2)], names=["gate-status"],
                entries=[{**_lease(1382), "check": "gate-status", "kind": LIVE_DEPENDENT, "issue": 1382,
                          "mechanism": "the live statuses source",
                          "precondition": {"command": "gh auth status"},
                          "reason": "a venue entry mislabelled"}],
            )
        )
        # --- the LEASE (#1499), borrowed from the quarantine --------------------
        # An exemption is honoured only while its tracker is OPEN and the
        # measurement of that fact is younger than the term it declares. Both
        # directions are provoked, plus the fail-closed shape: a MISSING or
        # disagreeing declaration is CANNOT-ASSESS rather than a silent "still
        # excused". Every state below is CLOCK-DERIVED (`_lease`), so no arm can
        # go stale by itself -- and the expiry arm is a RELATIVE age, so it is
        # deterministic on the day it runs and every day after.
        LEASE_VENUE = {
            "check": "module-registry",
            "kind": VENUE,
            "issue": 1295,
            "precondition": "vendor/CMR/sync",
            "reason": "the pin is unreadable in this venue",
        }
        results.append(
            _case(
                scratch, "lease-closed-tracker-refused", 1,
                "the tracking issue #1361 is closed, not open",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1361, state="closed"), **LEASE_VENUE, "issue": 1361}],
            )
        )
        results.append(
            _case(
                scratch, "lease-closed-tracker-names-the-remedy", 1,
                "re-point it at the live issue that now tracks the gap",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1361, state="closed"), **LEASE_VENUE, "issue": 1361}],
            )
        )
        # The closed tracker is refused even when the entry did NOT bite. That is
        # the half a per-bite check would miss entirely: the 13 entries whose
        # tracker closed did not bite on a developer box (gh, docker and crontab
        # are all present here), so a lease checked only where a skip happened
        # would never have looked at them.
        results.append(
            _case(
                scratch, "lease-closed-tracker-refused-even-when-it-did-not-bite", 1,
                "is not leased to an open tracker",
                results=[("module-registry", 0)], names=["module-registry"],
                entries=[{**_lease(1361, state="closed"), **LEASE_VENUE, "issue": 1361}],
            )
        )
        results.append(
            _case(
                scratch, "lease-lapsed-measurement-refused", 1,
                "the tracking measurement is 100.0h old (> 1h lease)",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[
                    {**_lease(1295, age_hours=100.0, max_age_hours=1.0), **LEASE_VENUE}
                ],
            )
        )
        results.append(
            _case(
                scratch, "lease-inside-its-last-quarter-is-noted-not-failed", 0,
                "inside the last 25% of their tracking lease",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[
                    {**_lease(1295, age_hours=600.0, max_age_hours=720.0), **LEASE_VENUE}
                ],
            )
        )
        results.append(
            _case(
                scratch, "lease-missing-is-cannot-assess", 2,
                "carries no 'tracking' block",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{"check": "module-registry", "kind": VENUE, "issue": 1295,
                          "precondition": "vendor/CMR/sync",
                          "reason": "no lease declared at all"}],
            )
        )
        results.append(
            _case(
                scratch, "lease-without-tracked-by-is-cannot-assess", 2,
                "carries no 'tracked_by'",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295), **LEASE_VENUE, "tracked_by": None}],
            )
        )
        results.append(
            _case(
                scratch, "lease-disagreeing-with-issue-is-cannot-assess", 2,
                "the two halves must agree",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295), **LEASE_VENUE, "issue": 1361}],
            )
        )
        results.append(
            _case(
                scratch, "lease-with-an-unparseable-measurement-is-cannot-assess", 2,
                "a lease cannot be measured from a timestamp nothing can parse",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[
                    {
                        **_lease(1295),
                        **LEASE_VENUE,
                        "tracking": dict(
                            _lease(1295)["tracking"], measured_at="yesterday-ish"
                        ),
                    }
                ],
            )
        )
        results.append(
            _case(
                scratch, "lease-with-a-non-positive-term-is-cannot-assess", 2,
                "tracking.max_age_hours must be a positive number",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{**_lease(1295, max_age_hours=0.0), **LEASE_VENUE}],
            )
        )
        results.append(
            _case(
                scratch, "venue-entry-without-an-issue-refused", 2,
                "is a venue with no open issue number",
                results=[("module-registry", 2)], names=["module-registry"],
                entries=[{"tracked_by": "#1295", "tracking": _lease(1295)["tracking"],
                          "check": "module-registry", "kind": VENUE,
                          "precondition": "vendor/CMR/sync",
                          "reason": "no tracker named"}],
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
    parser.add_argument(
        "--now",
        help="the instant the leases are measured against, as "
        "'%%Y-%%m-%%dT%%H:%%M:%%SZ' (default: the live clock; env "
        "AO_SKIP_RATCHET_CLOCK is the seam a fixture uses)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    at: Optional[float] = None
    clock = args.now or os.environ.get("AO_SKIP_RATCHET_CLOCK")
    if clock:
        try:
            at = parse_utc(clock.strip())
        except ValueError:
            print(
                "verify: skip ratchet CANNOT-ASSESS -- --now %r is not '%s', so no "
                "lease can be measured against it" % (clock, TRACKING_TIME_FORMAT),
                file=sys.stderr,
            )
            return CANNOT_ASSESS

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
            at=at,
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
                "unused_live_entries": [],
                "lease_notes": [],
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
