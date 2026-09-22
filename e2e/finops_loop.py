"""e2e/finops_loop — EPIC #665's two unproven links, proven end to end (#1019).

EPIC #665's thesis is ONE chain: *a conversion event lands in the ERPNext
ledger, the inference that answers it is served cache-optimally, and the cost
is metered against the rate cards.*  The sibling probe
``e2e/erp_finops_golden.py`` (#675) measures the chain's middle — signup ->
billing -> revenue recognition — and nothing measured either end of the epic's
own name ("from CRM hook to ledger").  Measured on ``origin/master``:
``git grep -lIi webhook -- 'e2e/**'``, ``... mcp ...`` and ``... cache_hit
...`` each returned **zero** files.  This probe measures all three:

| Stage      | Real module consumed           | What is measured                                                            |
|------------|--------------------------------|-----------------------------------------------------------------------------|
| conversion | ``integrations/erp/webhooks``  | a CRM conversion event crosses ``ConversionBridge``, its HMAC signature is verified *before* the payload is parsed, and the GL document it derives is read back through ``integrations.erp.tx.ledger`` — the module that owns the posting — never re-derived here |
| inference  | ``gateway/finops`` + ``telemetry/metering`` | the same scenario's calls, composed from the canonical prefix template (#670), normalise to ONE cache key, so the recorded cache-hit share (#673) is positive and the *metered* cost is strictly below the cold equivalent's — priced by the rate cards |
| trim       | ``gateway/mcp``                | a verbose tool output is trimmed by the response filter (#672) before the model-visible envelope is built, so what the model sees is strictly smaller |

**The chain, not two halves.**  The inference stage is handed the voucher id
the bridge minted (``crm-conversion:<eventId>``) and carries it as the
``billing_query_id`` axis of the recorded usage — the join column #673 defines
— so the token flow is attributable to the accounting document that caused it.
The usage rows are written to JSON Lines and read back with
``cache_audit.load_usage`` (the module's own input contract), and the cost is
resolved by ``telemetry/metering``'s intake from its rate cards.

**What is declared and what is measured.**  The repo ships no tokenizer, so the
per-call token split is a *declared* scenario parameter — exactly as
``gateway/finops/cache_baseline.py``'s ``SYNTHETIC_SAMPLE`` declares its own.
What the module decides is *which side of the split is billed*: that follows
from ``prefix_templates.validate_prefix`` (is the canonical prefix intact?) and
``prefix_templates.footprint`` (do the calls normalise to one cache key?), and
it is what makes the warm/cold split fail-able rather than asserted.

**Offline, deterministic, no wall-clock read.**  No network, no sockets, no
docker, no keys — synthetic (non-secret) signing material assembled at runtime
(GR-6).  Every clock is injected: the CRM funnel clock below, the per-call
session stamps, and the metered ``ts``.  In particular the intake is never
handed a record without a ``ts`` (``telemetry.metering.parse_ts(None)`` falls
back to *now*), so every figure is a function of the scenario, never of when
the suite ran.

Run from the repo root:

    python3 -m e2e.finops_loop --out /tmp/pf5   # the evidence, as JSON

---knowledge---
module_id: e2e.finops_loop
system: e2e
app: finops
solution_class: enterprise
patterns: [no-false-green, offline-composition-root]
derives_from: e2e/erp_finops_golden.py
owner_sme: qa-sme
tier: L1
interfaces: [webhook -> mcp -> cache_hit chain probe]
invariants: "measures the two links erp_finops_golden.py leaves unmeasured"
gotchas: ""
related: ["#1019", "#665", "#675"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from e2e._paths import REPO_ROOT, ensure_sys_paths

ensure_sys_paths()

# ``gateway/finops`` holds standalone scripts imported by plain name — that
# lane's own convention (``tests/conftest.py`` prepends this directory).  Adding
# it here does NOT shadow ``telemetry.metering``: a dotted import resolves
# through the ``telemetry`` *package*, while a top-level ``metering`` resolves
# to this directory's module, which is exactly what
# ``gateway/proxy/wiring.py`` expects.  Nothing else in the e2e suite imports a
# name this directory declares (measured), so the addition is inert for
# sibling suites.
_FINOPS_DIR = os.path.join(REPO_ROOT, "gateway", "finops")
if _FINOPS_DIR not in sys.path:
    sys.path.insert(0, _FINOPS_DIR)

# The metering pillar is imported EAGERLY, before anything can put
# ``<repo>/fleet`` on ``sys.path`` — where ``fleet/telemetry.py`` would shadow
# the ``telemetry`` package for a later ``import telemetry.*`` ("'telemetry' is
# not a package").  e2e/go_live_delivery.py documents the same hazard.
from telemetry.metering.intake import MeteringIntake  # noqa: E402
from telemetry.metering.ratecards import RateCardStore  # noqa: E402

import cache_audit  # noqa: E402
import cache_baseline  # noqa: E402
import prefix_templates  # noqa: E402

from e2e.wiring import write_evidence  # noqa: E402

# --------------------------------------------------------------------------- #
# scenario constants — declared, and every clock injected
# --------------------------------------------------------------------------- #

#: The tenant the loop runs under: the CRM lane's, the bridge's, the metering
#: pillar's.  One tenant, carried through every stage.
TENANT = "acme"

#: The actor every step is attributed to — the agent the conversion serves.
ACTOR = "agent:pf5-e2e"

#: The CRM funnel's clock, injected so the ledger's timestamps are a function
#: of the scenario (the same discipline e2e/erp_finops_golden.py applies).
CRM_CLOCK = {
    "opened": "2026-03-01T09:00:00Z",
    "contacted": "2026-03-01T10:00:00Z",
    "qualified": "2026-03-01T11:00:00Z",
    "converted": "2026-03-01T12:00:00Z",
    "proposal": "2026-03-02T09:00:00Z",
    "won": "2026-03-02T10:00:00Z",
}

LEAD_ID = "LEAD-LOOP-0001"
OPPORTUNITY_ID = "OPP-LOOP-0001"
CUSTOMER_ID = "CUST-LOOP-0001"
CUSTOMER_NAME = "Northwind Traders"
EVENT_ID = "evt-crm-0001"

#: The won opportunity's value — the amount the conversion event carries and
#: the ledger must therefore post.  A scenario parameter, never re-derived.
CONVERSION_AMOUNT = 1500.00

#: The posting policy's chart of accounts (scenario data, supplied by us — the
#: spine declares the *roles* and nothing else).
RECEIVABLE_ACCOUNT = "Accounts Receivable"
INCOME_ACCOUNT = "Sales Income"

#: Synthetic, non-secret webhook signing material (GR-6).  Assembled at runtime
#: from fragments so this committed file never carries a secret-shaped literal —
#: the convention ``e2e/wiring.py`` uses for its own synthetic key material.
_SIGNING_FRAGMENTS = (b"ao1019", b"-e2e-", b"webhook-", b"material")
WEBHOOK_SIGNING_KEY = b"".join(_SIGNING_FRAGMENTS).decode("utf-8")

#: A key that is deliberately the wrong one, for the forged-signature control.
_FORGED_FRAGMENTS = (b"ao1019", b"-e2e-", b"not-the-", b"signing-key")
FORGED_SIGNING_KEY = b"".join(_FORGED_FRAGMENTS).decode("utf-8")

# --- the inference scenario ------------------------------------------------- #

#: The call class the conversion's inference belongs to (PF-5's registry).
CALL_CLASS = "system-head"

#: How many in-class calls the scenario makes.
CALLS = 4

#: Declared token flow, per call.  The repo carries no tokenizer, so the split
#: is declared (exactly as cache_baseline.SYNTHETIC_SAMPLE declares its own);
#: which *side* of the split is billed is what the prefix module decides.
PROMPT_TOKENS = 2400
PREFIX_TOKENS = 2048
DELTA_TOKENS = PROMPT_TOKENS - PREFIX_TOKENS
OUTPUT_TOKENS = 400

PROVIDER = "deepseek"
MODEL = "deepseek-chat"

#: Per-call volatile tokens (injected).  Two calls in the class differ only
#: here — which is precisely what a canonical prefix template absorbs.
SESSION_IDS = (
    "3f2a1b9c-8d4e-4f1a-9b2c-3d4e5f6a7b8c",
    "0b1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e",
    "a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d",
    "9f8e7d6c-5b4a-4938-8271-6f5e4d3c2b1a",
)
SESSION_STARTED = (
    "2026-03-02T10:05:00Z",
    "2026-03-02T10:06:00Z",
    "2026-03-02T10:07:00Z",
    "2026-03-02T10:08:00Z",
)

# --- the trim scenario ------------------------------------------------------ #

#: The tool whose payload the response filter is declared to bound.  The rule
#: itself (budget, head, key sections) is read from the module, never restated.
TRIM_TOOL = "code.references"
#: A tool the closed rule registry does NOT declare — the refusal control.
UNDECLARED_TOOL = "code.delete_all"
MESSAGE_ID = "msg-pf5-1"

# --- the module-level singletons ------------------------------------------- #

_RATE_CARDS: Optional[RateCardStore] = None
_INTAKE: Optional[MeteringIntake] = None


def rate_cards() -> RateCardStore:
    """The shipped multi-provider rate cards (loaded once, offline)."""
    global _RATE_CARDS
    if _RATE_CARDS is None:
        _RATE_CARDS = RateCardStore.load_dir()
    return _RATE_CARDS


def intake() -> MeteringIntake:
    """The metering intake — the cost authority this probe prices through."""
    global _INTAKE
    if _INTAKE is None:
        _INTAKE = MeteringIntake(rate_store=rate_cards())
    return _INTAKE


@dataclass(frozen=True)
class Loop:
    """One full run of the chain, with each stage's own measured failures."""

    conversion: Dict[str, Any]
    inference: Dict[str, Any]
    trim: Dict[str, Any]

    def failures(self) -> Tuple[str, ...]:
        return tuple(
            f"{stage}: {failure}"
            for stage, evidence in (
                ("conversion", self.conversion),
                ("inference", self.inference),
                ("trim", self.trim),
            )
            for failure in evidence.get("failures", ())
        )


