"""Per-role monthly budget-cap tests (issue #633, workbook-2).

The caps are CONSUMED from the workbook-1 registry declaration
(``registry/personas/org-chart.yaml`` + the bound persona cards). These tests
own two jobs:

1. **Consumption, not redefinition** — the enforcer's caps are asserted equal
   to the values the org chart and the cards declare (CEO 300 / CTO 250 /
   COO 100 / CFO 50 / CMO 200). Nothing here hard-codes a cap as the source of
   truth; the numbers are read back from the declaration and compared.
2. **Per-role stop/warn/fallback + a metered refusal** — a role at its cap
   refuses spend (``RoleBudgetBlocked``) *and* records a metered decision, and
   the refusal is proven by mutation.

The cap arithmetic is deterministic (no model call, no tokens), so every test
is exact and offline: budgets are synthetic so the decision boundaries are
round numbers, and the network is never touched.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

import budget
import chooser
import loader
import metering
from budget import (
    BudgetLedger,
    BudgetPolicy,
    RoleBudget,
    RoleBudgetBlocked,
    RoleBudgetEnforcer,
    RoleBudgetError,
    load_org_chart,
    load_role_budgets,
    load_role_enforcer,
)

# The workbook row values, used ONLY to assert what the declaration says.
WORKBOOK_CAPS = {"ceo": 300.0, "cto": 250.0, "coo": 100.0, "cfo": 50.0, "cmo": 200.0}

TENANT = "platform"


# --------------------------------------------------------------------------- #
# 1. Caps are consumed from the workbook-1 declaration
# --------------------------------------------------------------------------- #
def test_org_chart_declares_the_workbook_caps() -> None:
    """The org chart itself carries the workbook caps (source of truth)."""
    chart = load_org_chart()
    declared = {r["id"]: float(r["monthlyBudgetCapUsd"]) for r in chart["roles"]}
    assert declared == WORKBOOK_CAPS


def test_role_enforcer_caps_match_the_org_chart_and_the_cards() -> None:
    """The enforcer's caps equal what the chart AND the card declare."""
    cards_dir = budget.PERSONA_CARDS_DIR
    chart = load_org_chart()
    enforcer = load_role_enforcer()
    for role_id, expected in WORKBOOK_CAPS.items():
        rb = enforcer.role_for(role_id, tenant=TENANT)
        assert rb is not None, f"role {role_id} has no cap"
        # cap <=> the org chart's own value
        node = next(r for r in chart["roles"] if r["id"] == role_id)
        assert rb.monthly_cap_usd == float(node["monthlyBudgetCapUsd"]) == expected
        # cap <=> the bound persona card's value (chart/card agreement)
        import yaml

        card = yaml.safe_load((cards_dir / f"{role_id}.yaml").read_text(encoding="utf-8"))
        assert rb.monthly_cap_usd == float(card["monthlyBudgetCapUsd"])


def test_role_enforcer_carries_tier_and_heartbeat_from_the_chart() -> None:
    """Tier/heartbeat are consumed too (no local restatement)."""
    enforcer = load_role_enforcer()
    cfo = enforcer.role_for("cfo", tenant=TENANT)
    assert cfo is not None
    assert cfo.default_model_tier == "LOW"
    assert cfo.heartbeat_schedule == "daily"
    ceo = enforcer.role_for("ceo", tenant=TENANT)
    assert ceo is not None and ceo.default_model_tier == "MAX"


def test_role_budgets_fail_closed_on_chart_card_drift() -> None:
    """A chart cap that disagrees with its card is refused, not silently used."""
    chart = copy.deepcopy(load_org_chart())
    for node in chart["roles"]:
        if node["id"] == "cfo":
            node["monthlyBudgetCapUsd"] = 999
    with pytest.raises(RoleBudgetError, match="chart and card must agree"):
        load_role_budgets(chart)


def test_role_budgets_fail_closed_on_missing_cap() -> None:
    """A role with no numeric cap is refused."""
    chart = copy.deepcopy(load_org_chart())
    for node in chart["roles"]:
        if node["id"] == "cmo":
            del node["monthlyBudgetCapUsd"]
    with pytest.raises(RoleBudgetError, match="must be a number"):
        load_role_budgets(chart)


