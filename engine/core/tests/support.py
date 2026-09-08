"""Shared fakes + helpers for the engine-core suite.

Kept free of sibling constants (the plain module name ``conftest``/``support``
is shared across test directories when suites run together).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from core.errors import StepFailure
from core.gateway_port import GatewayRequest, GatewayResult
from core.handlers import Handler


class FakeClock:
    """Deterministic clock: returns the fixed instant until advanced."""

    def __init__(self, start: Optional[str] = None) -> None:
        self._t = (
            datetime.fromisoformat(start)
            if start
            else datetime(2026, 1, 1, tzinfo=timezone.utc)
        )

    def now_iso(self) -> str:
        return self._t.isoformat()

    def advance(self, seconds: float) -> None:
        self._t = self._t + timedelta(seconds=seconds)


class FakeGateway:
    """Duck-typed ModelGateway recording every dispatch."""

    def __init__(
        self,
        calls: Optional[List[tuple]] = None,
        cost: float = 0.25,
        outcome: str = "success",
    ) -> None:
        self.calls: List[tuple] = calls if calls is not None else []
        self.cost = cost
        self.outcome = outcome

    def dispatch(self, request: GatewayRequest) -> GatewayResult:
        self.calls.append(
            (request.tenant_id, request.agent_id, request.task_type, dict(request.input_))
        )
        return GatewayResult(
            outcome=self.outcome,
            content={"echo": request.task_type},
            provider="fake",
            model="fake-1",
            cost=self.cost,
            usage={"input_tokens": 1, "output_tokens": 1},
        )


def record_run(name: str, calls: List[str]) -> Callable:
    """A step run that appends ``name`` to ``calls`` and succeeds."""

    def _run(step: Any, ctx: Any) -> Dict[str, Any]:
        calls.append(name)
        return {"action": name}

    return _run


def record_compensate(name: str, calls: List[str]) -> Callable:
    """A compensation that appends ``name`` to ``calls`` and succeeds."""

    def _compensate(comp: Any, ctx: Any) -> None:
        calls.append(name)

    return _compensate


def failing_run(name: str, calls: List[str], message: str = "boom") -> Callable:
    """A step run that appends ``name`` then raises StepFailure."""

    def _run(step: Any, ctx: Any) -> Dict[str, Any]:
        calls.append(name)
        raise StepFailure(message)

    return _run


def slow_run(seconds: float) -> Callable:
    """A step run that advances the engine clock (to breach an SLA)."""

    def _run(step: Any, ctx: Any) -> Dict[str, Any]:
        ctx.clock.advance(seconds)
        return {"slept": seconds}

    return _run


def handler(run: Callable, compensate: Optional[Callable] = None) -> Handler:
    return Handler(run=run, compensate=compensate)
