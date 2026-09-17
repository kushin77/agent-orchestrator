"""e2e/chat_surface — the conversational chain end-to-end (issue #1013).

The capstone suite proves the **platform** journey (`e2e/golden_path.py`) and
the **delivery** journey (`e2e/go_live_delivery.py`).  Nothing in it proved the
**conversational** journey EPIC #500 / M30 shipped — measured at filing:

    $ grep -rliE 'telemetry\\.chat|gateway\\.chat' e2e/ | wc -l
    0

Zero files, while the repo ships four chat suites, six chat gates and a whole
serving surface (`gateway/chat`, issue #503).  That gap is exactly how a rotted
green survived: issue #506's own quota-refusal tests have been red since
2026-09-15 while `make verify` stayed green, because the gate's `pytest-chat`
entry runs `gateway/chat/tests` and `telemetry/chat` only runs under
`make gate` / `make tests`.

This module wires the conversational chain over the REAL merged modules,
fully offline, and never patches one (GR-3 — `e2e/**` consumes every pillar
through its public API):

    tenant signup (identity/onboarding)
      -> one chat turn built from telemetry/chat/model.ChatTurn
      -> tier from the gateway's own routing stamp (telemetry/chat/tiering)
      -> pre-dispatch budget guard over the REAL telemetry/budgets rails
         (kill switch -> quota -> budget), refused *before* any provider call
      -> the served call dispatched through the REAL conversational gateway
         (gateway/chat composition over gateway/proxy's offline transport rig,
         routing policy, FinOps chooser and registry/chat prompt modules)
      -> exactly one attribution record + one metering record per turn
      -> the tenant's hash-chained ledger (telemetry/ledger) verifies OK
      -> the UX read model (telemetry/chat/readmodel) rolls the figures up

## The #506 date bomb — the whole point of the lane

``TurnBudgetGuard.check``/``evaluate`` accept ``day``/``month``, but
``GuardedTurnRunner.run`` has **no** such parameters
(``telemetry/chat/budget_guard.py``): it calls ``guard.check(turn, ...)``
without them, so a runner-evaluated turn resolves its bucket from the **live
clock**.  A test that seeds a pinned day and evaluates through the runner
therefore passes on the seed's day and fails every day after — the rot that
survived in #506 undetected.

This module closes that by pinning the *evaluation* bucket to the **turn's own
date**, at the seam the module does expose: the guard the runner is injected
with (:class:`TurnDatedBudgetGuard`).  The deterministic domain is a **fixed
past day** (``TURN_DAY``), so the refusal it asserts can never depend on when
the suite runs: a turn dated 2026-01-05 is judged against 2026-01-05's spend on
every future day, and an unpinned evaluation would silently stop refusing.
:func:`day_scoping` proves the day scope both ways (spend on another day does
not refuse it).

## Negative controls are mutation-proved

:func:`refusal_controls` drives every refusal path through the real rails and
records the exact refusal it produced.  Each control names the branch a mutant
must disable for the control to go red (``"mutant"`` in the evidence); the
external proof copies the module under test into a throwaway tree under ``/tmp``
and replays the control against it.  A control that cannot fail is a formality.

Offline by construction: the gateway's provider registry is the proxy's
scriptable transport rig, provider traffic is a canned response, and no socket,
key, secret or network call is involved.  Run it:

    python3 -m e2e.chat_surface --out /tmp/e2e-chat-surface
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from e2e.wiring import (  # noqa: E402
    PROVIDER_DEEPSEEK,
    TENANT,
    ControlPlane,
    TruthyListCallRecordSink,
    build_control_plane,
    write_evidence,
)
from telemetry.budgets.budget import BudgetEnforcer, load_budget_policies  # noqa: E402
from telemetry.budgets.killswitch import (  # noqa: E402
    KillSwitchController,
    KillSwitchState,
)
from telemetry.budgets.ledger import StaticLedger  # noqa: E402
from telemetry.budgets.model import RESOURCE_REQUESTS, WINDOW_DAY  # noqa: E402
from telemetry.budgets.quota import (  # noqa: E402
    QuotaEnforcer,
    QuotaLimit,
    QuotaPolicy,
    StaticProbe,
)
from telemetry.chat.attribution import (  # noqa: E402
    CODE_DUPLICATE_TURN,
    TurnAttributor,
)
from telemetry.chat.budget_guard import (  # noqa: E402
    GuardedTurnResult,
    GuardedTurnRunner,
    TurnBudgetGuard,
)
from telemetry.chat.cache_accounting import account_prefix  # noqa: E402
from telemetry.chat.model import (  # noqa: E402
    LEDGER_ACTION_REFUSED,
    LEDGER_ACTION_TURN,
    ChatTurn,
    TurnAttribution,
    TurnError,
)
from telemetry.chat.readmodel import ChatFinOpsReadModel  # noqa: E402
from telemetry.chat.tiering import (  # noqa: E402
    CODE_UNKNOWN_TIER_CLAIM,
    TierClaimError,
    resolve_turn_tier,
)
from telemetry.metering.model import NON_BILLABLE_OUTCOMES  # noqa: E402
from telemetry.metering.ratecards import RateCardStore  # noqa: E402
from telemetry.metering.store import MemoryUsageStore  # noqa: E402

# --------------------------------------------------------------------------- #
# The deterministic conversational domain
# --------------------------------------------------------------------------- #
#: The published chat task type a grounded turn runs under (registry/chat).
TASK_TYPE_ANSWER = "chat-answer"

#: The agent the turn is served as.  ``orchestrator`` is a published
#: registry/profiles persona that holds the ``research`` capability, which is
#: the capability the chat surface's own route declaration names.
AGENT = "orchestrator"
CONVERSATION = "conv-1013"
TICKET = "kushin77/agent-orchestrator#1013"

#: A FIXED PAST day.  Deliberately not "today": the whole point of the lane is
#: that the evaluation bucket is pinned to the turn's own date, so the
#: assertions below hold on every day the suite is ever run.
TURN_TS = "2026-01-05T09:00:00Z"
TURN_DAY = "2026-01-05"
TURN_MONTH = "2026-01"
#: Another past day: the day-scope control proves spend seeded here is *not*
#: what the turn above is judged against.
OTHER_DAY = "2026-02-11"

#: A cacheable static prefix: stable prose, no run id / timestamp / session id
#: (so the consumed engine/memory prompt-cache discipline reports it cacheable).
STATIC_PREFIX = (
    "You are the tenant's support agent for the platform.\n"
    "Answer strictly from the tenant's retrieved knowledge base.\n"
    "Name the fragment and its source id for every factual claim.\n"
    "Never invent a policy that is not in the retrieved memory.\n"
    "Prefer the tenant's own terminology over generic platform wording.\n"
    "When the memory does not answer the question, say so plainly."
)
USER_DELTA = "How do I rotate an API key for my organisation?"

#: Token figures are DERIVED from the turn's own prompt (never hand-picked): the
#: cacheable ceiling is the static prefix, so a "cached" figure above it would be
#: exactly the impossible report the accounting refuses.
_PREFIX = account_prefix(STATIC_PREFIX, USER_DELTA)
STATIC_TOKENS = _PREFIX.static_tokens
DELTA_TOKENS = _PREFIX.delta_tokens
PROMPT_TOKENS = _PREFIX.prompt_tokens
CACHED_TOKENS = max(1, STATIC_TOKENS - 1)
OUTPUT_TOKENS = 60

#: The retrieved fragment the canned answer is grounded in.  Its ``source_id``
#: matches registry/chat's own citations pattern (``^(bridge|tool_call|ticket):``)
#: — a fragment it does not match would be refused by the module's output schema,
#: which is the gateway's typed-output contract, not this lane's opinion.
FRAGMENT_ID = "frag-1013-1"
SOURCE_ID = "bridge:kb/tenant-keys.v3"
FRAGMENT = f"{FRAGMENT_ID} :: {SOURCE_ID} :: keys are rotated from tenant settings"

#: A grounded answer satisfying registry/chat's ``grounded-answer`` schema.  It is
#: the response the provider rig replays, so the turn runs the real typed-output
#: validation path and is served, not merely dispatched.
GROUNDED_ANSWER = json.dumps(
    {
        "answer": (
            "Rotate the key from the tenant settings page; the previous key is "
            "revoked when the new one is saved."
        ),
        "citations": [
            {
                "fragment_id": FRAGMENT_ID,
                "source_id": SOURCE_ID,
                "revision": "v3",
                "claim": "keys are rotated from tenant settings",
            }
        ],
    }
)

#: The client tier claims the refusals are proved against.  ``rung_above`` asks
#: for a higher rung than the chooser stamped (it must be ignored); ``unknown`` is
#: not a rung of this ladder at all (it must be refused, never honoured).
CLAIM_RUNG_ABOVE = "L2"
CLAIM_UNKNOWN = "L9"


class ChatSurfaceError(RuntimeError):
    """The conversational chain could not produce the evidence it asserts."""


# --------------------------------------------------------------------------- #
# The turn's own date: the fix for the #506 date bomb, at the exposed seam
# --------------------------------------------------------------------------- #
def turn_day(turn: ChatTurn) -> str:
    """The turn's own day (``YYYY-MM-DD``), from its normalized timestamp.

    A turn with no timestamp normalizes to *now* (``telemetry/chat/model``
    ``normalized_ts``), so a live turn is still judged against the day it
    happened.
    """
    return turn.normalized_ts[:10]


def turn_month(turn: ChatTurn) -> str:
    """The turn's own month (``YYYY-MM``)."""
    return turn.normalized_ts[:7]


