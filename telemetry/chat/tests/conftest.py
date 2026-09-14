"""Pytest bootstrap + fixtures for telemetry/chat (issue #506).

``telemetry/`` carries no ``__init__.py`` (per-issue package directories), so
this inserts the repo root at the front of ``sys.path`` and every test imports
``telemetry.chat.*`` (and the consumed ``telemetry.metering`` /
``telemetry.budgets`` / ``telemetry.ledger`` / ``engine.memory`` packages) no
matter where pytest is invoked.

Everything a test needs arrives as a **fixture** (``world``, ``record_factory``,
``spy_provider``, ...): this directory carries no ``__init__.py``, so importing
a sibling helper module by name would share one global module name across every
suite in the run.  The fixtures are deterministic — fixed timestamps, a fixed
tenant/agent/conversation, the shipped rate cards and budget policies — and the
ledger keys are derived at runtime from a seed (never a literal key in source),
so the repo-wide mechanical secret scan cannot false-positive on a test key.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from telemetry.chat.cache_accounting import account_prefix  # noqa: E402
from telemetry.chat.model import ChatTurn  # noqa: E402

# --------------------------------------------------------------------------- #
# The deterministic fixture world
# --------------------------------------------------------------------------- #
TURN_TS = "2026-09-14T09:00:00Z"
DAY = "2026-09-14"
MONTH = "2026-09"

#: A cacheable static prefix: stable prose, no run id / timestamp / session id.
STATIC_PREFIX = (
    "You are the tenant's support agent for the platform.\n"
    "Answer strictly from the tenant's knowledge base.\n"
    "Cite the memory entry key for every factual claim you make.\n"
    "Never invent a policy that is not in the retrieved memory.\n"
    "Prefer the tenant's own terminology over generic platform wording.\n"
    "When the memory does not answer the question, say so plainly."
)
#: The same shape with the run identity injected — cache-killing by design.
DYNAMIC_PREFIX = (
    "You are the tenant's support agent.\n"
    "Session id: 6f1c0f4a2b8e4d7f9a3c5e1b0d2f4a68\n"
    "Started at 2026-09-14T09:00:00Z"
)
USER_DELTA = "How do I rotate an API key for my organisation?"

#: Token fixtures are DERIVED from the turn's own prompt, never assumed: the
#: cacheable ceiling is the static prefix, so a hand-picked "cached" figure
#: above it would be exactly the impossible report the accounting refuses.
_PREFIX = account_prefix(STATIC_PREFIX, USER_DELTA)
STATIC_TOKENS = _PREFIX.static_tokens
DELTA_TOKENS = _PREFIX.delta_tokens
PROMPT_TOKENS = _PREFIX.prompt_tokens
CACHED_TOKENS = max(1, STATIC_TOKENS - 1)
BILLABLE_INPUT_TOKENS = PROMPT_TOKENS - CACHED_TOKENS
OUTPUT_TOKENS = 200


@dataclass(frozen=True)
class World:
    """The deterministic vocabulary every test shares."""

    tenant: str = "acme"
    other_tenant: str = "other"
    agent: str = "chat-agent-1"
    conversation: str = "conv-1"
    ticket: str = "kushin77/agent-orchestrator#506"
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    tier: str = "L0"
    ts: str = TURN_TS
    day: str = DAY
    month: str = MONTH
    static_prefix: str = STATIC_PREFIX
    dynamic_prefix: str = DYNAMIC_PREFIX
    user_delta: str = USER_DELTA
    static_tokens: int = STATIC_TOKENS
    delta_tokens: int = DELTA_TOKENS
    prompt_tokens: int = PROMPT_TOKENS
    cached_tokens: int = CACHED_TOKENS
    billable_input_tokens: int = BILLABLE_INPUT_TOKENS
    output_tokens: int = OUTPUT_TOKENS


def make_key(seed: str) -> bytes:
    """Deterministic 32-byte test key derived from ``seed`` (never literal)."""
    return hashlib.sha256(("telemetry-chat-test:" + seed).encode("utf-8")).digest()


def build_record(
    world: World,
    turn_id: str = "turn-1",
    tenant: Optional[str] = None,
    agent: Optional[str] = None,
    tier: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    outcome: str = "success",
    latency_ms: float = 420.0,
    estimated_cost_usd: Optional[float] = None,
    task_class: str = "chat-support",
) -> Dict[str, Any]:
    """A ``GatewayCallRecord.to_dict()``-shaped record (gateway/proxy, issue #16).

    Deliberately dict-shaped: this lane consumes the *stored* camelCase shape
    (as ``telemetry/metering`` and ``telemetry/observability`` do), so the chat
    surface never imports the gateway package.  The wrapped gate feeds a real
    ``GatewayCallRecord`` through this same parser to keep the two in step.
    """
    billable_input = (
        world.billable_input_tokens if input_tokens is None else input_tokens
    )
    out_tokens = world.output_tokens if output_tokens is None else output_tokens
    return {
        "requestId": turn_id,
        "ts": world.ts,
        "tenantId": world.tenant if tenant is None else tenant,
        "agentId": world.agent if agent is None else agent,
        "taskType": "chat.turn",
        "capability": None,
        "taskClass": task_class,
        "tier": world.tier if tier is None else tier,
        "provider": world.provider if provider is None else provider,
        "model": world.model if model is None else model,
        "outcome": outcome,
        "inputTokens": billable_input,
        "outputTokens": out_tokens,
        "tokens": billable_input + out_tokens,
        "latencyMs": latency_ms,
        "estimatedCostUsd": estimated_cost_usd,
        "budgetAction": "allow",
        "attempts": 1,
        "error": None,
    }


class SpyProvider:
    """A provider that counts its invocations and replays a fixed record.

    The refusal controls assert ``call_count == 0``: a guard that lets a
    refused turn through is caught by the count, not by an argument.
    """

    def __init__(self, world: World, record: Optional[Dict[str, Any]] = None) -> None:
        self.world = world
        self.record = record
        self.calls: List[Any] = []

    def __call__(self, turn: Any) -> Dict[str, Any]:
        self.calls.append(turn)
        if self.record is not None:
            return dict(self.record)
        return build_record(self.world, turn_id=turn.turn_id)

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture(scope="session")
def world() -> World:
    return World()


@pytest.fixture
def record_factory(world):
    """``record_factory(**overrides)`` over the deterministic world."""

    def make(**overrides: Any) -> Dict[str, Any]:
        return build_record(world, **overrides)

    return make


@pytest.fixture
def spy_provider(world):
    """The :class:`SpyProvider` factory (a test may need more than one spy)."""

    def make(record: Optional[Dict[str, Any]] = None) -> SpyProvider:
        return SpyProvider(world, record=record)

    return make


@pytest.fixture
def turn(world) -> ChatTurn:
    """A deterministic cache-warm chat turn (all but one prefix token reused)."""
    return ChatTurn(
        turn_id="turn-1",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        ticket_id=world.ticket,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
        cached_tokens=world.cached_tokens,
    )


@pytest.fixture
def cold_turn(world) -> ChatTurn:
    """The same prompt with no observed prefix reuse (the cold control)."""
    return ChatTurn(
        turn_id="turn-cold",
        conversation_id=world.conversation,
        tenant_id=world.tenant,
        agent_id=world.agent,
        ts=world.ts,
        ticket_id=world.ticket,
        static_prefix=world.static_prefix,
        user_delta=world.user_delta,
        cached_tokens=0,
    )


@pytest.fixture
def keystore(world):
    """A ledger keystore holding keys for the fixture tenants."""
    from telemetry.ledger import DictKeystore, KeyMaterial

    return DictKeystore(
        {
            tenant: KeyMaterial(key=make_key(tenant), key_id=f"test:{tenant}:v1")
            for tenant in (world.tenant, world.other_tenant)
        }
    )


@pytest.fixture
def ledger(keystore):
    """An in-memory tamper-evident audit ledger (the real issue-#31 store)."""
    from telemetry.ledger import open_ledger

    return open_ledger(None, keystore=keystore)


@pytest.fixture
def usage_store():
    """The metering destination: one record per turn, idempotent by source key."""
    from telemetry.metering.store import MemoryUsageStore

    return MemoryUsageStore()


@pytest.fixture
def attributor(ledger, usage_store):
    """A real attributor over the shipped rate cards + the two real sinks."""
    from telemetry.chat.attribution import TurnAttributor

    return TurnAttributor(ledger, usage_store=usage_store)
