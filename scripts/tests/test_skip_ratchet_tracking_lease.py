"""The skip ratchet's LEASE: an exemption whose tracking issue is closed is refused.

Issue #1499 measured the defect this suite pins: every one of `scripts/skip-budget.json`'s
13 entries named an issue as the OPEN tracker of its gap, and both of the issues named
(#1361, then #1382) had been closed -- while the composite still honoured the entries and
the venue's run read green. A skip whose tracker is shut, accepted as green, is a false
green of exactly the class #1268 exists to remove.

The fix BORROWS the shape `governance/reconcile/real-tree-quarantine.json` already solved
this class with: `tracked_by` plus a `tracking` block whose `state` must be `open` and whose
`measured_at` must be younger than `max_age_hours`. This suite is the independent,
out-of-process check of that contract, and it is deliberately written so that no arm can
pass for the wrong reason:

  * the state half is proved in BOTH directions -- a CLOSED tracker is refused by name
    while the SAME entry OPEN is accepted, because a rule that refuses everything is not
    the rule under test;
  * the lease is proved to be checked on EVERY entry, not only on the ones that skipped:
    on a developer box these entries do not bite at all (gh, docker and crontab are all
    present), so a lease consulted only where a skip happened would never have looked at
    the 13 whose trackers had closed;
  * the fail-closed shape is proved for a missing block, a disagreeing `tracked_by`, an
    unparseable instant and a non-positive term -- each CANNOT-ASSESS, never a silent
    "still excused";
  * the CLOCK is proved to be what decides: the same record, byte-identical, is green at
    one instant and refused at that instant plus its term. That is the property that
    replaces a memory about the world with a measurement of it -- and it is why every
    fixture here derives its timestamps from the live clock rather than pinning a date
    (a pinned date beside a live clock is green the day it is written and red every day
    after: the clock/date-bomb class, #1025).
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RATCHET = REPO / "scripts" / "lib" / "skip-ratchet.py"
BUDGET = REPO / "scripts" / "skip-budget.json"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

PINNED = "2026-09-20T13:14:34Z"


def _stamp(at: float) -> str:
    return datetime.datetime.fromtimestamp(at, datetime.timezone.utc).strftime(TIME_FORMAT)


def _epoch(text: str) -> float:
    stamp = datetime.datetime.strptime(text, TIME_FORMAT)
    return stamp.replace(tzinfo=datetime.timezone.utc).timestamp()


def _lease(
    issue: int,
    *,
    state: str = "open",
    age_hours: float = 0.0,
    max_age_hours: float = 720.0,
    at: str = PINNED,
) -> dict:
    """The borrowed lease block, measured RELATIVE to `at` so no arm goes stale."""
    return {
        "tracked_by": "#%d" % issue,
        "tracking": {
            "state": state,
            "measured_at": _stamp(_epoch(at) - age_hours * 3600.0),
            "measured_by": "scripts/tests/test_skip_ratchet_tracking_lease.py",
            "measured_via": "fixture: the entry's own issue number, at the arm's clock",
            "max_age_hours": max_age_hours,
        },
    }


def _entry(check: str, **overrides) -> dict:
    entry = {
        "check": check,
        "kind": "venue",
        "issue": 1295,
        "precondition": "vendor/CMR/sync",
        "reason": "the arm's own standing gap",
    }
    entry.update(overrides)
    if "tracked_by" not in entry and "tracking" not in entry:
        entry.update(_lease(entry["issue"]))
    return entry


def _drive(entries, *, results, now: str = PINNED):
    """Run the REAL ratchet against a scratch root. Returns (rc, output, record)."""
    with tempfile.TemporaryDirectory(prefix="ao1499-lease.") as scratch:
        root = Path(scratch)
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        budget = root / "scripts" / "skip-budget.json"
        budget.write_text(
            json.dumps({"schema": "ao.verify.skip-budget/v1", "entries": entries}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        results_path = root / "results.tsv"
        results_path.write_text(
            "".join("%s\t%d\t0\t%s.out\n" % (n, rc, n) for n, rc in results),
            encoding="utf-8",
        )
        names_path = root / "names.txt"
        names_path.write_text("".join("%s\n" % n for n, _ in results), encoding="utf-8")
        record_path = root / "record.json"
        note_path = root / "note.txt"
        proc = subprocess.run(
            [
                sys.executable,
                str(RATCHET),
                "--root",
                str(root),
                "--results",
                str(results_path),
                "--names",
                str(names_path),
                "--budget",
                str(budget),
                "--json-out",
                str(record_path),
                "--note-out",
                str(note_path),
                "--now",
                now,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
        return proc.returncode, proc.stdout + proc.stderr, record


def _findings(record: dict) -> str:
    return "\n".join(record.get("findings") or [])


# --- the state half, both directions -----------------------------------------

def test_a_closed_tracker_is_refused_by_name() -> None:
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1361, state="closed"), issue=1361)],
        results=[(check, 2)],
    )
    assert rc == 1, out
    assert record["verdict"] == "VIOLATION"
    assert "the tracking issue #1361 is closed, not open" in _findings(record)
    assert "re-point it at the live issue that now tracks the gap, or retire the entry" in _findings(record)


def test_the_same_entry_open_is_accepted() -> None:
    """The positive control: without it, "refuse everything" would satisfy the arm above."""
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1361, state="open"), issue=1361)],
        results=[(check, 2)],
    )
    assert rc == 0, out
    assert record["verdict"] == "OK"
    assert [s["check"] for s in record["standing_skips"]] == [check]


def test_a_closed_tracker_is_refused_even_when_the_entry_did_not_bite() -> None:
    """The lease is on EVERY entry, not only on the ones this venue exercised.

    On a developer box these entries do not bite at all -- gh, docker and crontab are
    present -- so a lease consulted only inside the skip loop would never have looked at
    the 13 whose trackers had closed, which is how the #1499 defect stayed invisible.
    """
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1361, state="closed"), issue=1361)],
        results=[(check, 0)],
    )
    assert rc == 1, out
    assert "is not leased to an open tracker" in _findings(record)
    # The entry did NOT bite here (its check assessed, so the venue supplied what it
    # needs) -- and it is refused anyway. The record says so in both places, which is
    # the point: a lease consulted only inside the skip loop would have said nothing.
    assert record["unused_venue_entries"] == [check]
    assert record["standing_skips"] == []


# --- the term of the lease ---------------------------------------------------

def test_a_lapsed_measurement_is_refused_and_names_its_age() -> None:
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1295, age_hours=100.0, max_age_hours=1.0))],
        results=[(check, 2)],
    )
    assert rc == 1, out
    assert "the tracking measurement is 100.0h old (> 1h lease)" in _findings(record)
    assert "re-measure #1295 and record it, or retire the exemption" in _findings(record)


def test_the_clock_is_what_decides_not_a_date_literal_in_the_record() -> None:
    """The SAME record is green at T and refused at T + term: the lease is a measurement.

    This is the arm that makes `state: open` a fact about the world rather than a memory
    of one, and it is why the committed record must be re-measured before its term lapses.
    """
    check = "module-registry"
    entry = _entry(check, **_lease(1295, max_age_hours=1.0))
    green_rc, _, green = _drive(
        [json.loads(json.dumps(entry))],
        results=[(check, 2)],
        now=PINNED,
    )
    assert green_rc == 0, green
    # One hour and one minute later the very same bytes are refused -- no edit, no diff.
    later = _stamp(_epoch(PINNED) + 3660)
    red_rc, _, red = _drive([json.loads(json.dumps(entry))], results=[(check, 2)], now=later)
    assert red_rc == 1, red
    assert "the tracking measurement is 1.0h old (> 1h lease)" in _findings(red)


def test_a_lease_inside_its_last_quarter_is_a_note_not_a_failure() -> None:
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1295, age_hours=600.0, max_age_hours=720.0))],
        results=[(check, 2)],
    )
    assert rc == 0, out
    assert record["lease_notes"], "a lease inside its last quarter must be reported"
    assert "inside the last 25% of their tracking lease" in out


# --- the fail-closed shape ---------------------------------------------------

def test_a_missing_lease_is_cannot_assess_not_a_silent_excuse() -> None:
    check = "module-registry"
    entry = _entry(check)
    entry.pop("tracked_by")
    entry.pop("tracking")
    rc, out, record = _drive([entry], results=[(check, 2)])
    assert rc == 2, out
    assert record["verdict"] == "CANNOT-ASSESS"
    assert "carries no 'tracking' block" in _findings(record)


def test_a_tracked_by_that_disagrees_with_the_issue_is_cannot_assess() -> None:
    check = "module-registry"
    entry = _entry(check, **_lease(1295))
    entry["issue"] = 1361
    rc, out, record = _drive([entry], results=[(check, 2)])
    assert rc == 2, out
    assert "the two halves must agree" in _findings(record)


def test_an_unparseable_measurement_is_cannot_assess() -> None:
    check = "module-registry"
    entry = _entry(check, **_lease(1295))
    entry["tracking"] = dict(entry["tracking"], measured_at="yesterday-ish")
    rc, out, record = _drive([entry], results=[(check, 2)])
    assert rc == 2, out
    assert "a lease cannot be measured from a timestamp nothing can parse" in _findings(record)


def test_a_non_positive_term_is_cannot_assess() -> None:
    check = "module-registry"
    rc, out, record = _drive(
        [_entry(check, **_lease(1295, max_age_hours=0.0))],
        results=[(check, 2)],
    )
    assert rc == 2, out
    assert "tracking.max_age_hours must be a positive number" in _findings(record)


def test_a_venue_entry_without_a_tracker_is_cannot_assess() -> None:
    """`issue` was optional for a venue entry until #1499; an unleased exemption now cannot load."""
    check = "module-registry"
    entry = _entry(check, **_lease(1295))
    entry.pop("issue")
    rc, out, record = _drive([entry], results=[(check, 2)])
    assert rc == 2, out
    assert "is a venue with no open issue number" in _findings(record)


