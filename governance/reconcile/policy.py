"""The reconcile package's own declared controls — read, never restated (#885).

---knowledge---
module_id: governance.reconcile.policy
system: governance
app: reconcile
solution_class: pattern
patterns: [no-false-green, honesty-tri-state, declared-authority, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ControlsUnavailable, Controls, load, dump, main]
invariants: ""
gotchas: ""
related: ["#885"]
do_not_duplicate: null
---knowledge---

`governance/policy/lease.py` already owns every timing this worker shares with
the rest of the fleet. This module reads the two controls that belong to
`governance/reconcile` alone, from the sibling artifact `controls.yaml`:

* `sweep.max_actions_per_pass` — the runaway guard `sweep()` enforces before it
  performs a teardown (see `controls.yaml` for why it exists);
* `sweep.outcome_codes` — the closed vocabulary `ledger.py` stamps a decision
  record with.

Consumers read this module's :func:`load` — `sweep.py` and `ledger.py` must
never restate a threshold or a code as a bare literal (that is precisely the
drift this artifact exists to prevent, GR-12/GR-29).

Exit-code contract: an unreadable, malformed, or internally-inconsistent
controls file raises :class:`ControlsUnavailable` — CANNOT-ASSESS, never a
silent default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple

SCHEMA = "ao.reconcile/controls-v1"

#: The packaged declaration — read by default, so no caller's working
#: directory decides which controls governed a sweep.
DEFAULT_CONTROLS = Path(__file__).resolve().parent / "controls.yaml"

#: The closed set of outcome names `sweep.py` can produce, plus `refused` —
#: `outcome_codes` must declare exactly this vocabulary, no more, no less.
OUTCOME_NAMES = frozenset({"reclaimed", "parked", "shelved", "reported", "failed", "refused"})


class ControlsUnavailable(ValueError):
    """The controls file could not be read or is internally inconsistent.

    A :class:`ValueError` subclass on purpose: ``governance/reconcile/cli.py``
    maps an uncaught ``ValueError`` from any command to exit 2 (CANNOT-ASSESS),
    the same tri-state contract every other refusal in this package holds to.
    """


@dataclass(frozen=True)
class Controls:
    max_actions_per_pass: int
    outcome_codes: Mapping[str, str]

    def code_for(self, outcome: str) -> str:
        try:
            return self.outcome_codes[outcome]
        except KeyError as exc:
            raise ControlsUnavailable(
                f"outcome {outcome!r} has no declared code in {DEFAULT_CONTROLS} "
                "(the vocabulary is closed — a code must be declared before it "
                "is emitted)"
            ) from exc


def load(path: Path | str | None = None) -> Controls:
    """Read and validate the declared controls. Never cached — a stale copy of
    a control is exactly the drift this artifact exists to prevent."""
    import yaml  # noqa: PLC0415 - optional dependency, resolved on demand

    resolved = Path(path) if path else DEFAULT_CONTROLS
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControlsUnavailable(f"{resolved} is unreadable: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - yaml.YAMLError and friends
        raise ControlsUnavailable(f"{resolved} is not valid YAML: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ControlsUnavailable(f"{resolved} must be a mapping")
    if str(raw.get("schema") or "") != SCHEMA:
        raise ControlsUnavailable(
            f"{resolved} declares schema {raw.get('schema')!r}, expected {SCHEMA!r}"
        )

    sweep_raw = raw.get("sweep")
    if not isinstance(sweep_raw, Mapping):
        raise ControlsUnavailable(f"{resolved} declares no `sweep` control block")

    limit = sweep_raw.get("max_actions_per_pass")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ControlsUnavailable(
            f"{resolved}: sweep.max_actions_per_pass must be a non-negative integer, "
            f"got {limit!r}"
        )

    codes_raw = sweep_raw.get("outcome_codes")
    if not isinstance(codes_raw, Mapping):
        raise ControlsUnavailable(f"{resolved}: sweep.outcome_codes must be a mapping")
    codes: dict[str, str] = {str(k): str(v) for k, v in codes_raw.items()}
    declared = frozenset(codes)
    if declared != OUTCOME_NAMES:
        missing = OUTCOME_NAMES - declared
        extra = declared - OUTCOME_NAMES
        raise ControlsUnavailable(
            f"{resolved}: sweep.outcome_codes must declare exactly {sorted(OUTCOME_NAMES)}"
            f" (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    if len(set(codes.values())) != len(codes):
        raise ControlsUnavailable(f"{resolved}: sweep.outcome_codes has duplicate codes")

    return Controls(max_actions_per_pass=int(limit), outcome_codes=codes)


def dump(controls: Controls) -> str:
    lines = [f"sweep.max_actions_per_pass = {controls.max_actions_per_pass}"]
    for name in sorted(controls.outcome_codes):
        lines.append(f"sweep.outcome_codes.{name} = {controls.outcome_codes[name]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    import sys

    try:
        controls = load(argv[0] if argv else None)
    except ControlsUnavailable as exc:
        print(f"reconcile-controls: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2
    print(dump(controls))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