class TurnDatedBudgetGuard(TurnBudgetGuard):
    """The guard the runner is injected with: the turn's own date decides.

    ``GuardedTurnRunner.run`` has no ``day``/``month`` parameters, so a check it
    drives resolves the bucket from the LIVE clock — a turn dated in the past is
    then evaluated against today's spend, which is the latent defect of #506
    (a pinned seed silently missing).  The runner's guard is injectable, so this
    subclass supplies the missing argument: the bucket is the turn's own day and
    month.  An explicit caller value still wins (``setdefault``), so a future
    runner that passes the day itself is deferred to rather than overridden.
    """

    def check(self, turn: ChatTurn, **kwargs: Any) -> Any:
        kwargs.setdefault("day", turn_day(turn))
        kwargs.setdefault("month", turn_month(turn))
        return super().check(turn, **kwargs)


class NeuteredTurnDatedBudgetGuard(TurnDatedBudgetGuard):
    """The regression every refusal control must catch: the refusal is gone.

    Its verdict still says "refused" (the rails are untouched) but it reports
    ``allowed=True``, so the turn reaches the provider — exactly what a broken
    guard would do.  The controls assert against the *run*, so this mutant must
    be noticed.
    """

    def check(self, turn: ChatTurn, **kwargs: Any) -> Any:
        from dataclasses import replace

        return replace(super().check(turn, **kwargs), allowed=True, outcome=None)


