"""**Fixture-only** authorities for the enterprise family (issue #504).

*Nothing in this module is a production surface.* It exists so that the
offline tests and the gate of record can exercise the read-only family against
a complete, hermetic authority tree without reaching into the working checkout -
and so that every stand-in is labelled as one.

That labelling is the point (ADR-0018 §2/§3): the declared in-memory index in
:mod:`mcp.kb` is a fake, this module's bridge stand-in is a fake, and a fake
answer standing in for a measured one is the false-green failure this fleet
forbids. So:

* :data:`FIXTURE_ONLY` is ``True`` and :class:`FixtureBridge` carries
  ``fixture_only = True``, which :class:`mcp.sources.SourceCatalog` refuses on a
  production path;
* :func:`fixture_catalog` is the *only* sanctioned way to build a catalogue that
  reaches these authorities, and it builds it in
  :data:`mcp.sources.MODE_FIXTURE`.

The shapes are the real ones - a board snapshot, the budget policy config, a
ledger chain built by the ledger's own store, a knowledge catalogue - so a test
exercises the same readers a deployment does.

---knowledge---
module_id: gateway.mcp.fixtures
system: gateway
app: mcp
solution_class: enterprise
patterns: [labelled-fixture, hermetic-authority-tree, refuse-fixture-outside-tests]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: [FixtureBridge, write_fixture_authorities, fixture_catalog, FIXTURE_ONLY]
invariants: "every stand-in is labelled a fake and a fixture-only source is refused outside tests, so a fake answer can never stand in for a measured one"
gotchas: "nothing here is a production surface, and the labelling is what keeps the false-green failure out"
related: ["#504"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Tuple

from .sources import MODE_FIXTURE, KbFixtureSource, SourceCatalog

#: This module is a fixture surface: never import it on a live path.
FIXTURE_ONLY = True

#: The note every fixture artifact's reader can quote back.
FIXTURE_NOTE = (
    "fixture-only: this authority is a declared stand-in for an offline test or "
    "gate run; it is refused on a production grounding path (issue #504, "
    "ADR-0018 §2/§3)"
)

#: Two fixture tickets plus one unrelated closed ticket.
FIXTURE_ISSUES: Tuple[dict, ...] = (
    {
        "number": 501,
        "title": "ADR lane for the chat epic",
        "state": "OPEN",
        "labels": ["epic"],
        "milestone": "chat",
        "parent": 500,
        "blocked_by": [],
        "closed_at": None,
    },
    {
        "number": 504,
        "title": "Chat grounding: the read-only enterprise tool family",
        "state": "OPEN",
        "labels": ["gateway"],
        "milestone": "chat",
        "parent": 500,
        "blocked_by": [501],
        "closed_at": None,
    },
    {
        "number": 7,
        "title": "An unrelated closed ticket",
        "state": "CLOSED",
        "labels": [],
        "milestone": "",
        "parent": None,
        "blocked_by": [],
        "closed_at": "2026-01-28T21:54:20Z",
    },
)

#: The bridge's own family-revision digest for the fixture registry family.
FIXTURE_REGISTRY_REVISION = "fixture00000000ab"


class FixtureBridge:
    """A **fixture** implementation of the ``ao.bridge/v1`` read surface.

    Labelled ``fixture_only`` so the catalogue refuses it on a production path:
    the real surface is ``portal.server.bridge.LiveBridge``, which is what
    :meth:`mcp.sources.SourceCatalog.from_repo_root` builds when no bridge is
    injected.
    """

    fixture_only = True
    CONTRACT = "ao.bridge/v1"

    def registry(self) -> dict:
        return {
            "family": "registry",
            "contract": self.CONTRACT,
            "available": True,
            "source": "portal.server.livestore.RegistrySnapshot",
            "revision": FIXTURE_REGISTRY_REVISION,
            "profiles": [
                {
                    "id": "claude",
                    "version": "1.0.0",
                    "owner": "platform/purebliss",
                    "modelTier": "MED",
                    "model": "flash",
                    "capabilities": ["orchestrate", "code-author"],
                },
                {
                    "id": "coder",
                    "version": "1.0.0",
                    "owner": "platform/execution",
                    "modelTier": "LOW",
                    "model": "flash",
                    "capabilities": ["code-author"],
                },
            ],
        }

    def manifest(self) -> dict:
        rows = [
            {
                "id": "registry",
                "source": "fixture.livestore",
                "path": "registry/profiles",
                "kind": "read",
            },
            {
                "id": "telemetry",
                "source": "fixture.live_feed",
                "path": ".telemetry",
                "kind": "read+push",
            },
        ]
        return {
            "contract": self.CONTRACT,
            "families": [dict(row, contract=self.CONTRACT) for row in rows],
        }

    def revisions(self) -> dict:
        return {"registry": FIXTURE_REGISTRY_REVISION, "telemetry": "fixture00000000cd"}


def write_fixture_authorities(root: Any) -> None:
    """Materialise the fixture authority tree the enterprise family reads."""
    root = Path(root)
    (root / ".board").mkdir(parents=True, exist_ok=True)
    (root / ".board" / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-14T00:00:00Z",
                "source": "fixture",
                "issues": [dict(issue) for issue in FIXTURE_ISSUES],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    budgets = root / "telemetry" / "budgets" / "config"
    budgets.mkdir(parents=True, exist_ok=True)
    (budgets / "policies.yaml").write_text(
        "schemaVersion: 1\n"
        "defaultMode: observe\n"
        "policies:\n"
        "  - tenantId: acme\n"
        "    mode: enforce\n"
        "    cost:\n"
        "      window: month\n"
        "      limitUsd: 120.0\n"
        "      warnAtPct: 0.8\n"
        "    tokens:\n"
        "      limit: 2000000\n"
        "      warnAtPct: 0.8\n",
        encoding="utf-8",
    )

    knowledge = root / "governance" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "catalog.json").write_text(
        json.dumps(
            {
                "schema": "cmr.knowledge/index-v1",
                "generated_at": "2026-09-14T00:00:00Z",
                "repo": "fixture",
                "item_count": 1,
                "items": [
                    {
                        "id": "docs/decision-records/ADR-0018-index-authority.md",
                        "kind": "adr",
                        "title": "The two-index authority split",
                        "path": "docs/decision-records/ADR-0018-index-authority.md",
                        "tags": ["architecture"],
                        "keywords": ["index", "authority"],
                        "provenance": {
                            "origin_repo": "kushin77/agent-orchestrator",
                            "origin_path": "docs/decision-records/ADR-0018-index-authority.md",
                            "owner": "architecture",
                            "version": "abc1234",
                            "sha256": "0" * 64,
                            "timestamp": "2026-09-14T00:00:00Z",
                        },
                    }
                ],
                "coverage": [
                    {
                        "kind": "adr",
                        "status": "present",
                        "count": 1,
                        "required": True,
                        "reason": "",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # A real chain, appended by the ledger's own store (never hand-written).
    ledger_dir = root / "telemetry" / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from telemetry.ledger.store import LedgerStore  # noqa: E402 - root-dependent

    store = LedgerStore(directory=str(ledger_dir))
    store.append("acme", actor="agent:worker-1", action="model.call", resource="gateway/proxy")
    store.append("acme", actor="agent:worker-1", action="policy.decision", resource="guardrails")
    store.append("globex", actor="agent:worker-2", action="model.call", resource="gateway/proxy")


def fixture_catalog(root: Any, *, mode: str = MODE_FIXTURE) -> SourceCatalog:
    """The family's catalogue over a fixture tree, including the fake KB.

    ``mode`` defaults to :data:`mcp.sources.MODE_FIXTURE`; passing
    ``MODE_PRODUCTION`` is legitimate *only* to prove the refusals (that is how
    the tests and the gate exercise them).
    """
    catalog = SourceCatalog.from_repo_root(str(root), mode=mode, bridge=FixtureBridge())
    catalog.add(KbFixtureSource(str(root)))
    return catalog
