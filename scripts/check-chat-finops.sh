#!/usr/bin/env bash
# check-chat-finops.sh — chat per-turn FinOps enforcement (issue #506).
#
# The chat turn is the unit of chat FinOps, and the control that matters is the
# refusal: a turn over budget (or under a paused kill switch / an exhausted
# quota) must NOT reach a provider, and must still be *metered* as a
# non-billable event so the spend that was prevented is visible.  This gate
# proves the control BITES rather than merely existing:
#
#   * a funded turn is allowed and reaches the provider exactly once, is billed
#     and audited (the accept control — a guard that refuses everything fails
#     here);
#   * a paused tenant, an over-cap tenant and a quota-exhausted tenant are each
#     REFUSED with the provider call count asserted at zero, one metering row
#     (non-billable, still metered) and exactly one ledger event;
#   * an impossible prompt-cache report (more cached tokens than the prompt's
#     cacheable prefix) is refused;
#   * a REAL gateway/proxy ``GatewayCallRecord`` object (built out-of-process
#     with ``gateway/`` on sys.path) is consumed by the attributor's parser, so
#     the two sides of the record contract cannot drift apart silently;
#   * a turn dated in the PAST is judged in ITS OWN day/month.  This is the
#     #506 date bomb: a rail given no bucket resolves it from the *live* clock,
#     so a fixture seeded on the turn's own day used to miss, an exhausted
#     tenant was silently ALLOWED, and the acceptance proof expired with the
#     calendar.  C1 refuses the past-dated turn against the rail exhausted in
#     that same bucket; C3 serves the same turn against a FRESH rail there, so
#     C1 cannot be satisfied by a guard that refuses everything.  Every instant
#     is a literal pinned in the probe — this control never reads the clock;
#   * and the refusals are provably sensitive: the refusal branch AND the
#     turn-date derivation in ``budget_guard.py`` are each mutated in a scratch
#     copy of the tree, and the gate FAILS, BY NAME, if the mutated guard is
#     not caught.  A control that cannot fail is a formality (GR-12 / AO-GR-4 /
#     AO-GR-19).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-chat-finops.sh
#
# ---knowledge---
# module_id: scripts.check-chat-finops
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, deterministic]
# derives_from: null
# owner_sme: cfo
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#506"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

# Never let a bytecode cache survive a tree copy (a stale ``__pycache__`` can
# shadow the mutated source and fake a passing control).
export PYTHONDONTWRITEBYTECODE=1

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-chat-finops: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

# ── the surface this lane owns ─────────────────────────────────────────────
for required in \
  telemetry/chat/__init__.py \
  telemetry/chat/README.md \
  telemetry/chat/model.py \
  telemetry/chat/attribution.py \
  telemetry/chat/budget_guard.py \
  telemetry/chat/tiering.py \
  telemetry/chat/cache_accounting.py \
  telemetry/chat/readmodel.py \
  telemetry/chat/tests/conftest.py \
  telemetry/budgets/config/killswitch.yaml \
  telemetry/metering/rate_cards/deepseek.yaml
do
  if [ -f "$required" ]; then
    echo "  OK    $required present"
  else
    echo "  FAIL  $required is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-chat-finops: FAIL ($fail missing file(s))" >&2
  exit 1
fi

work="/tmp/ao506-chatfinops.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

cat > "$work/probe.py" <<'PROBE'
"""Provoke one chat-turn outcome and report whether it held (issue #506).

Usage: python3 probe.py <import-root> <mode>

Exit 0 = the expectation held.  Exit 1 = it did not, with the reasons printed.
The import root is a directory holding ``telemetry/`` (the real tree, or a
scratch mutant copy), so nothing here reads the working tree implicitly.
"""

import hashlib
import os
import sys

root, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)

TENANT = "acme"
AGENT = "chat-agent-1"
CONVERSATION = "conv-1"
TICKET = "kushin77/agent-orchestrator#506"
TS = "2026-09-14T09:00:00Z"
STATIC_PREFIX = (
    "You are the tenant's support agent for the platform.\n"
    "Answer strictly from the tenant's knowledge base.\n"
    "Cite the memory entry key for every factual claim you make."
)
USER_DELTA = "How do I rotate an API key for my organisation?"
# The turn's OWN bucket, taken from the literal above and never from the clock:
# the guard evaluates a turn in its own window, so a rail seeded on "today" is
# the #506 date bomb itself — it matches only while the gate happens to run on
# the day this fixture names.
TURN_DAY = TS[:10]
TURN_MONTH = TS[:7]
# A day the fixture names nothing at all in: the fresh-rail half (C3) is seeded
# here so "no spend" cannot be confused with "spend on another day".
OTHER_DAY = "2026-08-01"


