"""Typed ticket edges over the lessons register (issue #402).

Each test is a control on the edge layer: the typed edge is the single read
surface, the vocabulary is closed, a reference that cannot be typed is refused
rather than passed through as free text, and the derivation is deterministic.
"""

from __future__ import annotations

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_lessons_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
action = _conftest.action
incident = _conftest.incident
lesson = _conftest.lesson
rca = _conftest.rca
suggestion = _conftest.suggestion

from edges import (
    EDGE_CAUSED_BY,
    EDGE_MITIGATES,
    EDGE_ORIGIN,
    EDGE_REMEDIATION_OF,
    TICKET_EDGE_TYPES,
    edges,
    findings,
    normalize_origin,
    pmo_rows,
    remediation_issue,
    ticket_kind,
)


def types_of(records):
    return sorted({edge.type for edge in edges(records)})


def clean_ledger():
    return [incident(1), rca(1), action(1), lesson(1)]


# --- the typed vocabulary ---------------------------------------------------


def test_every_edge_type_is_drawn_from_the_closed_vocabulary():
    records = clean_ledger() + [suggestion(1)]
    assert types_of(records)
    for edge in edges(records):
        assert edge.type in TICKET_EDGE_TYPES


def test_an_rca_is_caused_by_its_incident():
    assert (EDGE_CAUSED_BY, "RCA-0001", "INC-0001") in {
        (e.type, e.from_id, e.to_id) for e in edges(clean_ledger())
    }


def test_a_corrective_action_mitigates_its_rca():
    found = {
        (e.type, e.from_id, e.to_id) for e in edges(clean_ledger())
    }
    assert (EDGE_MITIGATES, "CA-0001", "RCA-0001") in found


def test_a_lesson_mitigates_the_incident_of_its_rca():
    found = {(e.type, e.from_id, e.to_id) for e in edges(clean_ledger())}
    assert (EDGE_MITIGATES, "LESSON-0001", "INC-0001") in found


def test_an_origin_becomes_a_typed_edge():
    found = {(e.type, e.from_id, e.to_id) for e in edges(clean_ledger())}
    assert (EDGE_ORIGIN, "RCA-0001", "issue-100") in found
    assert (EDGE_ORIGIN, "INC-0001", "issue-100") in found


def test_an_event_origin_is_a_typed_edge_not_a_finding():
    records = [incident(1, origin={"kind": "event", "ref": "audit 2026-09-13"})]
    found = {(e.type, e.to_id) for e in edges(records)}
    assert (EDGE_ORIGIN, "event-audit 2026-09-13") in found
    assert findings(records) == []


# --- remediation-of replaces the free string --------------------------------


def test_an_open_action_names_its_remediation_as_a_typed_edge():
    records = [
        incident(1),
        rca(1),
        action(1, status="open", evidence=[], remediation_issue="#170"),
    ]
    assert remediation_issue(records[2]) == "issue-170"
    found = {(e.type, e.from_id, e.to_id) for e in edges(records)}
    assert (EDGE_REMEDIATION_OF, "CA-0001", "issue-170") in found


def test_a_malformed_remediation_issue_is_refused_not_passed_through():
    record = action(1, status="open", evidence=[], remediation_issue="issue one")
    assert remediation_issue(record) == ""
    assert findings([record])


def test_a_malformed_origin_is_refused_not_passed_through():
    record = incident(1, origin={"kind": "issue", "ref": "not-a-number"})
    assert normalize_origin(record.get("origin")) == ""
    assert findings([record])


def test_a_delete_from_the_ledger_removes_its_edges():
    """An edge only exists for a record that resolves: no dangling endpoint."""
    records = [rca(1, incident_id="INC-0099")]
    found = {(e.type, e.from_id, e.to_id) for e in edges(records)}
    assert (EDGE_CAUSED_BY, "RCA-0001", "INC-0099") not in found


# --- the ticket kind --------------------------------------------------------


def test_a_suggestion_is_a_ticket_of_kind_suggestion():
    assert ticket_kind(suggestion(1)) == "suggestion"


def test_a_lesson_is_a_ticket_of_kind_lesson():
    assert ticket_kind(lesson(1)) == "lesson"


def test_the_ledger_kinds_map_to_their_ticket_kinds():
    assert ticket_kind(incident(1)) == "incident"
    assert ticket_kind(rca(1)) == "rca"
    assert ticket_kind(action(1)) == "corrective-action"


# --- determinism ------------------------------------------------------------


def test_the_derivation_is_order_independent():
    records = clean_ledger() + [suggestion(1)]
    assert edges(records) == edges(list(reversed(records)))


def test_the_derivation_is_repeatable():
    records = clean_ledger()
    assert [e.as_dict() for e in edges(records)] == [
        e.as_dict() for e in edges(records)
    ]


# --- the PMO view -----------------------------------------------------------


def test_a_learning_carries_owner_status_class_and_goal():
    records = clean_ledger() + [suggestion(1)]
    snapshot = {
        100: {
            "number": 100,
            "state": "CLOSED",
            "parent": 42,
            "milestone": "M1 - A Goal",
            "labels": [],
        }
    }
    rows = {row["id"]: row for row in pmo_rows(records, snapshot)}
    row = rows["SUGGEST-0001"]
    assert row["kind"] == "suggestion"
    assert row["owner"] == "governance lane"
    assert row["status"] == "open"
    assert row["class"] == "elite"
    assert row["goal"] == "#42"
    assert row["origin"] == "#100"


def test_an_unresolvable_goal_is_reported_empty_not_guessed():
    records = clean_ledger()
    rows = {row["id"]: row for row in pmo_rows(records, snapshot=None)}
    assert rows["LESSON-0001"]["goal"] == ""


def test_the_pmo_view_holds_only_learnings():
    rows = pmo_rows(clean_ledger())
    assert {row["kind"] for row in rows} == {"lesson"}


# --- the repository's own register ------------------------------------------


def test_the_repositorys_own_register_types_cleanly():
    REPO_ROOT = _conftest.REPO_ROOT
    from edges import load_records

    records = load_records(REPO_ROOT)
    assert records
    assert findings(records) == []
    found = {(e.type, e.from_id, e.to_id) for e in edges(records)}
    assert (EDGE_REMEDIATION_OF, "CA-0007", "issue-170") in found
    assert (EDGE_CAUSED_BY, "RCA-0005", "INC-0005") in found
