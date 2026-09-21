"""Live projection of the board's declared tags (issue #1175).

---knowledge---
module_id: governance.tagging.live
system: governance
app: tagging
solution_class: pattern
patterns: [offline-hermetic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [LiveUnavailable, snapshot_age_minutes, project, render]
invariants: ""
gotchas: ""
related: ["#1175"]
do_not_duplicate: null
---knowledge---

The taxonomy can say what a tag *may* be; only the board can say what it *is*.
This module is the live half: it projects the offline board snapshot
(`.board/snapshot.json`) and the authority's ledger into one document that
answers the questions a tag system exists to answer —

* **coverage** — how many open, tagged items exist, and which declared dimensions
  are actually being used (an unused dimension is either new or dead);
* **conformance** — which open items the taxonomy refuses, by refusal code, so a
  drift report names the items rather than the count;
* **authority load** — what the ledger has recorded, by kind and decision.

It is deliberately read-only and offline: a projection that needs the network is
a report, not a gate. The board snapshot's own staleness is reported as a field
rather than raised, because a stale snapshot is a fact about the input, not a
failure of this reader — the caller decides what to do with it, and the CLI's
``--max-stale-minutes`` is the one place that policy lives.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ledger as _ledger  # noqa: E402
import model as M  # noqa: E402

DEFAULT_SNAPSHOT = Path(".board") / "snapshot.json"


class LiveUnavailable(Exception):
    """The snapshot could not be read — a CANNOT-ASSESS for the projection."""


def _age_minutes(stamp: str) -> Optional[float]:
    text = stamp.strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    return round(delta.total_seconds() / 60.0, 1)


def snapshot_age_minutes(snapshot: Mapping[str, Any]) -> Optional[float]:
    """How old the board snapshot is, in minutes, or ``None`` when unreadable."""
    for key in ("generated_at", "generated", "at", "fetched_at"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return _age_minutes(value)
    return None


def _labels_of(issue: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for label in issue.get("labels") or ():
        if isinstance(label, Mapping):
            out.append(str(label.get("name", "")))
        else:
            out.append(str(label))
    return out


def project(
    root: Path,
    taxonomy: M.Taxonomy,
    snapshot_path: Optional[Path] = None,
    ledger_file: Optional[Path] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """The live projection. Raises :class:`LiveUnavailable` when unreadable."""
    snapshot = Path(snapshot_path) if snapshot_path else Path(root) / DEFAULT_SNAPSHOT
    if not snapshot.is_file():
        raise LiveUnavailable(
            "no board snapshot at %s — run: python3 governance/dispatch/cli.py "
            "snapshot --from-github" % snapshot
        )
    try:
        raw = json.loads(snapshot.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise LiveUnavailable("%s is not valid JSON: %s" % (snapshot, exc))

    issues = raw if isinstance(raw, list) else raw.get("issues") or []
    source = {} if isinstance(raw, list) else dict(raw)

    open_items = [
        issue
        for issue in issues
        if isinstance(issue, Mapping) and str(issue.get("state", "open")).lower() == "open"
    ]

    dimension_use: Dict[str, Dict[str, int]] = {}
    refusals: Dict[str, List[str]] = {}
    deviations: Dict[str, List[str]] = {}
    tagged = 0
    untagged: List[str] = []

    for issue in open_items:
        subject = "issue-%s" % issue.get("number", "?")
        tag_set = M.parse_labels(_labels_of(issue))
        if not tag_set.values:
            untagged.append(subject)
            continue
        tagged += 1
        for name, values in tag_set.values.items():
            bucket = dimension_use.setdefault(name, {})
            for value in values:
                bucket[value] = bucket.get(value, 0) + 1
        for finding in M.validate_tags(tag_set, taxonomy, target="issue", strict=strict):
            # A refusal is an ERROR. A deviation is reported in its own bucket,
            # because counting a "should declare" warning as a refusal would make
            # a clean board look refused — and a report that cries wolf is a
            # report nobody reads.
            bucket = refusals if finding.severity == M.SEVERITY_ERROR else deviations
            bucket.setdefault(finding.code, []).append(
                "%s: %s" % (subject, finding.message)
            )

    declared = set(taxonomy.dimensions)
    unused = sorted(declared - set(dimension_use))
    undeclared = sorted(set(dimension_use) - declared)

    return {
        "snapshot": str(snapshot),
        "snapshot_age_minutes": snapshot_age_minutes(source),
        "open_issues": len(open_items),
        "tagged": tagged,
        "untagged": len(untagged),
        "untagged_sample": sorted(untagged)[:20],
        "dimensions_used": {k: dict(sorted(v.items())) for k, v in sorted(dimension_use.items())},
        "dimensions_declared_but_unused": unused,
        "dimensions_undeclared_but_used": undeclared,
        "refusals": {k: sorted(v) for k, v in sorted(refusals.items())},
        "refusal_count": sum(len(v) for v in refusals.values()),
        "deviations": {k: sorted(v) for k, v in sorted(deviations.items())},
        "deviation_count": sum(len(v) for v in deviations.values()),
        "ledger": _ledger.summarise(ledger_file or _ledger.ledger_path(root)),
    }


def render(projection: Mapping[str, Any], stream: Any = sys.stdout) -> None:
    """Print a projection a human can read."""
    age = projection.get("snapshot_age_minutes")
    print("tagging-live: snapshot %s (age %s)" % (
        projection.get("snapshot"),
        ("%sm" % age) if age is not None else "unknown",
    ))
    print(
        "  board:  %d open, %d tagged, %d untagged"
        % (
            projection.get("open_issues", 0),
            projection.get("tagged", 0),
            projection.get("untagged", 0),
        )
    )
    used = projection.get("dimensions_used") or {}
    if used:
        print("  dimensions in use:")
        for name, values in used.items():
            rendered = ", ".join("%s=%d" % kv for kv in sorted(values.items()))
            print("    %-12s %s" % (name, rendered))
    unused = projection.get("dimensions_declared_but_unused") or []
    if unused:
        print("  declared but unused: %s" % ", ".join(unused))
    undeclared = projection.get("dimensions_undeclared_but_used") or []
    if undeclared:
        print("  UNDECLARED but in use: %s" % ", ".join(undeclared))
    refusals = projection.get("refusals") or {}
    if refusals:
        print("  refusals (%d error(s)):" % projection.get("refusal_count", 0))
        for code, messages in refusals.items():
            print("    %-24s %d item(s)" % (code, len(messages)))
    deviations = projection.get("deviations") or {}
    if deviations:
        print(
            "  deviations (%d, reported not refused):"
            % projection.get("deviation_count", 0)
        )
        for code, messages in deviations.items():
            print("    %-24s %d item(s)" % (code, len(messages)))
    ledger_summary = projection.get("ledger") or {}
    print(
        "  ledger: %d row(s) at %s"
        % (ledger_summary.get("rows", 0), ledger_summary.get("path", "?"))
    )
    if ledger_summary.get("malformed"):
        print("  ledger malformed: %s" % "; ".join(ledger_summary["malformed"]))
