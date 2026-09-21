"""Wave ledger: append, query, and SLO assertions (issue #181, gaps 3 + 6).

---knowledge---
module_id: governance.waves.ledger
system: governance
app: waves
solution_class: pattern
patterns: [append-only-ledger, deterministic]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [parse_iso, compute_duration_s, compute_verify_rate, build_record, append_record, read_ledger, query, slo_violations, ledger_stats]
invariants: ""
gotchas: ""
related: ["#181"]
do_not_duplicate: null
---knowledge---

The ledger is an append-only JSONL file (``governance/waves/ledger.jsonl``), one
record per wave. Appending computes the derived fields — ``duration_s`` from the
two ISO timestamps, and ``verify_rate = 1 - verify_failures / issues`` — so the
maths is deterministic and lives in exactly one place.

SLOs are **reported, not enforced**: a wave that escalated is still recorded
(never silently dropped) and ``slo_violations`` names the breach, so the ledger
stays a faithful history even when a wave missed its target.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model import MODULE_PINS, SLO_FIELDS, WaveRecord

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_LEDGER_PATH = ROOT / "governance" / "waves" / "ledger.jsonl"

# SLO targets (issue #181, gap 3). A wave outside these is a measured finding,
# never a dropped record.
SLO_MAX_ESCALATIONS = 0
SLO_MIN_VERIFY_RATE = 0.9


def parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp; naive input is UTC; None when unusable."""
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_duration_s(started_at: str, finished_at: str) -> int:
    """Whole seconds between the two timestamps (0 when either side is empty).

    Raises ValueError when the finish precedes the start — a data error the
    caller must fix, never a silently clamped duration.
    """
    start = parse_iso(started_at)
    finish = parse_iso(finished_at)
    if start is None or finish is None:
        return 0
    delta = (finish - start).total_seconds()
    if delta < 0:
        raise ValueError(f"finished_at {finished_at!r} precedes started_at {started_at!r}")
    return int(delta)


def compute_verify_rate(verify_failures: int, issues: int) -> float:
    """``1 - verify_failures / issues``, clamped to [0, 1], 1.0 when no issues.

    A wave with no issues yields a vacuous 1.0; ``slo_violations`` flags that
    separately so the rate can never be mistaken for a measured pass.
    """
    if issues <= 0:
        return 1.0
    rate = 1.0 - (verify_failures / issues)
    return round(min(1.0, max(0.0, rate)), 4)


def build_record(
    wave_id: str,
    started_at: str = "",
    finished_at: str = "",
    issues: int = 0,
    cost_usd: float = 0.0,
    escalations: int = 0,
    verify_failures: int = 0,
    module_pins: dict[str, str] | None = None,
    direction_issues: tuple[str, ...] | list[str] = (),
) -> WaveRecord:
    """Build one record, computing the derived SLO fields."""
    if not wave_id or not wave_id.strip():
        raise ValueError("wave_id must not be empty")
    if issues < 0 or escalations < 0 or verify_failures < 0 or cost_usd < 0:
        raise ValueError("issues/escalations/verify_failures/cost_usd must be non-negative")
    pins = dict(module_pins or {})
    unknown = sorted(set(pins) - set(MODULE_PINS))
    if unknown:
        raise ValueError(f"module_pins key(s) {', '.join(unknown)} outside the closed vocabulary {MODULE_PINS}")
    module_pins = {key: str(pins.get(key, "") or "") for key in MODULE_PINS}
    duration_s = compute_duration_s(started_at, finished_at)
    verify_rate = compute_verify_rate(verify_failures, issues)
    return WaveRecord(
        wave_id=wave_id.strip(),
        started_at=started_at.strip(),
        finished_at=finished_at.strip(),
        duration_s=duration_s,
        issues=issues,
        cost_usd=round(float(cost_usd), 4),
        escalations=escalations,
        verify_failures=verify_failures,
        verify_rate=verify_rate,
        module_pins=module_pins,
        direction_issues=tuple(sorted(direction_issues)),
    )


def append_record(record: WaveRecord, path: Path | str = DEFAULT_LEDGER_PATH) -> Path:
    """Append one record as a JSONL line. A record with escalations is recorded."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_json(), sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return target


def read_ledger(path: Path | str = DEFAULT_LEDGER_PATH) -> list[WaveRecord]:
    """Read and validate every line; raises ValueError naming the bad line."""
    target = Path(path)
    if not target.exists():
        return []
    records: list[WaveRecord] = []
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target}:{lineno} invalid JSON: {exc}") from exc
        records.append(WaveRecord.from_dict(obj, where=f"{target}:{lineno}"))
    return records


def query(records: list[WaveRecord], wave_id: str | None = None) -> list[WaveRecord]:
    """Filter records by wave id (None = all), most recent first."""
    selected = [r for r in records if wave_id is None or r.wave_id == wave_id]
    return sorted(selected, key=lambda r: r.finished_at or "", reverse=True)


def slo_violations(record: WaveRecord) -> list[str]:
    """Name every SLO breach on a record (empty = within SLO)."""
    violations: list[str] = []
    if record.escalations > SLO_MAX_ESCALATIONS:
        violations.append(f"escalations {record.escalations} exceed SLO {SLO_MAX_ESCALATIONS}")
    if record.verify_rate < SLO_MIN_VERIFY_RATE:
        violations.append(f"verify_rate {record.verify_rate} below SLO {SLO_MIN_VERIFY_RATE}")
    if record.issues == 0:
        violations.append("wave recorded no issues (verify_rate is vacuous)")
    return violations


def ledger_stats(records: list[WaveRecord]) -> dict[str, Any]:
    """Aggregate the measured loop across every recorded wave (deterministic)."""
    if not records:
        return {"waves": 0, "fields": list(SLO_FIELDS)}
    return {
        "waves": len(records),
        "fields": list(SLO_FIELDS),
        "total_issues": sum(r.issues for r in records),
        "total_cost_usd": round(sum(r.cost_usd for r in records), 4),
        "total_escalations": sum(r.escalations for r in records),
        "total_verify_failures": sum(r.verify_failures for r in records),
        "mean_duration_s": round(sum(r.duration_s for r in records) / len(records), 4),
    }
