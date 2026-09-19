#!/usr/bin/env python3
"""governance/spawn/tiering — one tiers.yaml judges every runtime's spawn (issue #1274).

``gateway/finops/tiers.yaml`` already carries the cheapest-capable ladder
(L0/L1/L2), the per-task-class ``defaultTier``/``maxTier`` and the security
floor. Before this module, that table only gated the fleet's own spawn path
(``gateway/finops/chooser.py``): a Claude subagent dispatch or a hermes
persona could still be handed a tier the table forbids for the task class,
because nothing re-checked them against the same vocabulary.

This module is a **pure** judge: ``role`` + ``task_class`` + ``tier`` in,
allowed/refused out, reading ONLY ``gateway/finops/tiers.yaml`` (via
``gateway/finops/loader.py``, imported read-only — this module never edits
that package). No new tier vocabulary is coined; L0/L1/L2 and the task-class
table are the single source (dupcheck: a second copy of the ladder is refused
by ``scripts/check-tier-parity.sh``).

A tier is allowed for a (role, task_class) pair iff its rank sits between the
task class's ``defaultTier`` and ``maxTier`` inclusive, additionally floored
at ``security.floorTier`` when the class carries a guardrail. Anything else —
a fleet spawn, a Claude subagent dispatch, or a hermes persona render — that
requests a tier outside that window is refused ``FINOPS-ROLE-NOT-ALLOWED``,
by name, regardless of which runtime asked.

Exit-code contract (repo tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. A
missing or unparseable ``tiers.yaml`` is CANNOT-ASSESS, never a pass.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_FINOPS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "finops"
if str(_FINOPS_DIR) not in sys.path:
    sys.path.insert(0, str(_FINOPS_DIR))

from loader import TierTable, ValidationError, load_tier_table  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

FINDING_ROLE_NOT_ALLOWED = "FINOPS-ROLE-NOT-ALLOWED"
FINDING_UNKNOWN_TASK_CLASS = "FINOPS-UNKNOWN-TASK-CLASS"
FINDING_UNKNOWN_TIER = "FINOPS-UNKNOWN-TIER"
FINDING_UNKNOWN_ROLE = "FINOPS-UNKNOWN-ROLE"
#: The three findings the SPAWN POINT needs on top of the judge's (#1413): a
#: declared model the ladder does not carry, a model that contradicts the tier
#: declared beside it, and a tiers.yaml that cannot be read at all. They live
#: here because this module owns the ladder; the spawn point only names them.
FINDING_UNKNOWN_MODEL = "FINOPS-UNKNOWN-MODEL"
FINDING_MODEL_TIER_CONFLICT = "FINOPS-MODEL-TIER-CONFLICT"
FINDING_TIERS_UNAVAILABLE = "FINOPS-TIERS-UNAVAILABLE"

#: Runtimes this judge is known to gate (issue #1274: not only the fleet).
#: The set is descriptive/loggable only — every role is judged against the
#: SAME tiers.yaml window; no role gets a private allowance.
KNOWN_ROLES = ("fleet", "claude-subagent", "hermes-persona")


class TieringUnavailable(Exception):
    """tiers.yaml could not be loaded — no verdict is possible (rc 2)."""


@dataclass(frozen=True)
class Judgment:
    """The judge's verdict for one (role, task_class, tier) request."""

    allowed: bool
    role: str
    task_class: str
    tier: str
    min_tier: str
    max_tier: str
    finding: Optional[str] = None
    detail: str = ""

    def __str__(self) -> str:
        if self.allowed:
            return (
                f"OK role={self.role} class={self.task_class} tier={self.tier} "
                f"(window {self.min_tier}..{self.max_tier})"
            )
        return f"{self.finding}: {self.detail}"


def _load_table(tiers_path: Optional[Path] = None) -> TierTable:
    try:
        if tiers_path is None:
            return load_tier_table()
        return load_tier_table(tiers_path)
    except (ValidationError, OSError) as exc:
        raise TieringUnavailable(str(exc)) from exc


def allowed_tiers(table: TierTable, task_class: str):
    """The inclusive tier window (min, max) a task class may spawn at."""
    cls = table.task_class(task_class)  # ValidationError if unknown
    lo = cls.default_tier
    if cls.guardrail is not None:
        lo = table.higher(lo, table.security_floor)
    hi = cls.max_tier
    return lo, hi


def load_table(tiers_path: Optional[Path] = None) -> TierTable:
    """The loaded ladder, or ``TieringUnavailable`` — the one public loader (#1413).

    The spawn point judges a tier it was TOLD (a spawn record's declared tier, or
    the rung a declared model sits on), so it needs the table itself rather than
    one verdict — and it must get it from here, so there is still exactly one
    reader of ``tiers.yaml``.
    """
    return _load_table(tiers_path)