# --------------------------------------------------------------------------- #
# stage 1 — the conversion bridge (CRM hook -> ERPNext ledger)
# --------------------------------------------------------------------------- #
def _run_crm_funnel() -> Any:
    """Drive the CRM lane's own conversion funnel on an injected clock."""
    from integrations.erp.crm import flows as crm
    from integrations.erp.crm.model import KIND_LEAD

    space = crm.workspace(TENANT)
    space = crm.create_document(
        space,
        KIND_LEAD,
        LEAD_ID,
        {"company": CUSTOMER_NAME, "stage": "new", "source": "web", "value": 250000},
        actor=ACTOR,
        at=CRM_CLOCK["opened"],
        title="Northwind renewal",
        owner="rep-1",
    )
    space = crm.advance(space, LEAD_ID, "contacted", actor=ACTOR, at=CRM_CLOCK["contacted"])
    space = crm.advance(space, LEAD_ID, "qualified", actor=ACTOR, at=CRM_CLOCK["qualified"])
    space = crm.convert_lead(
        space, LEAD_ID, OPPORTUNITY_ID, actor=ACTOR, at=CRM_CLOCK["converted"]
    )
    space = crm.advance(
        space, OPPORTUNITY_ID, "proposal", actor=ACTOR, at=CRM_CLOCK["proposal"]
    )
    space = crm.win_opportunity(
        space, OPPORTUNITY_ID, CUSTOMER_ID, actor=ACTOR, at=CRM_CLOCK["won"]
    )
    return space


def conversion_event() -> Dict[str, Any]:
    """The conversion event the CRM lane's own won opportunity raises.

    The envelope is the bridge lane's declared shape; its ``sourceDocument``
    and ``customerId`` name the ids the CRM module itself minted above, so the
    event is a *real* conversion rather than a payload written to fit.
    """
    from integrations.erp.webhooks.model import HOOK_OPPORTUNITY_WIN

    return {
        "schemaVersion": 1,
        "eventId": EVENT_ID,
        "hook": HOOK_OPPORTUNITY_WIN,
        "tenant": TENANT,
        "occurredAt": CRM_CLOCK["won"],
        "customerId": CUSTOMER_ID,
        "customerName": CUSTOMER_NAME,
        "amount": CONVERSION_AMOUNT,
        "currency": "usd",
        "sourceDocument": "opportunity/{}".format(OPPORTUNITY_ID),
    }


def _raw_and_header(event: Dict[str, Any], key: Optional[str] = None) -> Tuple[bytes, str]:
    """The raw delivery body and the signature header a sender would present."""
    from integrations.erp.webhooks import auth as webhook_auth

    raw = json.dumps(event, sort_keys=True).encode("utf-8")
    return raw, webhook_auth.sign(key or WEBHOOK_SIGNING_KEY, raw)