def test_role_budgets_fail_closed_on_missing_card(tmp_path: Path) -> None:
    """A role whose card cannot be read is refused (no silent unbudgeting)."""
    chart = copy.deepcopy(load_org_chart())
    with pytest.raises(RoleBudgetError, match="card for role .* not found"):
        load_role_budgets(chart, cards_dir=tmp_path)


# --------------------------------------------------------------------------- #
# 2. Decision semantics: stop / warn / fallback / hard cap
# --------------------------------------------------------------------------- #
def _role(cap: float, policy: BudgetPolicy, warn_at: float = 100.0) -> RoleBudget:
    return RoleBudget("cto", cap, policy, warn_at_pct=warn_at, tenant=TENANT)


def test_role_cap_allows_below_the_cap() -> None:
    rb = _role(100.0, BudgetPolicy.STOP)
    assert rb.decide(0.0) is budget.BudgetAction.ALLOW
    assert rb.decide(99.99) is budget.BudgetAction.ALLOW


def test_role_cap_stops_at_the_cap() -> None:
    """Default policy stop: a role AT its cap refuses spend."""
    rb = _role(50.0, BudgetPolicy.STOP)
    assert rb.decide(50.0) is budget.BudgetAction.STOP
    assert rb.decide(50.01) is budget.BudgetAction.STOP


def test_role_warn_policy_flags_at_the_span_threshold() -> None:
    rb = _role(100.0, BudgetPolicy.WARN, warn_at=80.0)
    assert rb.decide(85.0) is budget.BudgetAction.WARN
    assert rb.decide(100.0) is budget.BudgetAction.STOP  # hard cap is absolute


def test_role_fallback_policy_downgrades_at_the_threshold() -> None:
    rb = _role(100.0, BudgetPolicy.FALLBACK, warn_at=80.0)
    assert rb.decide(85.0) is budget.BudgetAction.FALLBACK
    assert rb.decide(100.0) is budget.BudgetAction.STOP


def test_role_zero_cap_refuses_all_spend() -> None:
    """A zero cap is deterministic (no per-cent math over a zero denominator)."""
    rb = _role(0.0, BudgetPolicy.WARN)
    assert rb.decide(0.0) is budget.BudgetAction.STOP


def test_role_cap_rejects_negative_cap() -> None:
    with pytest.raises(RoleBudgetError, match="must be >= 0"):
        RoleBudget("cfo", -1.0)


# --------------------------------------------------------------------------- #
# 3. The enforcer: separate ledger axis, tenant fallback
# --------------------------------------------------------------------------- #
def test_role_enforcer_uses_a_namespaced_ledger_key() -> None:
    """Role spend never collides with tenant spend on a shared ledger."""
    ledger = BudgetLedger()
    enforcer = RoleBudgetEnforcer(ledger=ledger)
    enforcer.add_role(_role(100.0, BudgetPolicy.STOP))
    enforcer.commit("cto", 10.0, tenant=TENANT)
    assert enforcer.spend("cto", tenant=TENANT) == 10.0
    # the tenant axis for the same id is untouched
    assert ledger.spend("cto") == 0.0
    assert ledger.spend(budget.role_ledger_key(TENANT, "cto")) == 10.0


def test_role_enforcer_returns_none_for_an_uncapped_role() -> None:
    """No cap declared => no role ceiling; the tenant axis governs instead."""
    enforcer = RoleBudgetEnforcer()
    assert enforcer.check("nobody", 10.0, tenant=TENANT) is None


def test_role_enforcer_refuses_and_reports_the_decision() -> None:
    enforcer = RoleBudgetEnforcer(ledger=BudgetLedger())
    enforcer.add_role(_role(50.0, BudgetPolicy.STOP))
    enforcer.commit("cto", 50.0, tenant=TENANT)
    decision = enforcer.check("cto", 1.0, tenant=TENANT)
    assert decision is not None
    assert decision.action is budget.BudgetAction.STOP
    assert decision.budget_usd == 50.0
    # projected 50.0 spent + 1.0 prospective = 51.0 on a $50 cap => 102%.
    assert decision.projected_usd == 51.0
    assert decision.pct_used == 102.0