def make_key(seed: str) -> bytes:
    return hashlib.sha256(("check-chat-finops:" + seed).encode("utf-8")).digest()


def build(prefix: str = STATIC_PREFIX):
    """The deterministic turn plus real sinks (ledger + metering store)."""
    from telemetry.chat.attribution import TurnAttributor
    from telemetry.chat.cache_accounting import account_prefix
    from telemetry.chat.model import ChatTurn
    from telemetry.ledger import DictKeystore, KeyMaterial, open_ledger
    from telemetry.metering.store import MemoryUsageStore

    shape = account_prefix(prefix, USER_DELTA)
    turn = ChatTurn(
        turn_id="turn-1",
        conversation_id=CONVERSATION,
        tenant_id=TENANT,
        agent_id=AGENT,
        ts=TS,
        ticket_id=TICKET,
        static_prefix=prefix,
        user_delta=USER_DELTA,
        cached_tokens=max(1, shape.static_tokens - 1),
    )
    ledger = open_ledger(
        None,
        keystore=DictKeystore(
            {TENANT: KeyMaterial(key=make_key(TENANT), key_id="check:k1")}
        ),
    )
    usage = MemoryUsageStore()
    return turn, TurnAttributor(ledger, usage_store=usage), usage, ledger


def gateway_record(turn_id: str = "turn-1") -> dict:
    return {
        "requestId": turn_id,
        "ts": TS,
        "tenantId": TENANT,
        "agentId": AGENT,
        "taskType": "chat.turn",
        "taskClass": "chat-support",
        "tier": "L0",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "outcome": "success",
        "inputTokens": 12,
        "outputTokens": 200,
        "tokens": 212,
        "latencyMs": 410.0,
        "estimatedCostUsd": 0.0,
        "budgetAction": "allow",
        "attempts": 1,
        "error": None,
    }