def _posting_policy() -> Any:
    from integrations.erp.tx.ledger import PostingPolicy

    return PostingPolicy(
        accounts={"receivable": RECEIVABLE_ACCOUNT, "income": INCOME_ACCOUNT}
    )


def enabled_bridge() -> Any:
    """A bridge with this lane's flag explicitly ON (the promoted state)."""
    from integrations.erp.webhooks.bridge import ConversionBridge
    from integrations.erp.webhooks.flags import FLAG_ID

    return ConversionBridge(
        secret=WEBHOOK_SIGNING_KEY,
        posting_policy=_posting_policy(),
        flags={FLAG_ID: True},
    )


def _stage_conversion() -> Dict[str, Any]:
    """Post the conversion and read the posting back through the ledger owner."""
    from integrations.erp.tx.ledger import GeneralLedger
    from integrations.erp.webhooks import auth as webhook_auth
    from integrations.erp.webhooks import schema as webhook_schema
    from integrations.erp.webhooks.bridge import (
        ROLE_INCOME,
        ROLE_RECEIVABLE,
        VOUCHER_TYPE,
    )
    from integrations.erp.webhooks.model import HOOK_OPPORTUNITY_WIN

    failures: List[str] = []
    space = _run_crm_funnel()
    for finding in space.findings():
        failures.append("crm {}: {}".format(finding.code, finding.detail))

    customer = space.get(CUSTOMER_ID)
    if customer.kind != "customer":
        failures.append("the won opportunity did not produce a customer document")

    event = conversion_event()
    parsed = webhook_schema.parse(event)
    if parsed.hook != HOOK_OPPORTUNITY_WIN:
        failures.append("the parsed event names hook {!r}".format(parsed.hook))
    if parsed.source_document != "opportunity/{}".format(OPPORTUNITY_ID):
        failures.append(
            "the event cites {!r}, not the CRM's own won opportunity".format(
                parsed.source_document
            )
        )

    raw, header = _raw_and_header(event)
    # The signature is verified through the module that owns it, before the
    # bridge is asked to handle the delivery.
    webhook_auth.verify(WEBHOOK_SIGNING_KEY, raw, header)

    bridge = enabled_bridge()
    result = bridge.handle(raw, header, event, at=CRM_CLOCK["won"])
    if result.status != "posted":
        failures.append(
            "the delivery was not posted ({}, {})".format(result.status, result.reason)
        )

    # Read-back through the module that OWNS the posting: the ledger.  Every
    # figure below is the ledger's own, or the parsed event's own -- none of
    # them is a value this probe chose.
    entries = bridge.ledger.for_document(result.voucher_id)
    debits, credits = bridge.ledger.totals()
    findings = bridge.ledger.verify()
    balances = bridge.ledger.balances()
    net = bridge.ledger.net_for_document(result.voucher_id)
    policy = _posting_policy()
    accounted = sorted(entry.account for entry in entries)

    if not isinstance(bridge.ledger, GeneralLedger):
        failures.append("the bridge did not post into a real GeneralLedger")
    if findings:
        failures.append(
            "the ledger contradicts itself: {}".format(
                "; ".join("{}: {}".format(f.code, f.detail) for f in findings)
            )
        )
    if len(entries) != 2:
        failures.append("the voucher holds {} row(s), not 2".format(len(entries)))
    if accounted != sorted((RECEIVABLE_ACCOUNT, INCOME_ACCOUNT)):
        failures.append("the voucher posts against {}".format(accounted))
    posted = sum(entry.debit for entry in entries)
    if posted != parsed.amount:
        failures.append(
            "the voucher debits {!r}, not the event's own amount {!r}".format(
                posted, parsed.amount
            )
        )
    if debits != credits or debits <= 0:
        failures.append(
            "the ledger posts {!r} of debits against {!r} of credits".format(
                debits, credits
            )
        )
    if net != 0:
        failures.append("the voucher nets to {!r}, not zero".format(net))
    if result.voucher_id != "{}:{}".format(VOUCHER_TYPE, parsed.event_id):
        failures.append(
            "the voucher is {!r}, not the bridge's own {!r}".format(
                result.voucher_id, "{}:{}".format(VOUCHER_TYPE, parsed.event_id)
            )
        )
    if policy.resolve(ROLE_RECEIVABLE) not in balances:
        failures.append("the receivable account never moved")
    if policy.resolve(ROLE_INCOME) not in balances:
        failures.append("the income account never moved")

    # The replay: the same delivery again must not post a second time.
    replay = bridge.handle(raw, header, event, at=CRM_CLOCK["won"])
    if replay.status != "duplicate":
        failures.append("a replayed delivery returned {!r}".format(replay.status))
    if len(bridge.ledger.entries) != len(entries):
        failures.append("a replayed delivery moved the ledger again")

    return {
        "failures": failures,
        "tenant": TENANT,
        "actor": ACTOR,
        "crmDocuments": sorted(space.documents),
        "crmCustomerKind": customer.kind,
        "crmCustomerState": customer.state,
        "crmRailEntries": len(space.rail),
        "event": {
            "eventId": parsed.event_id,
            "hook": parsed.hook,
            "tenant": parsed.tenant,
            "occurredAt": parsed.occurred_at,
            "customerId": parsed.customer_id,
            "sourceDocument": parsed.source_document,
            "amount": parsed.amount,
            "currency": parsed.currency,
            "idempotencyKey": parsed.idempotency_key,
        },
        "signatureVerified": True,
        "signatureHeaderPrefix": header.split("=")[0],
        "result": result.to_dict(),
        "voucherId": result.voucher_id,
        "posting": {
            "rows": [entry.to_dict() for entry in entries],
            "accounts": accounted,
            "debitTotal": debits,
            "creditTotal": credits,
            "netForVoucher": net,
            "balances": balances,
            "verify": [],
            "readBackThrough": "integrations.erp.tx.ledger.GeneralLedger",
        },
        "replay": replay.to_dict(),
        "ledgerRows": len(bridge.ledger.entries),
    }


# --------------------------------------------------------------------------- #
# stage 2 — cache-optimal inference -> metered cost
# --------------------------------------------------------------------------- #
def canonical_prompt(index: int, voucher_id: str) -> str:
    """The canonical composition: this class's template with THIS call's tokens.

    The template is spelled out rather than read from
    ``prefix_templates.PREFIX_TEMPLATES``: a probe that composed *from* the
    registry it then validates *with* would prove only that the module equals
    itself.  ``gateway/finops/tests/test_prefix_templates.py`` keeps its own
    fixtures independent for the same reason.
    """
    return (
        "You are an enterprise AI-agent orchestration assistant.\n"
        "Session {} started at {}.\n"
        "Respond concisely and accurately.\n".format(
            SESSION_IDS[index], SESSION_STARTED[index]
        )
        + _user_delta(voucher_id)
    )


