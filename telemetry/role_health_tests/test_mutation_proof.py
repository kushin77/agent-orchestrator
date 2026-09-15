"""telemetry/role_health — the staleness and burn alert checks must be able to fail (#637).

A check that cannot fail is a formality.  This file kills each check at the
source and proves the assertions that depend on it die with it, reporting the
real FAIL line — the same mutation-proof pattern the sibling lane uses
(``gateway/finops/tests/test_role_budget.py``).

Two independent mutants are applied in turn:

* ``CADENCE_SLACK_FACTOR`` -> ``-1.0`` — the staleness comparison can never be
  satisfied, so every heartbeat assertion that expects an ``ok`` verdict must
  fail (the zero-retention form of the rule: no beat is ever recent enough);
* ``RoleBudgetBurnReport.alerts`` neutered to return ``[]`` — the ``warnAtPct``
  alert path disappears, so every burn-alert assertion must fail.

The source is restored from an in-memory snapshot in a ``finally``, and the
restored sha256 is asserted equal to the original, so a failing run can never
leave a mutated tree behind.
"""

from __future__ import annotations

import importlib
import hashlib
import sys
from pathlib import Path

import pytest

from conftest import MONTH, call_record

MODULE_PATH = Path(
    importlib.import_module("telemetry.role_health").__file__
).resolve()

# Mutant A: negate the slack factor, so no age can ever be within the cadence.
_ANCHOR_SLACK = "CADENCE_SLACK_FACTOR = 1.0"
_MUTATED_SLACK = "CADENCE_SLACK_FACTOR = -1.0"

# Mutant B: drop the burn alert path entirely.
_ANCHOR_ALERTS = """    def alerts(self) -> List[DeferredAlert]:
        \"\"\"Every burn alert (only the roles at/over a threshold produce one).\"\"\"
        return [a for a in (row.alert() for row in self.rows()) if a is not None]"""
_MUTATED_ALERTS = """    def alerts(self) -> List[DeferredAlert]:
        \"\"\"MUTANT: the warnAtPct alert path removed (no alert can ever fire).\"\"\"
        return []"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_module_is_not_currently_mutated() -> None:
    """Guard: the pristine source is the anchor both mutants replace."""
    text = MODULE_PATH.read_text(encoding="utf-8")
    assert _ANCHOR_SLACK in text
    assert _ANCHOR_ALERTS in text


def _staleness_probes() -> list[str]:
    """Assertions that must die when the cadence slack is negated."""
    from telemetry.role_health import (
        CADENCE_SECONDS,
        STATUS_OK,
        Heartbeat,
        HeartbeatStatus,
    )

    failures: list[str] = []
    now = 1_800_000_000.0
    for schedule, cadence in sorted(CADENCE_SECONDS.items()):
        # a beat one second inside its own cadence must be ok
        try:
            status = HeartbeatStatus.evaluate(
                "ceo",
                schedule,
                Heartbeat.at("ceo", now - (cadence - 1.0)),
                now_epoch=now,
            )
        except Exception as exc:  # pragma: no cover - defensive
            failures.append(f"{schedule}: raised {type(exc).__name__}: {exc}")
            continue
        if status.status != STATUS_OK:
            failures.append(
                f"{schedule}: a beat {1.0}s inside the cadence read "
                f"{status.status!r} — the staleness comparison is unguarded"
            )
    return failures


def _burn_probes() -> list[str]:
    """Assertions that must die when the burn alert path is removed."""
    from telemetry.role_health import (
        CODE_BURN_BREACH,
        RoleBudgetBurnReport,
        load_role_caps,
    )

    failures: list[str] = []
    # A role exactly at its declared cap must raise exactly one breach alert.
    class _Agg:
        calls = 1
        cache_hits = 0
        input_tokens = 0
        output_tokens = 0
        unmetered_calls = 0

        def __init__(self, cost: float) -> None:
            self.cost_usd = cost

    class _Reporter:
        def __init__(self, costs: dict[str, float]) -> None:
            self._costs = costs

        def rollup(self, *, window, dimensions, start=None, end=None, tenant_id=None):
            return {
                (start or MONTH, role): _Agg(cost)
                for role, cost in self._costs.items()
            }

    caps = load_role_caps()
    reporter = _Reporter({"cfo": caps.cap_usd("cfo")})
    report = RoleBudgetBurnReport(reporter, caps, month=MONTH)
    try:
        alerts = report.alerts()
    except Exception as exc:  # pragma: no cover - defensive
        failures.append(f"alerts() raised {type(exc).__name__}: {exc}")
        return failures
    if not alerts:
        failures.append(
            "at_cap: a role exactly at its cap produced no burn alert — the "
            "warnAtPct/cap alert path is unguarded"
        )
    else:
        if len(alerts) != 1:
            failures.append(f"at_cap: expected 1 alert, got {len(alerts)}")
        elif alerts[0].code != CODE_BURN_BREACH:
            failures.append(
                f"at_cap: expected {CODE_BURN_BREACH!r}, got {alerts[0].code!r}"
            )
    return failures


def test_staleness_and_burn_checks_are_mutation_proved(capsys) -> None:
    """Disable each check at the source; prove the assertions die with it."""
    original = MODULE_PATH.read_text(encoding="utf-8")
    before = _sha256(MODULE_PATH)
    mutants = (
        ("cadence slack", _ANCHOR_SLACK, _MUTATED_SLACK, _staleness_probes),
        ("burn alert path", _ANCHOR_ALERTS, _MUTATED_ALERTS, _burn_probes),
    )
    report: list[str] = []
    try:
        for name, anchor, replacement, probe in mutants:
            assert anchor in original, (
                f"mutation anchor for {name} not found — the logic moved; "
                "update the mutant"
            )
            MODULE_PATH.write_text(
                original.replace(anchor, replacement, 1), encoding="utf-8"
            )
            mutated_sha = _sha256(MODULE_PATH)
            assert mutated_sha != before, f"mutant {name} changed nothing"
            for mod in list(sys.modules):
                if mod.startswith("telemetry.role_health"):
                    del sys.modules[mod]
            importlib.invalidate_caches()
            failures = probe()
            assert failures, (
                f"MUTATION SURVIVED ({name} disabled): every probe still passed, "
                "so the check is not really asserted."
            )
            for line in failures:
                report.append(
                    f"  FAIL [{name}] sha256={mutated_sha[:16]} {line}"
                )
            MODULE_PATH.write_text(original, encoding="utf-8")
            for mod in list(sys.modules):
                if mod.startswith("telemetry.role_health"):
                    del sys.modules[mod]
            importlib.invalidate_caches()
    finally:
        MODULE_PATH.write_text(original, encoding="utf-8")
        for mod in list(sys.modules):
            if mod.startswith("telemetry.role_health"):
                del sys.modules[mod]
        importlib.invalidate_caches()
        assert _sha256(MODULE_PATH) == before, "restore failed — tree left mutated"

    with capsys.disabled():
        print(f"\nMUTATION-PROVED: checks disabled -> FAIL lines "
              f"(module sha256 before={before[:16]})")
        for line in report:
            print(line)