class Spy:
    """A provider that counts its invocations (the refusal assertion)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, turn):
        self.calls += 1
        return gateway_record(turn.turn_id)


def quota_rail(day: str, calls: int):
    """The daily request rail (soft 10, hard 20) with ``calls`` spent in ``day``."""
    from telemetry.budgets.ledger import StaticLedger
    from telemetry.budgets.model import RESOURCE_REQUESTS, WINDOW_DAY
    from telemetry.budgets.quota import (
        QuotaEnforcer,
        QuotaLimit,
        QuotaPolicy,
        StaticProbe,
    )

    policy = QuotaPolicy(
        tenant_id=TENANT,
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
    ledger = StaticLedger(calls={(TENANT, day): calls})
    return QuotaEnforcer(ledger, {TENANT: policy}, probe=StaticProbe())


def guard_for(rail: str):
    """The shipped rails, seeded so that ``rail`` refuses the next turn.

    Every seed lands in the TURN'S OWN bucket (``TURN_DAY`` / ``TURN_MONTH``,
    taken from the literal turn timestamp): the guard judges a turn in its own
    window, so a rail seeded on "today" would be the #506 date bomb again — it
    would match only while the gate happened to run on the day it names.
    """
    from telemetry.chat.budget_guard import TurnBudgetGuard

    if rail == "killswitch":
        from telemetry.budgets.killswitch import (
            KillSwitchController,
            KillSwitchState,
        )

        return TurnBudgetGuard(
            killswitch=KillSwitchController(
                initial=KillSwitchState(
                    global_pause=True, reason="incident-1", paused_by="ops"
                )
            )
        )
    if rail == "budget":
        from telemetry.budgets.budget import BudgetEnforcer, load_budget_policies
        from telemetry.budgets.ledger import StaticLedger

        # acme's shipped policy is enforce with a 120 USD monthly cap.
        ledger = StaticLedger(costs={(TENANT, TURN_MONTH): 120.0})
        return TurnBudgetGuard(budget=BudgetEnforcer(ledger, load_budget_policies()))
    return TurnBudgetGuard(quota=quota_rail(TURN_DAY, 20))


def check_refusal(rail: str):
    """A refused turn: no provider call, still metered, exactly one audit event."""
    from telemetry.chat.budget_guard import GuardedTurnRunner

    turn, attributor, usage, ledger = build()
    spy = Spy()
    result = GuardedTurnRunner(guard_for(rail), attributor).run(
        turn, spy, estimated_cost_usd=0.01
    )
    problems = []
    if result.allowed:
        problems.append("the turn was ALLOWED despite the refusal rail")
    if spy.calls != 0:
        problems.append(f"the provider was called {spy.calls} time(s)")
    if usage.count() != 1:
        problems.append(f"{usage.count()} metering record(s), expected 1")
    else:
        row = usage.read()[0]
        if not row.metered:
            problems.append("the refused turn was NOT metered (AO-GR-18)")
        if row.billable:
            problems.append("the refused turn was billed")
        if row.outcome not in ("refused", "blocked", "budget_exceeded"):
            problems.append(f"metering outcome {row.outcome!r} is not a refusal")
    records = ledger.records(TENANT)
    if len(records) != 1:
        problems.append(f"{len(records)} ledger event(s), expected 1")
    elif records[0].get("action") != "chat.turn.refused":
        problems.append(f"ledger action {records[0].get('action')!r}, expected chat.turn.refused")
    return problems


def check_allow():
    """The accept control: a funded turn is served, billed and audited once."""
    from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard

    turn, attributor, usage, ledger = build()
    spy = Spy()
    result = GuardedTurnRunner(TurnBudgetGuard(), attributor).run(
        turn, spy, estimated_cost_usd=0.01
    )
    problems = []
    if not result.allowed:
        problems.append("a funded turn was refused (the guard refuses everything)")
    if spy.calls != 1:
        problems.append(f"the provider was called {spy.calls} time(s), expected 1")
    if usage.count() != 1:
        problems.append(f"{usage.count()} metering record(s), expected 1")
    else:
        row = usage.read()[0]
        if not row.billable:
            problems.append("a served turn was not billed")
        if row.cost_usd is None:
            problems.append("a served turn was not priced from its rate card")
    records = ledger.records(TENANT)
    if len(records) != 1:
        problems.append(f"{len(records)} ledger event(s), expected 1")
    elif records[0].get("action") != "chat.turn":
        problems.append(f"ledger action {records[0].get('action')!r}, expected chat.turn")
    if result.attribution.cost_usd is None:
        problems.append("the attribution carries no cost for a served turn")
    return problems


def check_cache_impossible():
    """An impossible cache report (more cached tokens than the prefix) is refused."""
    from telemetry.chat.cache_accounting import (
        CacheAccounting,
        CacheAccountingError,
        account_prefix,
    )

    shape = account_prefix(STATIC_PREFIX, USER_DELTA)
    try:
        CacheAccounting(prefix=shape, cached_tokens=shape.static_tokens + 1)
    except CacheAccountingError:
        return []
    return [
        "an impossible cache report was ACCEPTED "
        f"(cached {shape.static_tokens + 1} > cacheable {shape.static_tokens})"
    ]


def check_real_record():
    """A real gateway/proxy ``GatewayCallRecord`` is consumed by the parser."""
    sys.path.insert(0, os.path.join(root, "gateway"))
    from proxy.model import GatewayCallRecord

    turn, attributor, usage, _ledger = build()
    record = GatewayCallRecord(
        request_id="turn-1",
        ts=TS,
        tenant_id=TENANT,
        agent_id=AGENT,
        task_type="chat.turn",
        outcome="success",
        task_class="chat-support",
        tier="L0",
        provider="deepseek",
        model="deepseek-chat",
        input_tokens=12,
        output_tokens=200,
        latency_ms=410.0,
        estimated_cost_usd=0.0,
        budget_action="allow",
        attempts=1,
    )
    attribution = attributor.attribute(turn, record)  # the OBJECT, not a dict
    problems = []
    expected = {
        "tenantId": (attribution.tenant_id, TENANT),
        "agentId": (attribution.agent_id, AGENT),
        "conversationId": (attribution.conversation_id, CONVERSATION),
        "ticketId": (attribution.ticket_id, TICKET),
        "tier": (attribution.tier, "L0"),
        "provider": (attribution.provider, "deepseek"),
        "model": (attribution.model, "deepseek-chat"),
    }
    for label, (actual, want) in expected.items():
        if actual != want:
            problems.append(f"{label}: {actual!r}, expected {want!r}")
    if attribution.cost_usd is None:
        problems.append("the real record's zero estimate masked the rate card")
    if usage.count() != 1:
        problems.append(f"{usage.count()} metering record(s), expected 1")
    return problems


def check_turn_date_scope():
    """A past-dated turn is judged in ITS OWN bucket — C1 refuses, C3 serves.

    C1: the turn (dated ``TS``) against the rail exhausted in *its own* day is
        refused, and never reaches a provider.
    C3: the same turn against a rail exhausted in a DIFFERENT day is served —
        so a guard that refused everything would fail here instead of passing
        C1, and "exhausted elsewhere" is shown not to refuse this turn.

    Every instant is a literal pinned at the top of this probe (the turn's own
    timestamp and two day buckets); nothing here reads the clock, so the control
    cannot rot into the next #506.  Under the pre-fix guard the bucket came from
    the live clock, so C1's seed misses on every day the clock is not on
    ``TURN_DAY`` and the refusal is reported BY NAME.
    """
    from telemetry.chat.budget_guard import GuardedTurnRunner, TurnBudgetGuard

    exhausted_spy = Spy()
    other_day_spy = Spy()
    refused_turn, refused_attributor, _u1, _l1 = build()
    served_turn, served_attributor, _u2, _l2 = build()

    refused = GuardedTurnRunner(
        TurnBudgetGuard(quota=quota_rail(TURN_DAY, 20)), refused_attributor
    ).run(refused_turn, exhausted_spy, estimated_cost_usd=0.01)
    served = GuardedTurnRunner(
        TurnBudgetGuard(quota=quota_rail(OTHER_DAY, 20)), served_attributor
    ).run(served_turn, other_day_spy, estimated_cost_usd=0.01)

    problems = []
    if refused.allowed:
        problems.append(
            f"a turn dated {TS} was ALLOWED against a rail exhausted in its own "
            f"bucket {TURN_DAY}: the evaluation bucket is not the turn's own "
            f"(#506), so a refusal rail can never bite on a past-dated turn"
        )
    elif (refused.outcome.day, refused.outcome.month) != (TURN_DAY, TURN_MONTH):
        problems.append(
            "the refusal was judged in "
            f"{(refused.outcome.day, refused.outcome.month)}, not the turn's own "
            f"{TURN_DAY}/{TURN_MONTH}"
        )
    if exhausted_spy.calls != 0:
        problems.append(
            f"the provider was called {exhausted_spy.calls} time(s) for a refused turn"
        )
    if not served.allowed:
        problems.append(
            f"a turn dated {TS} was refused because {OTHER_DAY} is exhausted — the "
            f"guard is refusing on a day that is not the turn's own"
        )
    if other_day_spy.calls != 1:
        problems.append(
            f"the served turn reached the provider {other_day_spy.calls} time(s), "
            f"expected 1"
        )
    return problems


CHECKS = {
    "allow": check_allow,
    "killswitch": lambda: check_refusal("killswitch"),
    "budget": lambda: check_refusal("budget"),
    "quota": lambda: check_refusal("quota"),
    "cache-impossible": check_cache_impossible,
    "real-record": check_real_record,
    "turn-date-scope": check_turn_date_scope,
}

problems = CHECKS[mode]()
if problems:
    for problem in problems:
        print(f"  {problem}")
    raise SystemExit(1)
print(f"  held: {mode}")
PROBE

expect_probe() { # expect_probe <mode> <description>
  local mode="$1" desc="$2" out rc
  out="$(python3 "$work/probe.py" "$root" "$mode" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  OK    $desc"
  else
    echo "  FAIL  $desc (rc=$rc)" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
}

stage_mutant() { # stage_mutant <dir> -- a self-contained copy the probe can import
  local dir="$1"
  mkdir "$dir" || exit 2
  # The mutant root must be self-contained: every package the probe imports is
  # copied, so the mutant run can never "fail" merely because an import was
  # missing (which would look exactly like a caught mutation).
  cp -R "$root/telemetry" "$dir/telemetry"
  cp -R "$root/engine" "$dir/engine"
  find "$dir" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
}

mutate() { # mutate <file> <old-text> <new-text> -- a no-op is never a mutation
  python3 - "$1" "$2" "$3" <<'PY'
"""Apply one textual mutation to a scratch copy, or refuse to pretend.