def uncanonical_prompt(index: int, voucher_id: str) -> str:
    """The same scenario composed WITHOUT the class template.

    A per-call marker is stamped into the static head — the "dynamic value in
    the static prefix" anti-pattern ``engine/memory/prompt_cache.py`` exists to
    prevent — so the prefix from token 0 differs on every call even though the
    volatile session tokens are unchanged.
    """
    return (
        "Session {} started at {}. Call {} of {} for tenant {}.\n".format(
            SESSION_IDS[index], SESSION_STARTED[index], index + 1, CALLS, TENANT
        )
        + "You are an enterprise AI-agent orchestration assistant.\n"
        + "Respond concisely and accurately.\n"
        + _user_delta(voucher_id)
    )


def _user_delta(voucher_id: str) -> str:
    """The per-call user delta — after the cacheable prefix, by construction."""
    return "User request:\nSummarize posting {} for tenant {}.".format(voucher_id, TENANT)


def _prefix_accepted(prompt: str) -> bool:
    """Whether the canonical prefix validator accepts this composition."""
    try:
        prefix_templates.validate_prefix(CALL_CLASS, prompt)
    except prefix_templates.PrefixTemplateError:
        return False
    return True


def cache_disposition(prompts: Sequence[str]) -> Dict[str, Any]:
    """Whether the provider's prefix cache can serve these calls.

    Two conditions, both read from ``gateway/finops/prefix_templates``: every
    call must pass ``validate_prefix`` for the class (the canonical prefix is
    intact) AND every call must normalise to ONE ``footprint`` (one cache key
    from token 0).  Only then can the provider serve all but the first call
    from its prefix cache — so this, not a constant, is what decides whether the
    scenario is warm or cold.
    """
    footprints = [prefix_templates.footprint(prompt) for prompt in prompts]
    accepted = sum(1 for prompt in prompts if _prefix_accepted(prompt))
    return {
        "calls": len(prompts),
        "acceptedByPrefixValidator": accepted,
        "distinctFootprints": len(set(footprints)),
        "footprints": footprints,
        "warm": accepted == len(prompts) and len(set(footprints)) == 1,
    }


def usage_rows(disposition: Dict[str, Any], billing_query_id: str) -> List[Any]:
    """The provider's cache-efficiency observation for each call.

    The FIRST call of a warm class fills the cache (all prompt tokens billed
    uncached); every later call is served from the shared prefix, so only the
    delta is billed.  A class the provider cannot cache bills every prompt token
    on every call.
    """
    rows: List[Any] = []
    for index in range(disposition["calls"]):
        if disposition["warm"] and index > 0:
            hit, miss = PREFIX_TOKENS, DELTA_TOKENS
        else:
            hit, miss = 0, PROMPT_TOKENS
        rows.append(
            cache_audit.UsageRow(
                hit_tokens=hit,
                miss_tokens=miss,
                billing_query_id=billing_query_id,
                model=MODEL,
            )
        )
    return rows


def _usage_lines(rows: Sequence[Any]) -> List[str]:
    """Serialize rows in the JSON Lines shape ``cache_audit.load_usage`` reads."""
    return [
        json.dumps(
            {
                "provider": PROVIDER,
                "model": row.model,
                "prompt_cache_hit_tokens": row.hit_tokens,
                "prompt_cache_miss_tokens": row.miss_tokens,
                "billing_query_id": row.billing_query_id,
            },
            sort_keys=True,
        )
        for row in rows
    ]


def _metered_call(miss_tokens: int, at: str) -> Dict[str, Any]:
    """Meter one call through ``telemetry/metering`` and report what it resolved.

    The record is the gateway/providers ``ModelCallEvent`` shape the intake
    documents.  ``input_tokens`` carries the prompt tokens the provider billed
    as *uncached* — the cache-hit tokens are the discount the prefix template
    earned, and this pillar's rate cards price only the standard input rate.
    ``ts`` is injected: ``telemetry.metering.parse_ts(None)`` falls back to the
    wall clock, so a record with no stamp would make the cost a function of when
    the suite ran.
    """
    record = intake().normalize(
        {
            "tenant_id": TENANT,
            "agent_id": ACTOR,
            "provider": PROVIDER,
            "model": MODEL,
            "logical_key": CALL_CLASS,
            "status": "success",
            "usage": {"input_tokens": miss_tokens, "output_tokens": OUTPUT_TOKENS},
            "ts": at,
        }
    )
    # The same figure computed by the rate-card store directly: the record's own
    # price is asserted equal to it here, where the figure is produced, so no
    # caller has to take the cost on trust.
    from_rate_card = rate_cards().estimate(
        PROVIDER, MODEL, miss_tokens, OUTPUT_TOKENS
    )
    return {
        "ts": record.ts,
        "sourceType": record.source_type,
        "sourceKey": record.source_key,
        "metered": record.metered,
        "billable": record.billable,
        "costUsd": record.cost_usd,
        "costSource": record.cost_source,
        "unmeteredReason": record.unmetered_reason,
        "inputTokens": record.input_tokens,
        "outputTokens": record.output_tokens,
        "costUsdFromRateCard": None
        if from_rate_card is None
        else from_rate_card.cost_usd,
        "costMatchesRateCard": (
            from_rate_card is not None and record.cost_usd == from_rate_card.cost_usd
        ),
    }