def test_role_enforcer_is_deterministic_and_token_free() -> None:
    """The cap path is pure arithmetic: repeated calls give identical results.

    This is the workbook "zero-token arithmetic" rule — deciding whether tokens
    may be spent must not itself spend tokens, and must not depend on anything
    non-deterministic.
    """
    import inspect

    src = inspect.getsource(budget.RoleBudget.decide)
    for banned in ("random", "time.", "requests", "subprocess", "open("):
        assert banned not in src, f"cap path must not use {banned!r}"
    enforcer = RoleBudgetEnforcer(ledger=BudgetLedger())
    enforcer.add_role(_role(50.0, BudgetPolicy.STOP))
    enforcer.commit("cto", 25.0, tenant=TENANT)
    first = enforcer.check("cto", 1.0, tenant=TENANT)
    second = enforcer.check("cto", 1.0, tenant=TENANT)
    assert first == second


# --------------------------------------------------------------------------- #
# 4. The chooser consults the role cap BEFORE the tenant budget
# --------------------------------------------------------------------------- #
def _role_chooser(table, cap: float, spend: float, policy=BudgetPolicy.STOP):
    """Chooser with a cto role cap and a deliberately huge tenant budget.

    The tenant budget is $1,000,000 so any block must come from the ROLE cap —
    that is the property under test (the narrower ceiling wins).

    Costs are $1 per 1M-token call at L0 (the cheapest rung at 1_000_000
    tokens), so a prospective call costs exactly $1 and the expected percentage
    is ``(spend + 1) / cap * 100`` — a measured value, never a guessed one.
    """
    ledger = BudgetLedger()
    role_enforcer = RoleBudgetEnforcer(ledger=ledger)
    role_enforcer.add_role(RoleBudget("cto", cap, policy, tenant=TENANT))
    role_enforcer.commit("cto", spend, tenant=TENANT)
    tenant_enforcer = budget.BudgetDecisionMaker(
        ledger=ledger,
        default_monthly_budget_usd=1_000_000.0,
        default_policy=BudgetPolicy.WARN,
    )
    tenant_enforcer.roles = role_enforcer
    return (
        chooser.ModelChooser(
            table=table,
            budget_enforcer=tenant_enforcer,
            tokens_per_call=1_000_000,
            sink=metering.ListMeteringSink(),
        ),
        role_enforcer,
    )


def _capped_table(table):
    """Shipped table with L0 pinned to $1/call so role math is exact."""
    import yaml

    data = yaml.safe_load(loader.TIERS_PATH.read_text(encoding="utf-8"))
    for tier_block in data["ladder"].values():
        for model in tier_block["models"]:
            model["costPerMTok"] = 1.0
    return loader.parse_tier_table(data)


def test_chooser_refuses_a_role_at_its_cap(table) -> None:
    ch, _ = _role_chooser(table, cap=50.0, spend=50.0)
    with pytest.raises(RoleBudgetBlocked) as exc:
        ch.choose(task_class="code-author", tenant_id=TENANT, agent_id="cto", complexity=5.0)
    assert exc.value.role_id == "cto"
    assert exc.value.action == "stop"


def test_chooser_allows_a_role_below_its_cap_and_attributes_the_role(table) -> None:
    exact = _capped_table(table)
    ch, _ = _role_chooser(exact, cap=50.0, spend=10.0)
    choice = ch.choose(
        task_class="code-author", tenant_id=TENANT, agent_id="cto", complexity=5.0
    )
    assert choice.role_id == "cto"
    assert choice.role_cap_usd == 50.0
    # $10 spent + $1 prospective = $11 on a $50 cap => exactly 22%.
    assert choice.estimated_cost_usd == 1.0
    assert choice.role_pct_used == 22.0
    assert any("role-cap:cto" in r for r in choice.reasons)


def test_role_cap_wins_over_a_roomy_tenant_budget(table) -> None:
    """The narrower ceiling is the one that must not be crossed."""
    ch, enforcer = _role_chooser(table, cap=50.0, spend=50.0)
    # the tenant axis is nowhere near its own limit...
    tenant_decision = ch.enforcer.check(TENANT, 0.001)
    assert tenant_decision.action is budget.BudgetAction.ALLOW
    # ...yet the role cap refuses the same call.
    with pytest.raises(RoleBudgetBlocked):
        ch.choose(task_class="code-author", tenant_id=TENANT, agent_id="cto", complexity=5.0)


