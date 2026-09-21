"""Shared fixtures for the ticket-projection suite (issue #401).

The suite runs in isolation (``scripts/pytest-suites.txt``), like every other
package here: sibling suites collide on a flat ``conftest`` sys.path bootstrap,
so each package is a suite of its own.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE.parents[1]

if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

# governance/ticket shares the bare basenames "model", "cli", "sources",
# "builder" and "freshness" with sibling governance/* suites. Evict any stale
# sys.modules entry from an earlier-collected suite before this directory's
# test modules do their own bare imports, so they resolve against THIS
# package's files (issues #699, #702, #1042).
#
# ALL FIVE names must be evicted together, not just the three importED
# directly by a test file: cli.py imports builder, and builder imports model
# and sources. If "builder" is left cached from an earlier reimport while
# "model" is evicted and re-imported fresh, cli.py's fresh `model.CannotAssess`
# and the cached builder's (stale) `model.CannotAssess` become two distinct
# class objects — an exception raised via the cached chain then fails
# `except CannotAssess` in the freshly-imported cli.py, because the two
# classes are no longer `is`-identical despite matching names (#1501).
for _name in ("model", "cli", "sources", "builder", "freshness"):
    sys.modules.pop(_name, None)

SCHEMA_RELPATH = Path("docs/contracts/paperclip/ticket.schema.json")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A minimal projection root: the real frozen contract plus empty ledgers."""
    schema = tmp_path / SCHEMA_RELPATH
    schema.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / SCHEMA_RELPATH, schema)
    (tmp_path / ".board").mkdir(parents=True, exist_ok=True)
    return tmp_path


def write_board(root: Path, issues: list[dict]) -> None:
    payload = {
        "generated_at": "2026-09-14T00:00:00Z",
        "source": "kushin77/agent-orchestrator",
        "issues": issues,
    }
    (root / ".board" / "snapshot.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


def write_claims(root: Path, events: list[dict]) -> None:
    directory = root / ".board" / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    for index, event in enumerate(events):
        name = "%020d-%05d-%s.json" % (index, int(event["issue"]), event.get("event", "claim"))
        (directory / name).write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")


def write_lessons(root: Path, records: list[dict]) -> None:
    path = root / "governance" / "lessons" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def write_attestation(root: Path, payload: dict) -> None:
    path = root / ".verify" / "attestation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def issue(number: int, **overrides) -> dict:
    record = {
        "number": number,
        "title": f"issue {number}",
        "state": "OPEN",
        "milestone": "",
        "labels": [],
        "parent": None,
        "blocked_by": [],
        "closed_at": "",
    }
    record.update(overrides)
    return record