def _stage_inference(voucher_id: str, scratch_dir: Optional[str] = None) -> Dict[str, Any]:
    """Measure the cache-optimal half, joined to the accounting half."""
    failures: List[str] = []
    cards = rate_cards()

    bucket = scratch_dir or tempfile.mkdtemp(prefix="ao1019-usage.")
    os.makedirs(bucket, exist_ok=True)
    path = Path(bucket) / "usage.jsonl"

    stages: Dict[str, Any] = {}
    for name, composer in (
        ("canonical", canonical_prompt),
        ("uncanonical", uncanonical_prompt),
    ):
        prompts = [composer(index, voucher_id) for index in range(CALLS)]
        disposition = cache_disposition(prompts)
        rows = usage_rows(disposition, voucher_id)

        # The recorded usage the provider would have written, read back through
        # the audit module's own loader rather than trusted in memory.
        path.write_text("\n".join(_usage_lines(rows)) + "\n", encoding="utf-8")
        loaded = cache_audit.load_usage(path)
        audit = cache_audit.audit(loaded)

        calls = [
            _metered_call(row.miss_tokens, SESSION_STARTED[index])
            for index, row in enumerate(loaded)
        ]
        sources = sorted({call["costSource"] for call in calls})
        if sources != ["rate_card"]:
            failures.append(
                "{}: the metered cost resolved from {} not the rate cards".format(
                    name, sources
                )
            )
        for index, call in enumerate(calls):
            if call["ts"] != SESSION_STARTED[index]:
                failures.append(
                    "{}: call {} carries ts {!r}, not the injected {!r}".format(
                        name, index, call["ts"], SESSION_STARTED[index]
                    )
                )
            if not call["costMatchesRateCard"]:
                failures.append(
                    "{}: call {} cost {!r} is not the rate card's {!r}".format(
                        name,
                        index,
                        call["costUsd"],
                        call["costUsdFromRateCard"],
                    )
                )

        stages[name] = {
            "disposition": disposition,
            "prefixAcceptedEveryCall": disposition["acceptedByPrefixValidator"] == CALLS,
            "rows": [
                {
                    "hitTokens": row.hit_tokens,
                    "missTokens": row.miss_tokens,
                    "billingQueryId": row.billing_query_id,
                }
                for row in loaded
            ],
            "recordedHitTokens": audit.total.hit_tokens,
            "recordedMissTokens": audit.total.miss_tokens,
            "promptTokens": audit.total.hit_tokens + audit.total.miss_tokens,
            "cacheHitRatio": audit.total.hit_ratio,
            "hasBillingQuery": audit.has_billing_query,
            "perQuery": sorted(audit.per_query),
            "joinLine": cache_audit.render(audit).splitlines()[0],
            "calls": calls,
            "costUsd": sum(call["costUsd"] for call in calls),
            "costUsdFromRateCards": sum(
                call["costUsdFromRateCard"] for call in calls
            ),
            "costSources": sources,
            "everyCallMatchesRateCard": all(
                call["costMatchesRateCard"] for call in calls
            ),
        }

    warm = stages["canonical"]
    cold = stages["uncanonical"]

    if not warm["disposition"]["warm"]:
        failures.append("the canonical composition did not produce a cacheable prefix")
    if cold["disposition"]["warm"]:
        failures.append("the uncanonical composition was cacheable after all")
    if warm["cacheHitRatio"] <= 0:
        failures.append("the cache-optimal scenario reports no cache hits")
    if cold["cacheHitRatio"] != 0:
        failures.append("the cold scenario reports cache hits")
    if not (warm["costUsd"] < cold["costUsd"]):
        failures.append(
            "the cache-optimal scenario is not cheaper ({!r} vs {!r})".format(
                warm["costUsd"], cold["costUsd"]
            )
        )

    # The gap, expressed as the rate card's own price for the tokens the cache
    # saved -- so the inequality above is checked against the cost authority
    # rather than a figure this probe chose.
    saved = cold["recordedMissTokens"] - warm["recordedMissTokens"]
    gap = cards.estimate(PROVIDER, MODEL, saved, 0)
    if gap is None:
        failures.append("the rate card cannot price {}/{}".format(PROVIDER, MODEL))
        gap_cost = None
    else:
        gap_cost = gap.cost_usd
        measured_gap = cold["costUsd"] - warm["costUsd"]
        # Money tolerance: the repo's own ledger TOLERANCE is 0.005; this
        # comparison is between two sums of the SAME per-call figures, so a
        # picodollar slack is already generous.
        if abs(measured_gap - gap_cost) > 1e-12:
            failures.append(
                "the cost gap {!r} is not the rate card's price {!r} for the "
                "cached tokens".format(measured_gap, gap_cost)
            )

    # The baseline (#667) prices from the same cards: its standard leg must equal
    # the rate card's own computation, and it must independently agree the
    # cache-adjusted path is cheaper.
    class_row = cache_baseline.ClassRow(
        call_class=CALL_CLASS,
        source=cache_baseline.SOURCE_MEASURED,
        model=MODEL,
        requests=CALLS,
        prompt_tokens=warm["promptTokens"],
        output_tokens=CALLS * OUTPUT_TOKENS,
        hit_tokens=warm["recordedHitTokens"],
        miss_tokens=warm["recordedMissTokens"],
        cache_assessable=True,
    )
    standard_cost, cache_adjusted_cost = class_row.cost(cards)
    from_card = cards.estimate(PROVIDER, MODEL, warm["promptTokens"], CALLS * OUTPUT_TOKENS)
    if from_card is None or standard_cost != from_card.cost_usd:
        failures.append("the baseline's standard cost is not the rate card's")

    return {
        "failures": failures,
        "callClass": CALL_CLASS,
        "provider": PROVIDER,
        "model": MODEL,
        "tenant": TENANT,
        "calls": CALLS,
        "promptTokensPerCall": PROMPT_TOKENS,
        "prefixTokens": PREFIX_TOKENS,
        "deltaTokens": DELTA_TOKENS,
        "outputTokensPerCall": OUTPUT_TOKENS,
        "billingQueryId": voucher_id,
        "rateCard": {
            "source": cards.card(PROVIDER).source,
            "fingerprint": cards.fingerprint(),
            "standard": cards.lookup(PROVIDER, MODEL).standard.to_dict(),
        },
        "warm": warm,
        "cold": cold,
        "savedTokens": saved,
        "costGapUsd": cold["costUsd"] - warm["costUsd"],
        "costGapFromRateCardUsd": gap_cost,
        "costGapMatchesRateCard": bool(
            gap_cost is not None and abs(cold["costUsd"] - warm["costUsd"] - gap_cost) <= 1e-12
        ),
        "baseline": {
            "source": class_row.source,
            "cacheHitRatio": class_row.cache_hit_ratio,
            "standardCostUsd": standard_cost,
            "cacheAdjustedCostUsd": cache_adjusted_cost,
            "standardMatchesRateCard": bool(
                from_card is not None and standard_cost == from_card.cost_usd
            ),
            "cacheAdjustmentCheaper": bool(
                cache_adjusted_cost is not None
                and standard_cost is not None
                and cache_adjusted_cost < standard_cost
            ),
        },
    }


# --------------------------------------------------------------------------- #
# stage 3 — the MCP response filter trims before the model sees it
# --------------------------------------------------------------------------- #
def _verbose_tool_payload(references: int = 400) -> Dict[str, Any]:
    """A ``code.references``-shaped payload far over its declared budget."""
    return {
        "symbol": "charge",
        "repo": "acme/payments",
        "count": references,
        "references": [
            {
                "path": "src/payments/{}.py".format(index % 11),
                "line": index,
                "context": "charge(account, amount={})".format(index),
            }
            for index in range(references)
        ],
    }


