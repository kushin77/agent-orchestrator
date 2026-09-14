# telemetry/chat — per-turn chat attribution, budget caps + prompt-cache accounting

> Owner lane: **telemetry** (issue `kushin77/agent-orchestrator#506`, child of
> EPIC **#500** chat in the SPoG). Doctrine: [`AGENTS.md`](../../AGENTS.md),
> [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).

The FinOps half of the chat surface: every chat turn is **attributed exactly
once**, **enforced before the model is called**, and **accounted for prompt
cache reuse** so a cache-hit turn is visibly cheaper than the same turn cold.

This lane owns `telemetry/chat/**` and `scripts/check-chat-finops.sh` only. It
**consumes** — and never redefines — the merged contracts it joins on.

## What a turn produces

| Row | Authority | Written by |
|---|---|---|
| `TurnAttribution` | this lane | `attribution.TurnAttributor` |
| one `UsageRecord` (metering) | `telemetry/metering` | `MeteringIntake.ingest` |
| one ledger event (audit) | `telemetry/ledger` | `LedgerStore.append` |
| the verdict (budget/quota/kill switch) | `telemetry/budgets` | `budget_guard.TurnBudgetGuard` |

Exactly one of each, per turn. A turn whose record cannot be identified fails
before anything is written, and re-attributing the same turn is refused
(`CHAT-DUPLICATE-TURN`) rather than double-counted.

## Consumed vocabulary (never redefined here)

| Contract | Where it comes from | How this lane uses it |
|---|---|---|
| decision ladder `allow` → `warn` → `block`/`refuse` | `telemetry.budgets.model` (from `gateway/finops`, issue #17) | reported unchanged as the turn's verdict |
| budget / quota / kill-switch rails | `telemetry/budgets` (`preflight`, issue #34) | composed pre-dispatch check |
| kill switch config | `telemetry/budgets/config/killswitch.yaml` | loaded, ships **OFF** |
| rate cards + `UsageRecord` + cost sources | `telemetry/metering` (issue #33) | the price authority and the metering row |
| camelCase gateway call record | `gateway/proxy` `GatewayCallRecord.to_dict()` (issue #16) | consumed structurally; never imported |
| tamper-evident audit chain | `telemetry/ledger` (issue #31) | one event per turn |
| prompt-cache discipline (`estimate_tokens`, `validate_static_region`, `footprint`) | `engine/memory/prompt_cache.py` (issue #25) | prefix accounting |
| ticket join node | ADR-0014 (issue #14/v2) | the turn's `ticket_id`, when it came from a ticket |

The chooser (`gateway/finops/chooser.py` over `tiers.yaml`) is the tier
authority. This lane does not import the gateway package — those modules are
plain scripts that require their own directory on `sys.path` — so the chooser
is **injected** where it is needed (`tiering.escalate_on_observed_failure`), and
the wrapped gate exercises the real chooser out-of-process.

## Contract, in five rules

1. **One attribution per turn.** Identity and the routing stamp
   (`tier`/`provider`/`model`), tokens and latency are *promoted* from the
   gateway call record; nothing is re-derived.
2. **The rate card prices, this lane does not.** The cost is
   `RateCardStore.estimate(...)` over the turn's *billable* (uncached) input.
   No rate card and no positive attached estimate ⇒ `cost_usd is None` with an
   `unmetered_reason` — never a fabricated `0.00`.
3. **Refused *and* metered** (AO-GR-18). A refused turn never reaches a
   provider, is recorded as the metering lane's non-billable event
   (`billable=False`, `metered=True`), and gets one ledger event naming the
   refusal code. Enforcement is not allowed to hide the spend it prevented.
4. **A client tier is never the authority.** A claim is validated against the
   ladder when one is supplied, recorded, and ignored
   (`tier_claim_honoured=False`). A turn with no routing stamp is *unresolved*
   — a claim may not fill the gap. Escalation requires counted, observed
   difficulty and moves one rung at a time through the chooser.
5. **A cache report must be possible.** `cached_tokens` may not exceed the
   cacheable prefix; a prefix carrying run identity (a session id, a timestamp)
   is accounted **uncacheable**, not credited with a hit.

## Public surface

- `model` — `ChatTurn`, `TurnAttribution` (one per turn).
- `attribution` — `TurnAttributor`: `attribute(turn, record)` and
  `attribute_refusal(turn, ...)`; `record_fields(record)` accepts a mapping or
  any object with `to_dict()`.
- `budget_guard` — `TurnBudgetGuard` (`check`) and `GuardedTurnRunner` (`run`),
  which consults the guard **before** it will call the injected provider;
  `TurnBudgetOutcome` carries the ladder decision, the refusal outcome and the
  rails it evaluated.
- `tiering` — `resolve_turn_tier`, `escalate_on_observed_failure`.
- `cache_accounting` — `account_prefix`, `CacheAccounting`.
- `readmodel` — `ChatFinOpsReadModel` with per-turn, per-conversation,
  per-tenant, per-agent and per-ticket rollups (the UX lane renders these
  numbers; it does not compute them).

## Usage

```python
from telemetry.chat import ChatTurn, ChatFinOpsReadModel, GuardedTurnRunner, TurnAttributor
from telemetry.ledger import DictKeystore, KeyMaterial, open_ledger
from telemetry.chat.budget_guard import TurnBudgetGuard

ledger = open_ledger("/var/lib/audit", keystore=keystore)
# Guard first, then attribute: the runner owns the ordering.
result = GuardedTurnRunner(TurnBudgetGuard.from_config(), TurnAttributor(ledger)).run(
    turn, provider, estimated_cost_usd=chooser_estimate
)
read_model.add(result.attribution, result.outcome)
```

## Offline CLI-free by design

Everything here is stdlib + PyYAML and runs offline. A turn's audit event
carries the turn identity as an **encrypted payload**, so the ledger's
per-tenant key requirement applies exactly as it does everywhere else: with no
key, the append fails closed and the failure is loud (the metering row is
written first, so a metered-but-unaudited turn is visible rather than silent).

## Verification

```bash
python3 -m pytest telemetry/chat -q -p no:cacheprovider   # the unit/contract suite
bash scripts/check-chat-finops.sh                        # the wrapped gate (0/1/2)
```

Wiring this gate into `scripts/verify.sh` and `scripts/pytest-suites.txt` is
owned by issue **#502** (the gate-of-record lane); this lane ships the check
and runs it directly.
