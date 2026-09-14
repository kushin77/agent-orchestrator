"""Wave ledger schema (issue #181, gap 3).

One JSONL record per wave. Every record carries the same fields so the ledger
can be queried for the "faster / cheaper / smarter" trend across waves, and the
SLO fields form a **closed vocabulary** — no free-form keys sneak in.

Provenance (GR-10) is explicit: ``module_pins`` keys the SHA of the deepseek
module and the codeidx context-pack this wave consumed (empty string = baseline
unset). ``direction_issues`` records the issues pushed back to their boards for
the next wave (the bidirectional half of "perfect sync").

This module is a plain data container plus strict parsing; the derived fields
(``duration_s``, ``verify_rate``) are computed by ``ledger.py`` so the maths
lives in exactly one place.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Closed vocabulary of module-pin keys (issue #181, rule 4: pin for reproducibility).
PIN_DEEPSEEK = "deepseek"
PIN_CODEIDX = "codeidx-context-pack"
MODULE_PINS = (PIN_DEEPSEEK, PIN_CODEIDX)

# Closed vocabulary of SLO fields — every numeric field the ledger tracks.
SLO_FIELDS = (
    "duration_s",
    "issues",
    "cost_usd",
    "escalations",
    "verify_failures",
    "verify_rate",
)

# A wave id is non-empty, no whitespace, so it can be a stable JSONL key.
_WAVE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

_MISSING = object()


def _require(obj: dict, key: str, kind: type, where: str) -> Any:
    value = obj.get(key, _MISSING)
    if value is _MISSING:
        raise ValueError(f"{where}: missing required field '{key}'")
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{where}: field '{key}' must be an integer, got {type(value).__name__}")
    elif kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where}: field '{key}' must be a number, got {type(value).__name__}")
    elif not isinstance(value, kind):
        raise ValueError(f"{where}: field '{key}' must be {kind.__name__}, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class WaveRecord:
    """One wave in the append-only ledger."""

    wave_id: str
    started_at: str = ""
    finished_at: str = ""
    duration_s: int = 0
    issues: int = 0
    cost_usd: float = 0.0
    escalations: int = 0
    verify_failures: int = 0
    verify_rate: float = 1.0
    module_pins: dict[str, str] = field(default_factory=dict)
    direction_issues: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "wave_id": self.wave_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "issues": self.issues,
            "cost_usd": self.cost_usd,
            "escalations": self.escalations,
            "verify_failures": self.verify_failures,
            "verify_rate": self.verify_rate,
            "module_pins": dict(sorted(self.module_pins.items())),
            "direction_issues": list(self.direction_issues),
        }

    @classmethod
    def from_dict(cls, obj: Any, where: str = "ledger") -> "WaveRecord":
        """Parse and validate one ledger record (closed vocabulary)."""
        if not isinstance(obj, dict):
            raise ValueError(f"{where}: record must be a JSON object")
        unknown = sorted(set(obj) - {
            "wave_id", "started_at", "finished_at", "duration_s", "issues",
            "cost_usd", "escalations", "verify_failures", "verify_rate",
            "module_pins", "direction_issues",
        })
        if unknown:
            raise ValueError(
                f"{where}: unknown field(s) {', '.join(unknown)} — the wave record schema is closed"
            )
        wave_id = _require(obj, "wave_id", str, where).strip()
        if not wave_id or not _WAVE_ID_RE.match(wave_id):
            raise ValueError(f"{where}: field 'wave_id' must match {_WAVE_ID_RE.pattern}")
        started_at = str(obj.get("started_at", "") or "")
        finished_at = str(obj.get("finished_at", "") or "")
        duration_s = _require(obj, "duration_s", int, where)
        issues = _require(obj, "issues", int, where)
        cost_usd = float(_require(obj, "cost_usd", float, where))
        escalations = _require(obj, "escalations", int, where)
        verify_failures = _require(obj, "verify_failures", int, where)
        verify_rate = float(_require(obj, "verify_rate", float, where))
        if issues < 0 or escalations < 0 or verify_failures < 0:
            raise ValueError(f"{where}: issues/escalations/verify_failures must be non-negative")
        if duration_s < 0:
            raise ValueError(f"{where}: field 'duration_s' must be non-negative")

        pins = obj.get("module_pins") or {}
        if not isinstance(pins, dict):
            raise ValueError(f"{where}: field 'module_pins' must be an object")
        for key in pins:
            if key not in MODULE_PINS:
                raise ValueError(f"{where}: module_pins key {key!r} outside the closed vocabulary {MODULE_PINS}")
        module_pins = {str(key): str(pins.get(key, "") or "") for key in MODULE_PINS}

        direction = obj.get("direction_issues") or []
        if not isinstance(direction, list):
            raise ValueError(f"{where}: field 'direction_issues' must be a list")
        direction_issues = tuple(sorted(str(item) for item in direction))

        return cls(
            wave_id=wave_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_s=duration_s,
            issues=issues,
            cost_usd=cost_usd,
            escalations=escalations,
            verify_failures=verify_failures,
            verify_rate=verify_rate,
            module_pins=module_pins,
            direction_issues=direction_issues,
        )