def model_visible(text: str) -> str:
    """The ``tools/call`` result envelope the model is handed, as JSON.

    Built by the protocol module itself, so "what the model sees" is the real
    envelope and not a byte count this probe invented.
    """
    from mcp.protocol import result_body, tool_result

    return json.dumps(result_body(MESSAGE_ID, tool_result(text)), sort_keys=True)


def _stage_trim() -> Dict[str, Any]:
    """Measure the filter's effect on the payload that reaches the model."""
    from mcp.response_filter import TRUNCATION_MARKER, ResponseFilter

    failures: List[str] = []
    payload = _verbose_tool_payload()
    serialized = json.dumps(payload, sort_keys=True, indent=2)

    filter_enabled = ResponseFilter(enabled=True)
    rule = filter_enabled.rules()[TRIM_TOOL]
    trimmed = filter_enabled.filter(TRIM_TOOL, payload)

    before = model_visible(serialized)
    after = model_visible(trimmed)

    if len(trimmed) >= len(serialized):
        failures.append(
            "the filter did not shrink the payload ({} -> {})".format(
                len(serialized), len(trimmed)
            )
        )
    if len(trimmed) > rule.budget_chars:
        failures.append(
            "the filtered payload is {} chars, over the declared budget {}".format(
                len(trimmed), rule.budget_chars
            )
        )
    if TRUNCATION_MARKER not in trimmed:
        failures.append("the filtered payload carries no elision marker")
    if len(after) >= len(before):
        failures.append(
            "the model-visible envelope did not shrink ({} -> {})".format(
                len(before), len(after)
            )
        )
    for key in rule.key_sections:
        if '"{}"'.format(key) not in trimmed:
            failures.append("the key section {!r} was elided".format(key))

    # The pairing: the same payload with the filter OFF passes through
    # byte-identically, so the shrinkage above is the filter's, not the
    # envelope's.
    inert = ResponseFilter(enabled=False)
    untrimmed = inert.filter(TRIM_TOOL, payload)

    return {
        "failures": failures,
        "tool": TRIM_TOOL,
        "budgetChars": rule.budget_chars,
        "keepHeadChars": rule.keep_head_chars,
        "keySections": list(rule.key_sections),
        "payloadChars": len(serialized),
        "trimmedChars": len(trimmed),
        "markerPresent": TRUNCATION_MARKER in trimmed,
        "modelVisibleChars": len(before),
        "modelVisibleTrimmedChars": len(after),
        "smallerBeforeTheModel": len(after) < len(before),
        "keySectionsSurvived": all(
            '"{}"'.format(key) in trimmed for key in rule.key_sections
        ),
        "disabledPassesByteIdentical": untrimmed == serialized,
        "insideBudget": len(trimmed) <= rule.budget_chars,
    }


# --------------------------------------------------------------------------- #
# the whole chain
# --------------------------------------------------------------------------- #
def probe_loop(scratch_dir: Optional[str] = None) -> Loop:
    """Run the three stages over one conversion and return the measurements."""
    conversion = _stage_conversion()
    return Loop(
        conversion=conversion,
        inference=_stage_inference(conversion["voucherId"], scratch_dir=scratch_dir),
        trim=_stage_trim(),
    )


# --------------------------------------------------------------------------- #
# negative controls — each proves a refusal, and each could have failed
# --------------------------------------------------------------------------- #
def probe_controls() -> Dict[str, Any]:
    """Provoke every refusal path this probe relies on and record each verdict.

    A control passes only when the real module refuses *by name* AND the paired
    measurement comes out the other way when the one thing under test changes —
    so a guard that refused everything, or refused nothing, could not pass.  Each
    control also names the branch a mutation must disable: an external driver
    (``/tmp/ao1019-mutate.sh``, nothing patched in place) copies that module,
    removes that branch, and re-runs the control's own test, which must then
    FAIL.
    """
    controls = [
        _control_flag_off(),
        _control_forged_signature(),
        _control_replayed_delivery(),
        _control_prefix_deviation(),
        _control_undeclared_tool(),
        _control_absent_cache_split(),
        _control_absent_join_key(),
    ]
    failed = [control["controlId"] for control in controls if not control["passed"]]
    return {"controls": controls, "failedControls": failed, "passed": not failed}


def _control_flag_off() -> Dict[str, Any]:
    """With ``erp-webhooks-bridge`` off, the bridge refuses and posts nothing."""
    from integrations.erp.webhooks import flags as flags_module
    from integrations.erp.webhooks.bridge import ConversionBridge
    from integrations.erp.webhooks.flags import FLAG_ID

    event = conversion_event()
    raw, header = _raw_and_header(event)

    closed = ConversionBridge(secret=WEBHOOK_SIGNING_KEY, posting_policy=_posting_policy())
    result = closed.handle(raw, header, event, at=CRM_CLOCK["won"])
    quarantine = closed.quarantine_store.all()

    # The pairing: the same delivery with the flag on does post, so the refusal
    # is the flag's and not a payload that could never be accepted.
    open_bridge = enabled_bridge()
    accepted = open_bridge.handle(raw, header, event, at=CRM_CLOCK["won"])

    refused = result.status == "quarantined" and FLAG_ID in (result.reason or "")
    passed = (
        flags_module.is_enabled(None) is False
        and flags_module.is_enabled({FLAG_ID: False}) is False
        and refused
        and len(closed.ledger.entries) == 0
        and len(quarantine) == 1
        and len(closed.idempotency_store) == 0
        and accepted.status == "posted"
        and len(open_bridge.ledger.entries) == 2
    )
    return {
        "controlId": "flag-off-refuses-every-delivery",
        "passed": passed,
        "refusedBy": quarantine[0].code if quarantine else "",
        "detail": result.reason or "",
        "mutant": {
            "name": "webhook-flags",
            "module": "integrations.erp.webhooks.flags",
            "branch": "is_enabled",
            "mustFail": "test_the_flag_off_bridge_refuses_and_posts_nothing",
        },
        "evidence": {
            "flagId": FLAG_ID,
            "defaultEnabled": flags_module.DEFAULT_ENABLED,
            "isEnabledWithNoFlags": flags_module.is_enabled(None),
            "isEnabledWithFlagOff": flags_module.is_enabled({FLAG_ID: False}),
            "status": result.status,
            "reasonNamesTheFlag": FLAG_ID in (result.reason or ""),
            "ledgerRows": len(closed.ledger.entries),
            "quarantinedEvents": len(quarantine),
            "idempotencyKeys": len(closed.idempotency_store),
            "pairedAcceptedStatus": accepted.status,
            "pairedLedgerRows": len(open_bridge.ledger.entries),
        },
    }