class MeterlessAttributor(TurnAttributor):
    """The regression the one-record control must catch: metering is skipped.

    ``_meter`` is the single place a turn's metering row is persisted, so a
    regression there silently drops the row while the turn still looks
    attributed.
    """

    def _meter(self, source: Dict[str, Any]) -> Any:
        return SimpleNamespace(
            record_id="unmetered",
            billable=True,
            metered=True,
            cost_usd=None,
            cost_source=None,
            unmetered_reason=None,
        )


# --------------------------------------------------------------------------- #
# The real conversational gateway (gateway/chat over gateway/proxy's rig)
# --------------------------------------------------------------------------- #
def build_chat_gateway() -> Tuple[Any, Any]:
    """The REAL conversational gateway over the proxy's offline transport rig.

    ``gateway/chat/wiring.build_gateway`` builds the proxy's own dispatch core
    (``proxy.wiring.build_real_gateway`` — personas, prompt registry, FinOps
    chooser, provider registry and the scriptable rig) and replaces the two
    chat-specific seams: the task resolver (``registry/chat``'s published
    modules) and the router (the proxy policy composed with the surface's own
    declared routes).  Nothing is re-implemented here.
    """
    from gateway.chat.resolver import ChatTaskResolver
    from gateway.chat.wiring import build_gateway

    audit = TruthyListCallRecordSink()
    metering = TruthyListCallRecordSink()
    return build_gateway(
        audit_sink=audit,
        metering_sink=metering,
        task_resolver=ChatTaskResolver(),
    )


class ChatProvider:
    """The turn's provider seam: one real conversational dispatch per turn.

    Passed to ``GuardedTurnRunner`` as the ``provider``.  It counts its
    invocations (a refusal control asserts zero, which is the property "refused
    before any provider call"), keeps the gateway's own dispatch result so the
    caller can read the routing decision and the call record, and returns the
    REAL ``GatewayCallRecord`` the attribution consumes.
    """

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway
        self.calls: List[ChatTurn] = []
        self.result: Any = None

    def __call__(self, turn: ChatTurn) -> Any:
        from proxy.model import TaskRequest

        self.calls.append(turn)
        self.result = self.gateway.dispatch(
            turn.agent_id,
            TaskRequest(
                tenant_id=turn.tenant_id,
                task_type=TASK_TYPE_ANSWER,
                input={
                    "tenant": turn.tenant_id,
                    "fragments": FRAGMENT,
                    "question": turn.user_delta,
                },
            ),
        )
        return self.result.record

    @property
    def call_count(self) -> int:
        return len(self.calls)

    # -- evidence off the gateway's own dispatch ------------------------- #
    @property
    def record(self) -> Dict[str, Any]:
        if self.result is None or self.result.record is None:
            raise ChatSurfaceError("no dispatch has produced a call record yet")
        return dict(self.result.record.to_dict())

    @property
    def decision(self) -> Dict[str, Any]:
        """The router's own routing decision (the tier's authority)."""
        for event in getattr(self.result, "events", ()) or ():
            if event.stage == "route_selected":
                return dict(event.data)
        raise ChatSurfaceError("the dispatch carried no route_selected decision")