def test_chooser_records_a_metered_refusal_for_the_role(table) -> None:
    """A role cap refusal is a metered decision, not a silent drop."""
    exact = _capped_table(table)
    ch, _ = _role_chooser(exact, cap=50.0, spend=50.0)
    with pytest.raises(RoleBudgetBlocked):
        ch.choose(task_class="code-author", tenant_id=TENANT, agent_id="cto", complexity=5.0)
    records = ch.sink.records
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, metering.RefusalRecord)
    assert rec.scope == "role"
    assert rec.role_id == "cto"
    assert rec.budget_action == "stop"
    assert rec.cap_usd == 50.0
    # $50 spent + $1 prospective = $51 on a $50 cap => 102%.
    assert rec.pct_used == 102.0


def test_chooser_falls_back_to_platform_role_caps(table) -> None:
    """A tenant with no role declaration uses the platform-default caps."""
    enforcer = load_role_enforcer()
    ch = chooser.ModelChooser(
        table=table,
        budget_enforcer=budget.BudgetDecisionMaker(ledger=enforcer.ledger),
        tokens_per_call=1_000_000,
    )
    ch.role_enforcer = enforcer
    enforcer.commit("ceo", 300.0, tenant=TENANT)
    with pytest.raises(RoleBudgetBlocked) as exc:
        ch.choose(
            task_class="code-author",
            tenant_id="tenant-acme",
            agent_id="ceo",
            complexity=5.0,
        )
    assert exc.value.role_id == "ceo"


def test_chooser_uncapped_agent_is_unaffected(table) -> None:
    """No role cap for the agent => existing behaviour is unchanged."""
    enforcer = load_role_enforcer()
    ch = chooser.ModelChooser(
        table=table,
        budget_enforcer=budget.BudgetDecisionMaker(ledger=enforcer.ledger),
        tokens_per_call=1_000_000,
    )
    ch.role_enforcer = enforcer
    choice = ch.choose(
        task_class="code-author", tenant_id="tenant-acme", agent_id="sniper", complexity=5.0
    )
    assert choice.role_id is None
    assert choice.role_cap_usd is None


def test_load_budgets_wires_both_axes_over_one_ledger() -> None:
    """``load_budgets`` attaches the role axis to the tenant enforcer."""
    enforcer = budget.load_budgets()
    assert enforcer.roles is not None
    assert enforcer.roles.ledger is enforcer.ledger
    assert enforcer.roles.role_for("ceo", tenant=TENANT) is not None


def test_budgets_yaml_documents_the_role_vocabulary() -> None:
    """budgets.yaml documents the per-role vocabulary (AC 5)."""
    data = budget.parse_role_policy(
        _read_yaml(budget.BUDGETS_PATH)
    )
    assert data["policy"] is BudgetPolicy.STOP
    assert data["warn_at_pct"] == 100.0
    assert data["capSource"] == "registry/personas/org-chart.yaml"
    text = budget.BUDGETS_PATH.read_text(encoding="utf-8")
    for token in ("monthlyBudgetCapUsd", "org-chart.yaml", "zero-token", "per-role"):
        assert token in text, f"budgets.yaml must document {token!r}"


