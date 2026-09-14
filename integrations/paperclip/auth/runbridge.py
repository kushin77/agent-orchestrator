"""Run correlation bridge: ``X-Paperclip-Run-Id`` <-> ``correlation_id`` (issue #412).

A mutating request made during an agent run carries the upstream
``X-Paperclip-Run-Id`` header. The fleet already has a run-correlation id of its
own: the ``correlation_id`` in the steering envelope (``fleet/channel.py``,
``fleet/schema/message.schema.json``), which binds a result/ack to the directive
that caused it. This bridge joins the two, so **one run is traceable on both
sides**: the upstream run id resolves to the fleet's correlation id and back
again.

The bridge keeps only the binding. The fleet's ``correlation_id`` — written by
``fleet/channel.py`` and validated here against the same shape rule — remains the
authoritative fleet-side value; this is a join, not a second correlation store.

A run id is consumed **once**: a second bind of the same run id is a replay and
is refused ``409 replayed_run_id`` (an agent run id names exactly one run).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional, Protocol, runtime_checkable

from .model import RunBinding, replayed_run_id, validation_error

#: A run id is an opaque, shell-safe token; the shape is closed so a path or a
#: newline can never be smuggled into the ledger.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise validation_error("the run id is not a valid token")
    return run_id


def validate_correlation_id(correlation_id: object) -> str:
    """A fleet ``correlation_id`` is a non-empty string (message.schema.json)."""
    if not isinstance(correlation_id, str) or not correlation_id.strip():
        raise validation_error("the correlation id must be a non-empty string")
    return correlation_id


@runtime_checkable
class RunLedger(Protocol):
    """The join store: which run ids have been consumed, and their correlation."""

    def seen(self, run_id: str) -> bool:  # pragma: no cover - protocol declaration
        ...

    def correlation_for(self, run_id: str) -> Optional[str]:  # pragma: no cover
        ...

    def run_for(self, correlation_id: str) -> Optional[str]:  # pragma: no cover
        ...

    def record(self, run_id: str, correlation_id: str) -> None:  # pragma: no cover
        ...


class InMemoryRunLedger:
    """Default ledger: process-local, deterministic, dependency-free."""

    def __init__(self) -> None:
        self._by_run: Dict[str, str] = {}
        self._by_correlation: Dict[str, str] = {}

    def seen(self, run_id: str) -> bool:
        return run_id in self._by_run

    def correlation_for(self, run_id: str) -> Optional[str]:
        return self._by_run.get(run_id)

    def run_for(self, correlation_id: str) -> Optional[str]:
        return self._by_correlation.get(correlation_id)

    def record(self, run_id: str, correlation_id: str) -> None:
        self._by_run[run_id] = correlation_id
        self._by_correlation[correlation_id] = run_id


class JsonFileRunLedger(InMemoryRunLedger):
    """A ledger persisted to an injected JSON path (never a committed file).

    The path is supplied by the caller — the repository carries no run ledger —
    so a durable bridge is opt-in and the default stays process-local.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.is_file():
            for entry in json.loads(self.path.read_text(encoding="utf-8")).get("bindings", []):
                self.record(str(entry["run_id"]), str(entry["correlation_id"]))

    def record(self, run_id: str, correlation_id: str) -> None:
        super().record(run_id, correlation_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bindings": [
                {"run_id": r, "correlation_id": c} for r, c in sorted(self._by_run.items())
            ]
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class RunBridge:
    """Binds upstream run ids to the fleet's own ``correlation_id``."""

    def __init__(self, ledger: Optional[RunLedger] = None) -> None:
        self.ledger: RunLedger = ledger or InMemoryRunLedger()

    def bind(self, run_id: str, correlation_id: str) -> RunBinding:
        """Join one run id to one correlation id; refuse a replayed run id."""
        run_id = validate_run_id(run_id)
        correlation_id = validate_correlation_id(correlation_id)
        if self.ledger.seen(run_id):
            raise replayed_run_id()
        self.ledger.record(run_id, correlation_id)
        return RunBinding(run_id=run_id, correlation_id=correlation_id)

    def correlation_for(self, run_id: str) -> Optional[str]:
        return self.ledger.correlation_for(run_id) if self.ledger.seen(run_id) else None

    def run_for(self, correlation_id: str) -> Optional[str]:
        return self.ledger.run_for(correlation_id)

    def bind_from_fleet(
        self, run_id: str, *, now: Optional[int] = None, prefix: str = "paperclip-run"
    ) -> RunBinding:
        """Derive the fleet ``correlation_id`` for a run when none is supplied.

        Deterministic over ``(prefix, run_id)`` so re-deriving an already-bound
        run id is caught as a replay by :meth:`bind`, never silently duplicated.
        """
        del now  # determinism is intentional; wall-clock plays no part in the join
        return self.bind(run_id, f"{prefix}:{validate_run_id(run_id)}")


def epoch_now() -> int:
    return int(time.time())