# --------------------------------------------------------------------------- #
# The chain: control plane + gateway + attributor + read model
# --------------------------------------------------------------------------- #
class ConversationalChain:
    """One provisioned tenant, one real conversational gateway, one attributor.

    The ledger and the usage store are the same real sinks the rest of the e2e
    capstone uses: ``telemetry/ledger``'s tamper-evident store and
    ``telemetry/metering``'s store, so a chat turn is audited and billed by the
    platform's own modules rather than by a chat-specific double.
    """

    def __init__(
        self,
        control: Optional[ControlPlane] = None,
        *,
        work_dir: Optional[str] = None,
        tenant_id: str = TENANT,
    ) -> None:
        self.control = control or build_control_plane(tenant_id, work_dir=work_dir)
        self.gateway, self.wired = build_chat_gateway()
        self.rig = self.wired.rig
        self.rate_store = RateCardStore.load_dir()
        self.usage_store = MemoryUsageStore()
        self.attributor = TurnAttributor(
            self.control.ledger_store,
            rate_store=self.rate_store,
            usage_store=self.usage_store,
        )
        self.readmodel = ChatFinOpsReadModel()
        self.provider = ChatProvider(self.gateway)
        self.script_answer()

    # -- wiring helpers --------------------------------------------------- #
    def script_answer(self, *, input_tokens: int = PROMPT_TOKENS, output_tokens: int = OUTPUT_TOKENS) -> None:
        """Script the rig's canned typed answer for the primary provider."""
        self.rig.script_success(
            PROVIDER_DEEPSEEK,
            GROUNDED_ANSWER,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @property
    def tenant_id(self) -> str:
        return self.control.tenant_id

    @property
    def ladder_keys(self) -> Tuple[str, ...]:
        """The FinOps ladder rungs, read from the gateway's OWN routing policy.

        A client tier claim is validated against these; the ladder is never
        re-declared in this lane (a second ladder would be a second authority).
        """
        config = getattr(getattr(self.gateway, "router", None), "config", None)
        tier_map = getattr(config, "tier_map", None)
        if not isinstance(tier_map, Mapping) or not tier_map:
            raise ChatSurfaceError(
                "the gateway's routing policy declares no tier map; the FinOps "
                "ladder cannot be read, so a tier claim cannot be validated"
            )
        return tuple(sorted(str(key) for key in tier_map))

    def projected_cost(self, turn: ChatTurn) -> float:
        """The turn's projected cost — the rate card's figure, never invented."""
        estimate = self.rate_store.estimate(
            PROVIDER_DEEPSEEK, DEFAULT_MODEL, turn.prefix.prompt_tokens, OUTPUT_TOKENS
        )
        if estimate is None:
            raise ChatSurfaceError(
                f"no rate card for {PROVIDER_DEEPSEEK}/{DEFAULT_MODEL}; the guard "
                "cannot be given a projected cost it did not invent"
            )
        return float(estimate.cost_usd)

    # -- the run ---------------------------------------------------------- #
    def run(
        self,
        turn: ChatTurn,
        *,
        guard: Optional[TurnBudgetGuard] = None,
        provider: Optional[Any] = None,
        attributor: Optional[Any] = None,
        estimated_cost_usd: Optional[float] = None,
    ) -> GuardedTurnResult:
        """Guard, then (only if allowed) dispatch, then attribute — in that order."""
        outcome = GuardedTurnRunner(
            guard if guard is not None else TurnDatedBudgetGuard(),
            attributor if attributor is not None else self.attributor,
        ).run(
            turn,
            provider if provider is not None else self.provider,
            estimated_cost_usd=(
                self.projected_cost(turn)
                if estimated_cost_usd is None
                else estimated_cost_usd
            ),
        )
        self.readmodel.add(outcome.attribution, outcome.outcome)
        return outcome

    # -- observations ----------------------------------------------------- #
    def row_for(self, attribution: TurnAttribution) -> Optional[Any]:
        """The metering row the attribution names (``usage_record_id``).

        The metering store's own ``source_key`` is a content hash (the intake's
        idempotency key), so the row a turn owns is the one whose ``record_id``
        the attribution recorded — never a row guessed from a turn id.
        """
        for row in self.usage_store.read():
            if row.record_id == attribution.usage_record_id:
                return row
        return None

    def ledger_records(self) -> List[Dict[str, Any]]:
        return list(self.control.ledger_store.records(self.tenant_id))

    def ledger_status(self) -> str:
        return self.control.ledger_store.verify(self.tenant_id).status

    def ledger_payload(self, seq: int) -> Dict[str, Any]:
        """The decrypted payload of the tenant's ledger event at ``seq``.

        Read back through ``telemetry/ledger``'s own tri-state reader, so a
        payload that cannot be decrypted is reported rather than assumed.
        """
        from telemetry.ledger.verify import read_payload

        status, payload, detail = read_payload(
            self.control.ledger_store, self.tenant_id, seq
        )
        if status != "OK":
            raise ChatSurfaceError(
                f"ledger payload at seq {seq} is {status}: {detail}"
            )
        return dict(payload or {})

    # -- the fixtures ----------------------------------------------------- #
    def turn(
        self,
        turn_id: str = "turn-1",
        *,
        cached_tokens: int = 0,
        client_tier: Optional[str] = None,
        ts: str = TURN_TS,
        critical: bool = False,
    ) -> ChatTurn:
        """A deterministic turn of this chain's tenant/agent/conversation."""
        return ChatTurn(
            turn_id=turn_id,
            conversation_id=CONVERSATION,
            tenant_id=self.tenant_id,
            agent_id=AGENT,
            ts=ts,
            ticket_id=TICKET,
            static_prefix=STATIC_PREFIX,
            user_delta=USER_DELTA,
            cached_tokens=cached_tokens,
            critical=critical,
            client_tier=client_tier,
        )


#: The model the rig's primary provider serves (the gateway's own stamp is what
#: the attribution carries; this is only used to ask the rate card for a figure).
DEFAULT_MODEL = "deepseek-chat"


# --------------------------------------------------------------------------- #
# Assertions the suite is built on (each can fail, by construction)
# --------------------------------------------------------------------------- #
def assert_single_record(
    chain: ConversationalChain,
    turn: ChatTurn,
    attribution: Optional[TurnAttribution] = None,
    *,
    expected_rows: Optional[int] = None,
) -> Dict[str, Any]:
    """Assert exactly one metering record and one ledger event back this turn.

    Reads the metering store and the tenant's hash-chained ledger — the two
    sinks the attribution wrote — and refuses when the row the attribution
    names is absent, when the store holds more rows than the turns that ran, or
    when the ledger event it names is not on the chain.  A turn with no record
    fails here.
    """
    attribution = attribution or chain.readmodel.turn(turn.turn_id)
    if attribution is None:
        raise ChatSurfaceError(
            f"CHAT-TURN-NO-VIEW: turn {turn.turn_id!r} produced no read-model view"
        )
    row = chain.row_for(attribution)
    if row is None:
        raise ChatSurfaceError(
            f"CHAT-TURN-ROW-COUNT: turn {turn.turn_id!r} must have exactly one "
            f"metering record naming {attribution.usage_record_id!r}, found 0"
        )
    store_rows = chain.usage_store.count()
    if expected_rows is not None and store_rows != expected_rows:
        raise ChatSurfaceError(
            f"CHAT-TURN-ROW-COUNT: turn {turn.turn_id!r} ran as the only turn, so "
            f"the store must hold exactly {expected_rows} row(s), found {store_rows}"
        )
    events = [
        record
        for record in chain.ledger_records()
        if record.get("seq") == attribution.ledger_seq
    ]
    if len(events) != 1:
        raise ChatSurfaceError(
            f"CHAT-TURN-LEDGER-COUNT: turn {turn.turn_id!r} must have exactly one "
            f"ledger event at seq {attribution.ledger_seq!r}, found {len(events)}"
        )
    return {
        "turnId": turn.turn_id,
        "meteringRows": 1,
        "storeRows": store_rows,
        "meteringRecordId": row.record_id,
        "metered": bool(row.metered),
        "billable": bool(row.billable),
        "outcome": row.outcome,
        "costUsd": row.cost_usd,
        "costSource": row.cost_source,
        "ledgerSeq": attribution.ledger_seq,
        "ledgerAction": events[0].get("action"),
    }


# --------------------------------------------------------------------------- #
# Negative controls (one per refusal path; each names its mutant)
# --------------------------------------------------------------------------- #
#: Every refusal path, the rail that refuses it, and the branch a mutant must
#: disable for the control to go red.  ``mutant`` is the mutation-proof contract:
#: an external driver patches that branch in a throwaway copy under ``/tmp`` and
#: requires this control (``test_each_refusal_path_is_a_mutation_proved_control``)
#: to fail naming the path.
REFUSAL_PATHS: Tuple[Dict[str, str], ...] = (
    {
        "path": "kill_switch",
        "rail": "telemetry/budgets kill switch (global pause)",
        "mutant": "telemetry.chat.budget_guard.TurnBudgetGuard.check must report "
        "allowed=True for a paused tenant instead of the kill switch's refusal",
    },
    {
        "path": "budget_over_cap",
        "rail": "telemetry/budgets per-tenant monthly cap (acme: enforce, 120 USD)",
        "mutant": "telemetry.chat.budget_guard.TurnBudgetGuard.check must report "
        "allowed=True for an over-cap tenant instead of the budget rail's block",
    },
    {
        "path": "quota_exhausted_day",
        "rail": "telemetry/budgets per-tenant daily request quota (20 of 20 used)",
        "mutant": "telemetry.chat.budget_guard.TurnBudgetGuard.check must report "
        "allowed=True for a quota-exhausted tenant instead of the quota rail's refusal",
    },
)

#: The non-run refusals: paths that refuse by raising, not by a verdict.
TIER_REFUSALS: Tuple[Dict[str, str], ...] = (
    {
        "path": "tier_claim_unknown",
        "rail": "telemetry/chat/tiering claim validation against the FinOps ladder",
        "mutant": "telemetry.chat.tiering.resolve_turn_tier must accept a claim "
        "outside the ladder instead of raising TierClaimError",
    },
    {
        "path": "duplicate_turn",
        "rail": "telemetry/chat/attribution one-metering-record-per-turn rule",
        "mutant": "telemetry.chat.attribution.TurnAttributor._meter must re-ingest "
        "a turn that already has a row instead of raising CHAT-DUPLICATE-TURN",
    },
)


def refusal_guard(chain: ConversationalChain, path: str) -> TurnBudgetGuard:
    """The real rails, seeded so that ``path`` refuses this turn."""
    if path == "kill_switch":
        return TurnDatedBudgetGuard(
            killswitch=KillSwitchController(
                initial=KillSwitchState(
                    global_pause=True,
                    reason="incident-1013",
                    paused_by="ops",
                    timestamp=TURN_TS,
                )
            )
        )
    if path == "budget_over_cap":
        # acme's SHIPPED policy (telemetry/budgets/config/budgets.yaml) is
        # enforce with a 120 USD monthly cap: seeded AT the cap, the next turn
        # is refused by the budget rail.  The month is the turn's own month, so
        # the seed cannot drift away from the evaluation bucket (#506).
        ledger = StaticLedger(costs={(chain.tenant_id, TURN_MONTH): 120.0})
        return TurnDatedBudgetGuard(
            budget=BudgetEnforcer(ledger, load_budget_policies())
        )
    if path == "quota_exhausted_day":
        policy = QuotaPolicy(
            tenant_id=chain.tenant_id,
            plan="free",
            limits={
                RESOURCE_REQUESTS: QuotaLimit(
                    resource=RESOURCE_REQUESTS,
                    window=WINDOW_DAY,
                    soft_limit=10,
                    hard_limit=20,
                )
            },
        )
        # Seed the requests at the HARD limit for the turn's own day.
        ledger = StaticLedger(calls={(chain.tenant_id, TURN_DAY): 20})
        return TurnDatedBudgetGuard(
            quota=QuotaEnforcer(ledger, {chain.tenant_id: policy}, probe=StaticProbe())
        )
    raise ChatSurfaceError(f"unknown refusal path {path!r}")


def run_refusal_control(chain: ConversationalChain, path: str) -> Dict[str, Any]:
    """Provoke one refusal and record what the real rails did.

    The control passes only when the turn was refused, the provider was never
    called, the turn is still metered as a non-billable event, and the tenant's
    ledger carries exactly one refusal event — a guard that stopped refusing, or
    one that refused silently, fails at least one of those.
    """
    provider = ChatProvider(chain.gateway)
    result = chain.run(chain.turn(f"turn-{path}"), guard=refusal_guard(chain, path), provider=provider)

    row = chain.row_for(result.attribution)
    turn_events = [
        record for record in chain.ledger_records()
        if record.get("seq") == result.attribution.ledger_seq
    ]
    evidence = {
        "path": path,
        "rail": next(p["rail"] for p in REFUSAL_PATHS if p["path"] == path),
        "mutant": next(p["mutant"] for p in REFUSAL_PATHS if p["path"] == path),
        "refused": bool(not result.allowed),
        "decision": result.outcome.decision,
        "code": result.outcome.code,
        "outcome": result.outcome.outcome,
        "hardStop": bool(result.outcome.hard_stop),
        "providerCalls": provider.call_count,
        "providerCalled": bool(result.provider_called),
        "metered": bool(result.attribution.metered),
        "billable": bool(result.attribution.billable),
        "costUsd": result.attribution.cost_usd,
        "meteringRows": 0 if row is None else 1,
        "meteringRecordId": None if row is None else row.record_id,
        "turnLedgerEvents": len(turn_events),
        "turnLedgerAction": turn_events[0].get("action") if turn_events else None,
        "refusalLedgerEvents": sum(
            1 for record in chain.ledger_records()
            if record.get("action") == LEDGER_ACTION_REFUSED
        ),
        "blocked": True,
    }
    evidence["blocked"] = bool(
        evidence["refused"]
        and not evidence["providerCalled"]
        and evidence["providerCalls"] == 0
        and evidence["hardStop"]
        and evidence["outcome"] in NON_BILLABLE_OUTCOMES
        and evidence["metered"]
        and not evidence["billable"]
        and evidence["costUsd"] is None
        and evidence["meteringRows"] == 1
        and evidence["turnLedgerEvents"] == 1
        and evidence["turnLedgerAction"] == LEDGER_ACTION_REFUSED
    )
    return evidence


def refusal_controls(chain: ConversationalChain) -> List[Dict[str, Any]]:
    """Every refusal path's control, in order, with its own evidence."""
    controls = [run_refusal_control(chain, path["path"]) for path in REFUSAL_PATHS]

    # -- a claim outside the ladder is refused, never honoured ------------ #
    ladder = chain.ladder_keys
    tier_claim = {
        "path": "tier_claim_unknown",
        "rail": TIER_REFUSALS[0]["rail"],
        "mutant": TIER_REFUSALS[0]["mutant"],
        "ladder": list(ladder),
        "claim": CLAIM_UNKNOWN,
    }
    try:
        resolve_turn_tier("LOW", client_tier=CLAIM_UNKNOWN, ladder_keys=ladder)
        tier_claim.update({"refused": False, "code": None, "blocked": False})
    except TierClaimError as exc:
        tier_claim.update(
            {
                "refused": True,
                "code": getattr(exc, "code", None),
                "detail": str(exc),
            }
        )
        tier_claim["blocked"] = bool(tier_claim["code"] == CODE_UNKNOWN_TIER_CLAIM)
    controls.append(tier_claim)

    # -- one turn is metered exactly once: a second attribution refuses --- #
    duplicate = {
        "path": "duplicate_turn",
        "rail": TIER_REFUSALS[1]["rail"],
        "mutant": TIER_REFUSALS[1]["mutant"],
    }
    turn = chain.turn("turn-duplicate")
    before = chain.usage_store.count()
    served = chain.run(turn)
    after_served = chain.usage_store.count()
    try:
        # Re-attribute the EXACT served turn: its metering row already exists.
        chain.attributor.attribute(turn, chain.provider.record)
        duplicate.update({"refused": False, "code": None, "blocked": False})
    except TurnError as exc:
        duplicate.update(
            {
                "refused": True,
                "code": CODE_DUPLICATE_TURN,
                "detail": str(exc),
            }
        )
        duplicate["blocked"] = bool(
            CODE_DUPLICATE_TURN in str(exc)
            and after_served == before + 1
            and chain.usage_store.count() == after_served
        )
    duplicate["storeRowsBefore"] = before
    duplicate["storeRowsAfterServe"] = after_served
    duplicate["storeRowsAfterReplay"] = chain.usage_store.count()
    duplicate["servedTurnId"] = served.attribution.turn_id
    controls.append(duplicate)
    return controls


# --------------------------------------------------------------------------- #
# The day scope: the assertion that would have caught #506
# --------------------------------------------------------------------------- #
def day_scoping(chain: ConversationalChain) -> Dict[str, Any]:
    """Prove the refusal rail's bucket is the TURN's own day, not the run's.

    Two past days, one seeded with spend.  A turn dated the seeded day must be
    refused; the same turn dated the other day must not be — so the bucket is a
    specific day, and the pin (not the wall clock) is what selects it.  Run on
    any future day, the first half is impossible unless the evaluation bucket is
    the turn's own date: under the #506 rot it silently stops refusing.
    """
    policy = QuotaPolicy(
        tenant_id=chain.tenant_id,
        plan="free",
        limits={
            RESOURCE_REQUESTS: QuotaLimit(
                resource=RESOURCE_REQUESTS,
                window=WINDOW_DAY,
                soft_limit=10,
                hard_limit=20,
            )
        },
    )
    ledger = StaticLedger(calls={(chain.tenant_id, TURN_DAY): 20})

    def guard() -> TurnBudgetGuard:
        """A fresh guard per turn: the same seed, evaluated per turn."""
        return TurnDatedBudgetGuard(
            quota=QuotaEnforcer(ledger, {chain.tenant_id: policy}, probe=StaticProbe())
        )

    seeded_provider = ChatProvider(chain.gateway)
    seeded = chain.run(
        chain.turn("turn-seeded-day"), guard=guard(), provider=seeded_provider
    )
    other_provider = ChatProvider(chain.gateway)
    other = chain.run(
        chain.turn("turn-other-day", ts=f"{OTHER_DAY}T09:00:00Z"),
        guard=guard(),
        provider=other_provider,
    )
    return {
        "seededDay": TURN_DAY,
        "turnDay": TURN_DAY,
        "otherDay": OTHER_DAY,
        "seededDayRefused": bool(not seeded.allowed),
        "seededDayDecision": seeded.outcome.decision,
        "seededDayCode": seeded.outcome.code,
        "seededDayProviderCalls": seeded_provider.call_count,
        "otherDayAllowed": bool(other.allowed),
        "otherDayProviderCalls": other_provider.call_count,
        "scoped": bool(
            not seeded.allowed
            and seeded_provider.call_count == 0
            and other.allowed
            and other_provider.call_count == 1
        ),
    }


# --------------------------------------------------------------------------- #
# The scenario (one honest evidence document, `--out` like its siblings)
# --------------------------------------------------------------------------- #
def _signup_stage(chain: ConversationalChain) -> Dict[str, Any]:
    """The tenant the turn is served for was provisioned by identity/onboarding."""
    control = chain.control
    tenant = control.provision_result.tenant
    roles = sorted(role.key for role in control.rbac_store.roles_in_org(chain.tenant_id))
    agents = sorted(
        binding.subject for binding in control.rbac_store.bindings_in_org(chain.tenant_id)
    )
    evidence = {
        "tenantId": tenant.id,
        "status": tenant.status,
        "ownerSubject": control.owner_subject,
        "roles": roles,
        "ownerBindings": agents,
        "provenance": "identity/onboarding",
    }
    control.attest(
        "chat.signup",
        0 if tenant.status == "active" else 1,
        f"tenant {tenant.id} provisioned status={tenant.status} roles={roles}",
        provenance="identity/onboarding",
    )
    return evidence


def _served_turn_stage(
    chain: ConversationalChain, turn_id: str = "turn-1"
) -> Tuple[GuardedTurnResult, Dict[str, Any]]:
    """One real turn end to end: guard -> conversational dispatch -> attribution."""
    turn = chain.turn(turn_id)
    result = chain.run(turn)
    provider = chain.provider
    evidence = {
        "turnId": turn.turn_id,
        "conversationId": turn.conversation_id,
        "tenantId": turn.tenant_id,
        "agentId": turn.agent_id,
        "ticketId": turn.ticket_id,
        "taskType": TASK_TYPE_ANSWER,
        "providerCalled": bool(result.provider_called),
        "providerCalls": provider.call_count,
        "allowed": bool(result.allowed),
        "budgetDecision": result.outcome.decision,
        "servedProvider": provider.record.get("provider"),
        "servedModel": provider.record.get("model"),
        "servedTier": provider.record.get("tier"),
        "outcome": result.attribution.outcome,
        "costUsd": result.attribution.cost_usd,
        "costSource": result.attribution.cost_source,
        "provenance": "telemetry/chat + gateway/chat",
    }
    passed = bool(
        result.allowed
        and result.provider_called
        and provider.call_count == 1
        and result.attribution.outcome == "success"
    )
    chain.control.attest(
        "chat.served-turn",
        0 if passed else 1,
        f"turn {turn.turn_id} served by {evidence['servedProvider']}/"
        f"{evidence['servedModel']} tier={evidence['servedTier']} "
        f"cost={evidence['costUsd']} ({evidence['costSource']})",
        provenance="telemetry/chat + gateway/chat",
    )
    return result, evidence


def _tier_stage(chain: ConversationalChain, result: GuardedTurnResult) -> Dict[str, Any]:
    """The tier is the router's stamp; a client claim is never authoritative."""
    record = chain.provider.record
    decision = chain.provider.decision
    claims = {}
    for label, claim, ladder in (
        ("rungAbove", CLAIM_RUNG_ABOVE, chain.ladder_keys),
        # A claim that happens to MATCH the stamp: agreement is observable, and
        # still not authority.
        ("agrees", str(record.get("tier")), None),
    ):
        resolution = resolve_turn_tier(
            record.get("tier"), client_tier=claim, ladder_keys=ladder
        )
        claims[label] = resolution.to_dict()
    evidence = {
        "recordedTier": record.get("tier"),
        "routerTier": decision.get("tier"),
        "routerLadderTier": decision.get("ladderTier"),
        "taskClass": decision.get("taskClass"),
        "capability": decision.get("capability"),
        "ladderKeys": list(chain.ladder_keys),
        "claims": claims,
        "attributionTier": result.attribution.tier,
        "clientTier": result.attribution.client_tier,
        "tierClaimHonoured": bool(result.attribution.tier_claim_honoured),
    }
    evidence["tierFromStamp"] = bool(
        evidence["recordedTier"] == evidence["routerTier"]
        and evidence["recordedTier"] == evidence["attributionTier"]
    )
    evidence["claimNeverAuthoritative"] = bool(
        all(not item["claimHonoured"] for item in claims.values())
    )
    chain.control.attest(
        "chat.tier",
        0 if (evidence["tierFromStamp"] and evidence["claimNeverAuthoritative"]) else 1,
        f"tier {evidence['recordedTier']} is the router's stamp "
        f"(ladder {evidence['routerLadderTier']}, taskClass {evidence['taskClass']}); "
        f"claims {sorted(claims)} are never honoured",
        provenance="telemetry/chat/tiering",
    )
    return evidence


def _records_stage(chain: ConversationalChain, turn_id: str) -> Dict[str, Any]:
    """Exactly one attribution record and one metering record per turn."""
    turn = chain.turn(turn_id)
    rows = assert_single_record(chain, turn)
    verdict = chain.ledger_status()
    evidence = {
        "turn": rows,
        "ledgerStatus": verdict,
        "tenantLedgerEvents": len(chain.ledger_records()),
    }
    served_action = bool(rows["ledgerAction"] == LEDGER_ACTION_TURN)
    evidence["servedAction"] = served_action
    chain.control.attest(
        "chat.records",
        0 if (verdict == "OK" and served_action) else 1,
        f"turn {turn_id} has exactly one metering record ({rows['meteringRecordId']}) "
        f"and one ledger event (seq {rows['ledgerSeq']}, {rows['ledgerAction']}); "
        f"tenant ledger verifies {verdict}",
        provenance="telemetry/chat/attribution + telemetry/ledger",
    )
    return evidence


def _cache_stage(chain: ConversationalChain) -> Dict[str, Any]:
    """A cache-hit turn reports a cache share and costs strictly less than cold."""
    cold = chain.run(chain.turn("turn-cold", cached_tokens=0))
    warm = chain.run(chain.turn("turn-warm", cached_tokens=CACHED_TOKENS))
    warm_view = warm.attribution
    cold_view = cold.attribution
    rate_card_cold = chain.rate_store.estimate(
        str(cold_view.provider), str(cold_view.model), PROMPT_TOKENS, OUTPUT_TOKENS
    )
    evidence = {
        "cold": _cache_figures(cold_view),
        "warm": _cache_figures(warm_view),
        "derivedCachedTokens": CACHED_TOKENS,
        "derivedStaticTokens": STATIC_TOKENS,
        "derivedDeltaTokens": DELTA_TOKENS,
        "derivedPromptTokens": PROMPT_TOKENS,
        "coldRateCardUsd": None if rate_card_cold is None else rate_card_cold.cost_usd,
        "cheaper": bool(
            warm_view.cost_usd is not None
            and cold_view.cost_usd is not None
            and warm_view.cost_usd < cold_view.cost_usd
        ),
    }
    evidence["cacheShareReported"] = bool(warm_view.cache_hit_share > 0)
    evidence["coldEquivalentMatchesCold"] = bool(
        warm_view.cold_equivalent_cost_usd is not None
        and cold_view.cost_usd is not None
        and abs(warm_view.cold_equivalent_cost_usd - cold_view.cost_usd) < 1e-12
    )
    chain.control.attest(
        "chat.cache",
        0 if (evidence["cheaper"] and evidence["cacheShareReported"]) else 1,
        f"warm turn cost {warm_view.cost_usd} < cold {cold_view.cost_usd} "
        f"(share {warm_view.cache_hit_share}, cold-equivalent "
        f"{warm_view.cold_equivalent_cost_usd})",
        provenance="telemetry/chat/cache_accounting + telemetry/metering rate card",
    )
    return evidence


def _cache_figures(view: TurnAttribution) -> Dict[str, Any]:
    return {
        "turnId": view.turn_id,
        "cachedTokens": view.cache.cached_tokens,
        "promptTokens": view.cache.prompt_tokens,
        "billableInputTokens": view.input_tokens,
        "cacheHitShare": view.cache_hit_share,
        "costUsd": view.cost_usd,
        "costSource": view.cost_source,
        "coldEquivalentCostUsd": view.cold_equivalent_cost_usd,
    }


def _controls_stage(chain: ConversationalChain) -> Dict[str, Any]:
    controls = refusal_controls(chain)
    scoping = day_scoping(chain)
    every_control_blocked = all(item.get("blocked") for item in controls)
    evidence = {
        "controls": controls,
        "dayScoping": scoping,
        "everyControlBlocked": bool(every_control_blocked),
        "controlsBlocked": [item["path"] for item in controls if item.get("blocked")],
        "controlsNotBlocked": [item["path"] for item in controls if not item.get("blocked")],
    }
    chain.control.attest(
        "chat.refusals",
        0 if (every_control_blocked and scoping["scoped"]) else 1,
        f"{len(evidence['controlsBlocked'])}/{len(controls)} refusal paths blocked "
        f"(not blocked: {evidence['controlsNotBlocked'] or 'none'}); the rail's "
        f"bucket is the turn's own day ({scoping['seededDay']} refused, "
        f"{scoping['otherDay']} allowed)",
        provenance="telemetry/budgets + telemetry/chat",
    )
    return evidence


def run_chat_surface(
    control: Optional[ControlPlane] = None, *, work_dir: Optional[str] = None
) -> Dict[str, Any]:
    """Run the whole conversational chain and return JSON-serializable evidence."""
    chain = ConversationalChain(control, work_dir=work_dir)
    signup = _signup_stage(chain)
    served_result, served = _served_turn_stage(chain)
    tier = _tier_stage(chain, served_result)
    records = _records_stage(chain, served["turnId"])
    cache = _cache_stage(chain)
    controls = _controls_stage(chain)
    payload = {
        "gate": "e2e.chat_surface",
        "tenantId": chain.tenant_id,
        "stages": {
            "signup": signup,
            "served-turn": served,
            "tier": tier,
            "records": records,
            "cache": cache,
            "controls": controls,
        },
        "readModel": chain.readmodel.tenant(chain.tenant_id).to_dict(),
        "attestations": [item.to_dict() for item in chain.control.attestations],
        "attested": all(item.attested for item in chain.control.attestations),
    }
    if chain.control.work_dir:
        write_evidence(chain.control.work_dir, "chat-surface.json", payload)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m e2e.chat_surface")
    parser.add_argument("--out", default=None, help="evidence directory")
    args = parser.parse_args(list(argv) if argv is not None else None)
    work_dir = args.out or os.path.join(os.getcwd(), ".verify", "e2e")
    payload = run_chat_surface(work_dir=work_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print(f"CHAT SURFACE: {'PASS' if payload['attested'] else 'FAIL'}")
    return 0 if payload["attested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