# --- the shipped record ------------------------------------------------------

def _load_budget_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("skip_ratchet", RATCHET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_record_loads_with_no_shape_finding() -> None:
    module = _load_budget_module()
    entries, findings = module.load_budget(BUDGET)
    assert findings == [], findings
    assert len(entries) == 13, [e["check"] for e in entries]


def test_the_tracker_is_parsed_once_and_the_helper_is_what_reads_it() -> None:
    """`tracked_issue` is the module's ONE parse of the tracker (#1499).

    The agreement rule reads it, so it is asserted directly: a spelling the helper
    cannot parse must never be read as a number (which would let `tracked_by`
    disagree with `issue` in a shape a regex-with-`search` would have accepted).
    """
    module = _load_budget_module()
    assert module.tracked_issue({"tracked_by": "#1295"}) == 1295
    # Surrounding whitespace is not a different tracker ...
    assert module.tracked_issue({"tracked_by": "  #1504  "}) == 1504
    assert module.tracked_issue({"tracked_by": "#1295\n"}) == 1295
    # ... while a spelling that is not a lone `#<n>` must never read as a number.
    for unparseable in ("1295", "#", "#abc", "#1295 and more", "#1 295", "", None, 1295, True):
        assert module.tracked_issue({"tracked_by": unparseable}) is None, unparseable


def test_every_committed_entry_is_leased_to_an_open_tracker() -> None:
    module = _load_budget_module()
    entries, _ = module.load_budget(BUDGET)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    for entry in entries:
        assert entry["tracked_by"] == "#%d" % entry["issue"], entry["check"]
        assert entry["tracking"]["state"] == "open", entry["check"]
        # The lease must HOLD today: a shipped record whose term had already lapsed would
        # be refused on every run, which is the same false green pointing the other way.
        assert module.lease_refusal(entry, at=now) == "", entry["check"]


def test_the_committed_record_declares_its_per_entry_decision() -> None:
    """Issue #1499 ask 1: the re-point decision is recorded per entry, BY NAME.

    The gate asserts the same three properties; this is the independent check that the
    shipped document cannot have its rows drift away from the entries they describe.
    """
    doc = json.loads(BUDGET.read_text(encoding="utf-8"))
    entries = {e["check"]: e for e in doc["entries"]}
    rows = doc["tracking-repoint-2026-09-20"]["rows"]
    assert len(rows) == len(entries) == 13
    for row in rows:
        entry = entries[row["check"]]
        assert row["decision"] == "re-point", row
        assert row["now"] == entry["tracked_by"], row
        assert row["now"] != row["was"], row
    assert {row["check"] for row in rows} == set(entries)


def test_the_committed_record_names_only_trackers_that_are_open_today() -> None:
    """The measurement, re-taken: every tracker named is OPEN on the board right now.

    Skipped when `gh` is unavailable (the Cloud Build venue installs none), because a
    network-dependent arm must never read as a pass it did not earn.

    Also skipped outside AO_GATE_VENUE=attestation (#1725): this reads the LIVE
    GitHub issue-tracker state, the same "judges the shared box, not this tree"
    class scripts/check-worktree-cap.sh already gates on that variable (#1620) —
    a lane worktree cannot make a currently-open tracker close and should not
    red every lane the moment one does. Still blocking, by design, under
    AO_GATE_VENUE=attestation, the serial post-merge run where live state is
    authoritative.
    """
    import os

    if os.environ.get("AO_GATE_VENUE", "lane") != "attestation":
        import pytest

        pytest.skip(
            "live tracker state judges the shared board, not this tree; "
            "assessed under AO_GATE_VENUE=attestation (#1725)"
        )
    import shutil

    gh = shutil.which("gh")
    if gh is None:
        import pytest

        pytest.skip("gh is not on PATH here; the lease's recorded state is what the gate reads")
    import re

    text = BUDGET.read_text(encoding="utf-8")
    trackers = sorted({int(n) for n in re.findall(r'"tracked_by": "#(\d+)"', text)})
    assert trackers, "the committed record must name at least one tracker"
    for number in trackers:
        proc = subprocess.run(
            [gh, "api", "repos/kushin77/agent-orchestrator/issues/%d" % number, "--jq", ".state"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            import pytest

            pytest.skip("gh could not read #%d here (%s)" % (number, proc.stderr.strip()[:120]))
        assert proc.stdout.strip() == "open", "#%d is %s" % (number, proc.stdout.strip())
