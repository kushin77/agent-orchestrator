"""Dispatch's declared acceptance policy — read, never restated (issue #885).

---knowledge---
module_id: governance.dispatch.policy
system: governance
app: dispatch
solution_class: pattern
patterns: [no-false-green, honesty-tri-state, declared-authority]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [PolicyUnavailable, Controls, load, stale_minutes]
invariants: ""
gotchas: ""
related: ["#322", "#885"]
do_not_duplicate: null
---knowledge---

Before this module existed, dispatch's controls were Python constants spread
across three files (`model.py`'s reason tuples, `snapshot.py`'s staleness
threshold) with no artifact a reviewer or a test could point at independently
of the code that enforces them. This module reads the declaration from
``controls.yaml`` (a sibling of this file) and gives the package two things:

* :func:`load` returns a validated :class:`Controls`, cross-checked against
  the closed vocabulary ``model.py`` actually defines (`ALLOWED_CLAIM_REASONS`,
  `TERMINAL_CLAIM_REASONS`, `ARBITRATION_REFUSALS`) in both directions — a
  reason the code can emit that this file does not declare, or a reason this
  file declares that the code cannot emit, is a policy defect
  (:class:`PolicyUnavailable`), never a silent pass.
* :func:`stale_minutes` is the value ``claims.arbitrate`` reads as its default
  staleness threshold, in place of a hard-coded constant — change it in a
  temporary copy of ``controls.yaml`` and arbitration behaviour changes with
  it (the mutation ``tests/test_policy.py`` proves).

Exit-code contract (repository convention, GR-12): a missing, malformed, or
drifted policy raises :class:`PolicyUnavailable` — CLI callers map it to
exit 2 (CANNOT-ASSESS), never exit 0.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import model as model_mod  # noqa: E402
from governance.policy import lease  # noqa: E402

SCHEMA = "ao.dispatch/controls-v1"

#: The packaged declaration — read by default, so no caller's working
#: directory decides which policy governed an arbitration.
DEFAULT_CONTROLS = _PKG_DIR / "controls.yaml"


class PolicyUnavailable(Exception):
    """The declared dispatch policy is missing, malformed, or has drifted."""


@dataclass(frozen=True)
class Controls:
    path: Path
    schema: str
    stale_minutes: int
    claim_ttl_hours: int
    allowed_claim_reasons: Tuple[str, ...]
    terminal_claim_reasons: Tuple[str, ...]
    arbitration_refusals: Tuple[str, ...]


def _read_yaml(path: Path):
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable(f"the dispatch policy {path} cannot be read: PyYAML is not installed ({exc})") from exc
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable(f"the dispatch policy {path} is unreadable: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable(f"the dispatch policy {path} is not valid YAML: {exc}") from exc


def _str_tuple(raw, key: str, path: Path) -> Tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise PolicyUnavailable(f"the dispatch policy {path} declares `{key}` as empty or a non-list")
    return tuple(str(item) for item in raw)


def _int(raw, key: str, path: Path) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise PolicyUnavailable(f"the dispatch policy {path} declares `{key}` as {raw!r}, expected a positive integer")
    return raw


def load(path: Path | str | None = None) -> Controls:
    """Read and validate the declared dispatch policy.

    Cross-checks the reason/refusal vocabulary against ``model.py``'s closed
    tuples and the claim TTL against ``governance/policy/lease`` (issue #322,
    the single upstream source) — never restates them without proving they
    still agree.
    """
    path = Path(path) if path else DEFAULT_CONTROLS
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise PolicyUnavailable(f"the dispatch policy {path} must be a mapping")

    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise PolicyUnavailable(f"the dispatch policy {path} carries schema {schema!r}, expected {SCHEMA!r}")

    stale_minutes = _int(raw.get("stale_minutes"), "stale_minutes", path)
    claim_ttl_hours = _int(raw.get("claim_ttl_hours"), "claim_ttl_hours", path)

    allowed = _str_tuple(raw.get("allowed_claim_reasons"), "allowed_claim_reasons", path)
    terminal = _str_tuple(raw.get("terminal_claim_reasons"), "terminal_claim_reasons", path)
    refusals = _str_tuple(raw.get("arbitration_refusals"), "arbitration_refusals", path)

    if set(allowed) != set(model_mod.ALLOWED_CLAIM_REASONS):
        raise PolicyUnavailable(
            f"the dispatch policy {path} declares allowed_claim_reasons {sorted(allowed)}, "
            f"which does not match model.ALLOWED_CLAIM_REASONS {sorted(model_mod.ALLOWED_CLAIM_REASONS)}"
        )
    if set(terminal) != set(model_mod.TERMINAL_CLAIM_REASONS):
        raise PolicyUnavailable(
            f"the dispatch policy {path} declares terminal_claim_reasons {sorted(terminal)}, "
            f"which does not match model.TERMINAL_CLAIM_REASONS {sorted(model_mod.TERMINAL_CLAIM_REASONS)}"
        )
    if set(refusals) != set(model_mod.ARBITRATION_REFUSALS):
        raise PolicyUnavailable(
            f"the dispatch policy {path} declares arbitration_refusals {sorted(refusals)}, "
            f"which does not match model.ARBITRATION_REFUSALS {sorted(model_mod.ARBITRATION_REFUSALS)}"
        )
    if claim_ttl_hours != lease.CLAIM_TTL_HOURS:
        raise PolicyUnavailable(
            f"the dispatch policy {path} declares claim_ttl_hours={claim_ttl_hours}, which has drifted from "
            f"governance/policy/lease.CLAIM_TTL_HOURS={lease.CLAIM_TTL_HOURS} (issue #322 is the single source)"
        )

    return Controls(
        path=path,
        schema=schema,
        stale_minutes=stale_minutes,
        claim_ttl_hours=claim_ttl_hours,
        allowed_claim_reasons=allowed,
        terminal_claim_reasons=terminal,
        arbitration_refusals=refusals,
    )


def stale_minutes(path: Path | str | None = None) -> int:
    """The staleness threshold (minutes) ``arbitrate()`` uses as its default."""
    return load(path).stale_minutes