def _control_forged_signature() -> Dict[str, Any]:
    """A signature that does not verify is refused and nothing is posted."""
    from integrations.erp.webhooks import auth as webhook_auth
    from integrations.erp.webhooks.model import Refused

    event = conversion_event()
    raw, honest_header = _raw_and_header(event)
    forged = webhook_auth.sign(FORGED_SIGNING_KEY, raw)

    raised = ""
    detail = ""
    try:
        webhook_auth.verify(WEBHOOK_SIGNING_KEY, raw, forged)
    except Refused as refusal:
        raised = refusal.code
        detail = refusal.detail

    unlabeled = ""
    try:
        webhook_auth.verify(WEBHOOK_SIGNING_KEY, raw, "not-a-labelled-signature")
    except Refused as refusal:
        unlabeled = refusal.code

    missing = ""
    try:
        webhook_auth.verify(WEBHOOK_SIGNING_KEY, raw, None)
    except Refused as refusal:
        missing = refusal.code

    bridge = enabled_bridge()
    result = bridge.handle(raw, forged, event, at=CRM_CLOCK["won"])
    quarantine = bridge.quarantine_store.all()

    # The pairing: the honest signature is accepted and posts.
    honest_bridge = enabled_bridge()
    honest = honest_bridge.handle(raw, honest_header, event, at=CRM_CLOCK["won"])

    passed = (
        raised == "auth-failed"
        and unlabeled == "auth-failed"
        and missing == "auth-failed"
        and result.status == "quarantined"
        and (result.reason or "").startswith("auth-failed")
        and len(bridge.ledger.entries) == 0
        and len(bridge.idempotency_store) == 0
        and len(quarantine) == 1
        and honest.status == "posted"
        and len(honest_bridge.ledger.entries) == 2
    )
    return {
        "controlId": "forged-signature-refused-nothing-posted",
        "passed": passed,
        "refusedBy": raised,
        "detail": detail,
        "mutant": {
            "name": "webhook-auth",
            "module": "integrations.erp.webhooks.auth",
            "branch": "verify",
            "mustFail": "test_a_forged_signature_is_refused_and_posts_nothing",
        },
        "evidence": {
            "verifyRaised": raised,
            "unlabeledHeaderRaised": unlabeled,
            "absentHeaderRaised": missing,
            "status": result.status,
            "reasonPrefix": (result.reason or "").split(":")[0],
            "ledgerRows": len(bridge.ledger.entries),
            "quarantinedEvents": len(quarantine),
            "idempotencyKeys": len(bridge.idempotency_store),
            "pairedAcceptedStatus": honest.status,
            "pairedLedgerRows": len(honest_bridge.ledger.entries),
        },
    }


def _control_replayed_delivery() -> Dict[str, Any]:
    """A replayed delivery returns the first outcome and posts nothing more."""
    event = conversion_event()
    raw, header = _raw_and_header(event)
    bridge = enabled_bridge()

    first = bridge.handle(raw, header, event, at=CRM_CLOCK["won"])
    rows_after_first = len(bridge.ledger.entries)
    second = bridge.handle(raw, header, event, at=CRM_CLOCK["won"])

    passed = (
        first.status == "posted"
        and second.status == "duplicate"
        and second.voucher_id == first.voucher_id
        and len(bridge.ledger.entries) == rows_after_first == 2
        and len(bridge.idempotency_store) == 1
        and len(bridge.quarantine_store) == 0
    )
    return {
        "controlId": "replayed-delivery-posts-once",
        "passed": passed,
        "refusedBy": "duplicate",
        "detail": second.reason or "",
        "mutant": {
            "name": "webhook-idempotency",
            "module": "integrations.erp.webhooks.idempotency",
            "branch": "seen",
            "mustFail": "test_a_replayed_delivery_does_not_post_twice",
        },
        "evidence": {
            "firstStatus": first.status,
            "secondStatus": second.status,
            "sameVoucher": second.voucher_id == first.voucher_id,
            "ledgerRowsAfterFirst": rows_after_first,
            "ledgerRowsAfterReplay": len(bridge.ledger.entries),
            "idempotencyKeys": len(bridge.idempotency_store),
            "quarantinedEvents": len(bridge.quarantine_store),
        },
    }


def _control_prefix_deviation() -> Dict[str, Any]:
    """A composed prompt whose prefix deviates is refused, naming the class."""
    voucher = "crm-conversion:{}".format(EVENT_ID)
    canonical = canonical_prompt(0, voucher)
    deviating = uncanonical_prompt(0, voucher)

    refused = ""
    try:
        prefix_templates.validate_prefix(CALL_CLASS, deviating)
    except prefix_templates.PrefixTemplateError as refusal:
        refused = str(refusal)

    # The pairing: the canonical composition of the SAME scenario is accepted.
    accepted = _prefix_accepted(canonical)

    unknown = ""
    try:
        prefix_templates.validate_prefix("no-such-call-class", canonical)
    except prefix_templates.PrefixTemplateError as refusal:
        unknown = str(refusal)

    passed = (
        CALL_CLASS in refused
        and "prefix deviation" in refused
        and accepted
        and "no-such-call-class" in unknown
    )
    return {
        "controlId": "deviating-prefix-refused-by-name",
        "passed": passed,
        "refusedBy": "prefix-deviation",
        "detail": refused,
        "mutant": {
            "name": "prefix-guard",
            "module": "prefix_templates",
            "branch": "validate_prefix",
            "mustFail": "test_a_deviating_prefix_is_refused_by_name",
        },
        "evidence": {
            "canonicalAccepted": accepted,
            "deviatingRefused": bool(refused),
            "refusalNamesTheClass": CALL_CLASS in refused,
            "unknownClassRefused": bool(unknown),
            "canonicalFootprint": prefix_templates.footprint(canonical),
            "deviatingFootprint": prefix_templates.footprint(deviating),
            "footprintsDiffer": (
                prefix_templates.footprint(canonical)
                != prefix_templates.footprint(deviating)
            ),
        },
    }