def default_tier(task_class: str, table: Optional[TierTable] = None, tiers_path: Optional[Path] = None) -> str:
    """The cheapest capable tier for a task class: the floor of its own window.

    This is what a spawn that asks for NO tier runs at — the table's own
    ``defaultTier``, raised to ``security.floorTier`` for a guarded class — so
    "declared no tier" resolves to a real rung instead of an empty string.
    Raises ``ValidationError`` for a class the table does not declare.
    """
    resolved = table if table is not None else _load_table(tiers_path)
    return allowed_tiers(resolved, task_class)[0]


def tier_for_model(model: str, table: Optional[TierTable] = None, tiers_path: Optional[Path] = None) -> Optional[str]:
    """The ladder rung a declared MODEL sits on, or None when it sits on none.

    A spawn may name the model it wants (``claude-opus-5``) instead of a tier;
    the ladder is the only place that model's tier is written down, so it is read
    from there rather than mapped by a second table.
    """
    resolved = table if table is not None else _load_table(tiers_path)
    for rung in resolved.ladder:
        for spec in rung.models:
            if spec.id == model:
                return rung.key
    return None


def judge(
    role: str,
    task_class: str,
    tier: str,
    table: Optional[TierTable] = None,
    tiers_path: Optional[Path] = None,
) -> Judgment:
    """Judge whether ``role`` may spawn at ``tier`` for ``task_class``.

    Pure function: no I/O beyond loading ``tiers.yaml`` (once, by the caller
    or here). Raises ``TieringUnavailable`` when the table cannot be loaded —
    callers map that to rc 2, never to a refusal or a pass.
    """
    if table is None:
        table = _load_table(tiers_path)

    if role not in KNOWN_ROLES:
        return Judgment(
            allowed=False,
            role=role,
            task_class=task_class,
            tier=tier,
            min_tier="",
            max_tier="",
            finding=FINDING_UNKNOWN_ROLE,
            detail=f"role {role!r} is not a known spawn runtime {KNOWN_ROLES}",
        )

    try:
        cls = table.task_class(task_class)
    except ValidationError:
        return Judgment(
            allowed=False,
            role=role,
            task_class=task_class,
            tier=tier,
            min_tier="",
            max_tier="",
            finding=FINDING_UNKNOWN_TASK_CLASS,
            detail=f"task class {task_class!r} is not in tiers.yaml taskClasses",
        )

    if tier not in table.keys():
        return Judgment(
            allowed=False,
            role=role,
            task_class=task_class,
            tier=tier,
            min_tier="",
            max_tier="",
            finding=FINDING_UNKNOWN_TIER,
            detail=f"tier {tier!r} is not one of {table.keys()}",
        )

    lo, hi = allowed_tiers(table, task_class)
    if table.rank(tier) < table.rank(lo) or table.rank(tier) > table.rank(hi):
        return Judgment(
            allowed=False,
            role=role,
            task_class=task_class,
            tier=tier,
            min_tier=lo,
            max_tier=hi,
            finding=FINDING_ROLE_NOT_ALLOWED,
            detail=(
                f"{role} requested {tier} for task class {cls.name!r}, "
                f"allowed window is {lo}..{hi}"
            ),
        )

    return Judgment(
        allowed=True,
        role=role,
        task_class=task_class,
        tier=tier,
        min_tier=lo,
        max_tier=hi,
    )


# --------------------------------------------------------------------------- #
# CLI: python3 -m governance.spawn.tiering judge --role R --class C --tier T
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governance.spawn.tiering")
    sub = parser.add_subparsers(dest="command", required=True)

    judge_p = sub.add_parser("judge", help="judge one (role, class, tier) spawn request")
    judge_p.add_argument("--role", required=True)
    judge_p.add_argument("--class", dest="task_class", required=True)
    judge_p.add_argument("--tier", required=True)
    judge_p.add_argument("--tiers-path", type=Path, default=None)

    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "judge":
        try:
            verdict = judge(args.role, args.task_class, args.tier, tiers_path=args.tiers_path)
        except TieringUnavailable as exc:
            print(f"governance.spawn.tiering: CANNOT-ASSESS — {exc}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        print(str(verdict))
        return EXIT_OK if verdict.allowed else EXIT_NOT_OK

    parser.error(f"unknown command {args.command!r}")
    return EXIT_CANNOT_ASSESS  # pragma: no cover - argparse exits before this


if __name__ == "__main__":
    sys.exit(main())