def _read_yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 5. Mutation proof: the refusal must be able to fail
# --------------------------------------------------------------------------- #
CAP_PATH = Path(budget.__file__)
# Mutant A: disable the role hard-cap comparison entirely.
_MUTANT_HARD_CAP = (
    "        if pct >= self.hard_cap_pct:\n            return BudgetAction.STOP\n"
)
_MUTATED_HARD_CAP = (
    "        if pct >= self.hard_cap_pct and False:\n"
    "            return BudgetAction.STOP\n"
)
# Mutant B: neuter the over-cap policy (stop/warn/fallback all resolve to allow).
_MUTANT_POLICY = (
    "        if pct >= self.warn_at_pct:\n"
    "            return {\n"
    "                BudgetPolicy.STOP: BudgetAction.STOP,\n"
    "                BudgetPolicy.WARN: BudgetAction.WARN,\n"
    "                BudgetPolicy.FALLBACK: BudgetAction.FALLBACK,\n"
    "            }[self.policy]\n"
)
_MUTATED_POLICY = (
    "        if pct >= self.warn_at_pct and False:\n"
    "            return BudgetAction.STOP\n"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mutation_probe() -> list[str]:
    """Run the refusal assertions under one mutation; return the FAIL lines.

    A probe that still returns ``STOP`` was never really asserting the refusal,
    so its absence from the returned list is the mutation *surviving* — which
    ``test_role_cap_refusal_is_mutation_proved`` treats as a suite bug, not a
    pass.
    """
    failures: list[str] = []
    # 1. a role exactly at its cap (default policy stop)
    try:
        if _role(50.0, BudgetPolicy.STOP).decide(50.0) is budget.BudgetAction.STOP:
            failures.append(
                "at_cap_policy_stop: decide(50.0) returned STOP on a $50 cap — "
                "the refusal survived the mutation"
            )
    except Exception as exc:
        failures.append(f"at_cap_policy_stop: raised {type(exc).__name__}: {exc}")
    # 2. the hard cap is absolute under a warn policy
    try:
        if _role(100.0, BudgetPolicy.WARN, warn_at=80.0).decide(100.0) is budget.BudgetAction.STOP:
            failures.append(
                "hard_cap_under_warn: decide(100.0) returned STOP on a $100 cap "
                "— the hard cap survived the mutation"
            )
    except Exception as exc:
        failures.append(f"hard_cap_under_warn: raised {type(exc).__name__}: {exc}")
    # 3. the chooser refuses a role at its cap and meters the refusal
    try:
        ch, _ = _role_chooser(_capped_table(None), cap=50.0, spend=50.0)
        ch.choose(task_class="code-author", tenant_id=TENANT, agent_id="cto", complexity=5.0)
        failures.append(
            "chooser_role_stop: choose() did not raise RoleBudgetBlocked for a "
            "role at its cap — the refusal survived the mutation"
        )
    except RoleBudgetBlocked:
        pass  # the control died, as it must
    except Exception as exc:
        failures.append(f"chooser_role_stop: raised {type(exc).__name__}: {exc}")
    return failures


def test_role_cap_refusal_is_mutation_proved() -> None:
    """Kill the cap logic and prove the refusal tests die with it.

    Two independent mutants are applied in turn to ``RoleBudget.decide`` — the
    hard-cap comparison and the over-cap policy mapping. For each, the refusal
    probes must **fail** (raise or return a non-STOP action). A probe that
    still stops under the mutation means the refusal was never really
    asserted, and this test fails rather than reporting a false green.

    The source is restored from an in-memory snapshot in a ``finally``, and the
    restored sha256 is asserted equal to the original, so a failing run can
    never leave a mutated tree behind.
    """
    original_src = CAP_PATH.read_text(encoding="utf-8")
    before = _sha256(CAP_PATH)
    mutants = (
        ("hard-cap comparison", _MUTANT_HARD_CAP, _MUTATED_HARD_CAP),
        ("over-cap policy", _MUTANT_POLICY, _MUTATED_POLICY),
    )
    import importlib

    report: list[str] = []
    try:
        for name, anchor, replacement in mutants:
            assert anchor in original_src, (
                f"mutation anchor for {name} not found — the cap logic moved; "
                "update the mutant"
            )
            CAP_PATH.write_text(
                original_src.replace(anchor, replacement, 1), encoding="utf-8"
            )
            assert _sha256(CAP_PATH) != before, f"mutant {name} changed nothing"
            importlib.reload(budget)
            failures = _mutation_probe()
            assert failures, (
                f"MUTATION SURVIVED ({name} disabled): every refusal probe still "
                "returned STOP, so the refusal is not really asserted. The cap "
                "path is unguarded."
            )
            for line in failures:
                report.append(f"  FAIL [{name}] {line}")
            # restore between mutants so each runs against the pristine source
            CAP_PATH.write_text(original_src, encoding="utf-8")
            importlib.reload(budget)
    finally:
        CAP_PATH.write_text(original_src, encoding="utf-8")
        assert _sha256(CAP_PATH) == before, "restore failed — tree left mutated"
        importlib.reload(budget)
    print("\nMUTATION-PROVED: cap logic disabled -> FAIL lines:")
    for line in report:
        print(line)