A mutation that never landed, or that changed nothing, makes the control that
consumes it vacuous -- so both are refused here rather than reported as a
passing provocation (AO-GR-4).
"""

import sys

path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
seen = text.count(old)
if seen != 1:
    print(
        f"MUTATION-UNREACHABLE: the anchor appears {seen} time(s), not exactly once",
        file=sys.stderr,
    )
    raise SystemExit(2)
mutated = text.replace(old, new, 1)
if mutated == text:
    print("MUTATION-NOOP: the mutation changed nothing", file=sys.stderr)
    raise SystemExit(2)
open(path, "w", encoding="utf-8").write(mutated)
print("mutation applied to a scratch copy")
PY
}

expect_mutant_caught() { # expect_mutant_caught <label> <dir> <mode> <needle> <message>
  local label="$1" dir="$2" mode="$3" needle="$4" message="$5" out rc
  out="$(python3 "$work/probe.py" "$dir" "$mode" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  FAIL  $label: the mutated guard still passed — the control is insensitive" >&2
    fail=$((fail + 1))
    return 0
  fi
  # Bash-native containment, never a pipe into a quiet `grep`: a pipe would let
  # `grep -q` exit on its first match and kill the producer mid-write, so a large
  # report can read as ABSENT and the check would fail OPEN (check-verdict-contains).
  case "$out" in
    *"$needle"*)
      echo "  OK    $label: $message"
      printf '%s\n' "$out" | sed 's/^/        /'
      ;;
    *)
      # A crash is NOT a caught mutation: the mutated guard must have actually
      # run and been observed failing for the reason the control names.
      echo "  FAIL  $label: the mutated guard was never exercised (the mutant did not run, or it failed for another reason)" >&2
      printf '%s\n' "$out" | sed 's/^/        /' >&2
      fail=$((fail + 1))
      ;;
  esac
}

echo "== accept control =="
expect_probe allow "a funded turn is served once, billed and audited once"

echo "== refused paths (refused AND still metered) =="
expect_probe killswitch "a paused tenant is refused before any provider call"
expect_probe budget "an over-cap tenant is blocked and still metered"
expect_probe quota "a quota-exhausted tenant is refused and still metered"

echo "== accounting + contract controls =="
expect_probe cache-impossible "an impossible prompt-cache report is refused"
expect_probe real-record "a real GatewayCallRecord is consumed by the attribution"

echo "== the turn's own bucket (the #506 date bomb) =="
expect_probe turn-date-scope "a past-dated turn is refused in its own bucket (C1) and served there against another day's rail (C3)"

echo "== mutation controls (a control that cannot fail is a formality) =="

# (1) the refusal branch — the regression every refusal control above must catch.
refusal_mutant="$work/mutant-refusal"
stage_mutant "$refusal_mutant"
mutate_rc=0
mutate "$refusal_mutant/telemetry/chat/budget_guard.py" \
  "        if not outcome.allowed:" \
  "        if False and not outcome.allowed:" || mutate_rc=$?
if [ "$mutate_rc" -ne 0 ]; then
  echo "  FAIL  the refusal mutation could not be applied (rc=$mutate_rc) — the control cannot be proven" >&2
  fail=$((fail + 1))
else
  expect_mutant_caught "a guard with no refusal branch" "$refusal_mutant" killswitch \
    "the provider was called" "the refusal controls caught it letting the turn through"
fi

# (2) the turn-date derivation — the regression the date-scope control must catch.
# The anchor is the whole derivation as ONE block, so the mutant's guard really
# does judge against the clock rather than merely holding unreachable lines.
derivation_anchor='        if day is None:
            day = day_bucket(turn_ts)
        if month is None:
            month = month_bucket(turn_ts)
'
date_mutant="$work/mutant-date"
stage_mutant "$date_mutant"
mutate_rc=0
mutate "$date_mutant/telemetry/chat/budget_guard.py" "$derivation_anchor" "" || mutate_rc=$?
if [ "$mutate_rc" -ne 0 ]; then
  echo "  FAIL  the date mutation could not be applied (rc=$mutate_rc) — the control cannot be proven" >&2
  fail=$((fail + 1))
elif grep -q "day_bucket(turn_ts)" "$date_mutant/telemetry/chat/budget_guard.py"; then
  echo "  FAIL  the date mutant still derives the turn's bucket — the mutation did not land" >&2
  fail=$((fail + 1))
else
  echo "  OK    the date mutant's guard no longer derives the turn's own bucket"
  expect_mutant_caught "a guard that judges against the live clock" "$date_mutant" turn-date-scope \
    "the evaluation bucket is not the turn's own" "the past-dated turn was ALLOWED past an exhausted rail, as the control requires"
fi

echo
if [ "$fail" -gt 0 ]; then
  echo "check-chat-finops: FAIL ($fail check(s) failed)" >&2
  exit 1
fi
echo "check-chat-finops: OK — per-turn attribution, budget caps, the turn's own bucket and cache accounting hold"
exit 0
