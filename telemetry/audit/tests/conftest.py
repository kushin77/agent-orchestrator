"""Pytest bootstrap for telemetry/audit (issue #347).

``telemetry/`` has no ``__init__.py`` (per-issue package directory, mirroring
``registry/``), so this inserts ``telemetry/`` and ``telemetry/audit`` at the
front of ``sys.path``: every test can then ``import read_model`` and reach the
sibling ``ledger`` package no matter where pytest is invoked from.

The fixtures build a deterministic two-tenant trail and expose tamper helpers
that rewrite a *temp copy* of a chain file — never the committed ledger.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_AUDIT = os.path.dirname(_HERE)          # telemetry/audit
_TELEMETRY = os.path.dirname(_AUDIT)     # telemetry
for _path in (_TELEMETRY, _AUDIT):
    if _path not in sys.path:
        sys.path.append(_path)

from ledger import open_ledger  # noqa: E402  (after sys.path)

import read_model  # noqa: E402  (after sys.path)


#: The deterministic seed trail: two tenants, five records, three severities.
SEED: List[Dict[str, Any]] = [
    {
        "tenant": "acme",
        "actor": "user:alice",
        "action": "model.call",
        "resource": "gateway/proxy",
        "ts": "2026-09-08T10:00:00Z",
    },
    {
        "tenant": "acme",
        "actor": "agent:worker-1",
        "action": "registry.register",
        "resource": "registry/agents/worker-1",
        "evidence": "registry-event:aaa",
        "ts": "2026-09-08T10:01:00Z",
    },
    {
        "tenant": "acme",
        "actor": "agent:worker-1",
        "action": "policy.deny",
        "resource": "gateway/proxy",
        "ts": "2026-09-08T10:02:00Z",
    },
    {
        "tenant": "acme",
        "actor": "system:registry",
        "action": "registry.sync",
        "resource": "registry/agents/worker-2",
        "ts": "2026-09-09T09:00:00Z",
    },
    {
        "tenant": "beta",
        "actor": "user:bob",
        "action": "model.call",
        "resource": "gateway/proxy",
        "ts": "2026-09-08T11:00:00Z",
    },
]


def seed_ledger(directory: str) -> None:
    """Append the deterministic seed trail to the ledger at ``directory``."""
    store = open_ledger(directory)
    for row in SEED:
        store.append(
            row["tenant"],
            actor=row["actor"],
            action=row["action"],
            resource=row.get("resource"),
            evidence=row.get("evidence"),
            ts=row["ts"],
        )


@pytest.fixture()
def ledger_dir(tmp_path: Any) -> str:
    """A freshly seeded ledger directory (one JSON Lines file per tenant)."""
    directory = str(tmp_path / "ledger")
    seed_ledger(directory)
    return directory


@pytest.fixture()
def model(ledger_dir: str) -> Any:
    """A read model over the seeded ledger."""
    return read_model.open_read_model(ledger_dir)


# --------------------------------------------------------------------------- #
# tamper helpers - they rewrite a chain file, so callers use a temp copy
# --------------------------------------------------------------------------- #
def chain_path(directory: str, tenant: str) -> str:
    return os.path.join(directory, tenant + ".jsonl")


def _lines(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def _write(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _data_indices(lines: List[str]) -> List[int]:
    return [i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")]


def tamper_text(path: str, old: str, new: str) -> None:
    """Alter a record's content in place (the record hash no longer matches)."""
    lines = _lines(path)
    found = False
    for i in _data_indices(lines):
        if old in lines[i]:
            lines[i] = lines[i].replace(old, new, 1)
            found = True
            break
    assert found, f"anchor {old!r} not found in {path}"
    _write(path, lines)


def reorder_first_two(path: str) -> None:
    """Swap the first two records so the chain order no longer holds."""
    lines = _lines(path)
    data = _data_indices(lines)
    assert len(data) >= 2, "need at least two records to reorder"
    first, second = data[0], data[1]
    lines[first], lines[second] = lines[second], lines[first]
    _write(path, lines)


def remove_record(path: str, index: int) -> None:
    """Delete the ``index``-th record (0-based among data lines)."""
    lines = _lines(path)
    data = _data_indices(lines)
    del lines[data[index]]
    _write(path, lines)


def truncate_last(path: str) -> None:
    """Drop the last record (an internally consistent, silently shortened chain)."""
    lines = _lines(path)
    data = _data_indices(lines)
    del lines[data[-1]]
    _write(path, lines)


def corrupt_line(path: str) -> None:
    """Append an unparseable line so no honest verdict can be formed."""
    lines = _lines(path)
    lines.append("{this is not json")
    _write(path, lines)