def _control_undeclared_tool() -> Dict[str, Any]:
    """The filter's rule registry is closed: an undeclared tool is refused."""
    from mcp.response_filter import ResponseFilter, UnknownRuleError

    declared = ResponseFilter(enabled=True)
    refused = ""
    try:
        declared.filter(UNDECLARED_TOOL, {"symbol": "x"})
    except UnknownRuleError as refusal:
        refused = str(refusal)

    # The pairing: a tool the registry DOES declare is filtered normally, so the
    # refusal is the registry's closedness, not a filter that refuses anything.
    paired = declared.filter(TRIM_TOOL, _verbose_tool_payload())

    passed = (
        not declared.has_rule(UNDECLARED_TOOL)
        and UNDECLARED_TOOL in refused
        and len(paired) <= declared.rules()[TRIM_TOOL].budget_chars
    )
    return {
        "controlId": "undeclared-tool-refused-fail-closed",
        "passed": passed,
        "refusedBy": "UnknownRuleError",
        "detail": refused,
        "mutant": {
            "name": "mcp-filter",
            "module": "mcp.response_filter",
            "branch": "ResponseFilter.filter (budget)",
            "mustFail": "test_the_mcp_filter_trims_before_the_model_sees_it",
        },
        "evidence": {
            "hasRule": declared.has_rule(UNDECLARED_TOOL),
            "refused": bool(refused),
            "refusalNamesTheTool": UNDECLARED_TOOL in refused,
            "declaredTools": sorted(declared.rules()),
            "pairedToolFilteredChars": len(paired),
        },
    }


def _control_absent_cache_split() -> Dict[str, Any]:
    """A record with no cache headers is CANNOT-ASSESS, never a fabricated hit."""
    absent = cache_baseline._extract_cache_tokens(
        {"provider": PROVIDER, "usage": {"input_tokens": PROMPT_TOKENS}}
    )
    present = cache_baseline._extract_cache_tokens(
        {
            "prompt_cache_hit_tokens": PREFIX_TOKENS,
            "prompt_cache_miss_tokens": DELTA_TOKENS,
        }
    )
    unassessable = cache_baseline.ClassRow(
        call_class=CALL_CLASS,
        source=cache_baseline.SOURCE_MEASURED,
        model=MODEL,
        requests=CALLS,
        prompt_tokens=PROMPT_TOKENS,
        output_tokens=OUTPUT_TOKENS,
        hit_tokens=None,
        miss_tokens=None,
        cache_assessable=False,
    )
    standard_cost, cache_adjusted_cost = unassessable.cost(rate_cards())

    passed = (
        absent == (None, None)
        and present == (PREFIX_TOKENS, DELTA_TOKENS)
        and unassessable.cache_hit_ratio is None
        and cache_adjusted_cost is None
        and standard_cost is not None
    )
    return {
        "controlId": "absent-cache-split-is-cannot-assess",
        "passed": passed,
        "refusedBy": cache_baseline.CANNOT_ASSESS,
        "detail": "a record with no cache headers yields no cache-adjusted cost",
        "mutant": {
            "name": "baseline-honesty",
            "module": "cache_baseline",
            "branch": "_extract_cache_tokens",
            "mustFail": "test_an_absent_cache_split_is_cannot_assess_never_a_fabricated_hit",
        },
        "evidence": {
            "absentSplit": list(absent),
            "presentSplit": list(present),
            "absentRatio": unassessable.cache_hit_ratio,
            "absentCacheAdjustedCost": cache_adjusted_cost,
            "absentStandardCostIsPriced": standard_cost is not None,
        },
    }


def _control_absent_join_key() -> Dict[str, Any]:
    """With no billing-query axis, the audit reports no join, never a fake one."""
    rows = [
        cache_audit.UsageRow(hit_tokens=PREFIX_TOKENS, miss_tokens=DELTA_TOKENS, model=MODEL)
        for _ in range(CALLS)
    ]
    joined = cache_audit.audit([row for row in rows])
    with_axis = cache_audit.audit(
        [
            cache_audit.UsageRow(
                hit_tokens=row.hit_tokens,
                miss_tokens=row.miss_tokens,
                billing_query_id="crm-conversion:{}".format(EVENT_ID),
                model=MODEL,
            )
            for row in rows
        ]
    )

    passed = (
        joined.has_billing_query is False
        and joined.per_query == {}
        and cache_audit.NO_BILLING_QUERY in cache_audit.render(joined)
        and "TOTAL" in cache_audit.render(joined)
        and joined.total.hit_tokens == PREFIX_TOKENS * CALLS
        and with_axis.has_billing_query is True
        and sorted(with_axis.per_query) == ["crm-conversion:{}".format(EVENT_ID)]
    )
    return {
        "controlId": "absent-join-key-reported-never-fabricated",
        "passed": passed,
        "refusedBy": cache_audit.NO_BILLING_QUERY,
        "detail": "no record carries a billing-query id, so no per-query row is invented",
        "mutant": {
            "name": "audit-honesty",
            "module": "cache_audit",
            "branch": "AuditResult.has_billing_query",
            "mustFail": "test_an_absent_join_key_is_reported_never_fabricated",
        },
        "evidence": {
            "withoutAxisHasJoin": joined.has_billing_query,
            "withoutAxisPerQuery": sorted(joined.per_query),
            "withoutAxisJoinLine": cache_audit.render(joined).splitlines()[0],
            "withoutAxisTotalHitTokens": joined.total.hit_tokens,
            "withAxisPerQuery": sorted(with_axis.per_query),
        },
    }


# --------------------------------------------------------------------------- #
# the evidence writer / CLI
# --------------------------------------------------------------------------- #
def run_loop(*, work_dir: Optional[str] = None, scratch_dir: Optional[str] = None) -> Dict[str, Any]:
    """Run the chain and its controls, and return JSON-serializable evidence."""
    loop = probe_loop(scratch_dir=scratch_dir)
    controls = probe_controls()
    failures = list(loop.failures())
    if not controls["passed"]:
        failures.append("controls: {}".format(", ".join(controls["failedControls"])))
    payload = {
        "gate": "e2e.finops_loop",
        "issue": 1019,
        "tenant": TENANT,
        "conversion": loop.conversion,
        "inference": loop.inference,
        "trim": loop.trim,
        "negativeControls": controls,
        "failures": failures,
        "passed": not failures,
    }
    if work_dir:
        write_evidence(work_dir, "finops-loop.json", payload)
    return payload


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    if len(argv) > 1 and argv[0] == "--out":
        out = argv[1]
    payload = run_loop(work_dir=out)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("PF-5 LOOP: {}".format("PASS" if payload["passed"] else "FAIL"))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - a manual entry point
    raise SystemExit(main())
