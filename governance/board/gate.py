"""The enforcement gate (issue #143).

---knowledge---
module_id: governance.board.gate
system: governance
app: board
solution_class: pattern
patterns: [no-false-green]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [now_iso, load_exceptions, run_check, run_gate, write_report]
invariants: ""
gotchas: ""
related: ["#139", "#140", "#141", "#142", "#143"]
do_not_duplicate: null
---knowledge---

Re-runs the required CMR gates for real — it does not trust their cached
``.verify/*.json`` reports, because a report on disk could be stale relative
to the working tree. Every check is a real subprocess with a real exit code;
a check that cannot fail its own gate is rejected elsewhere in this repo
(no-false-green doctrine, GR-12) and the board gate holds itself to the same
rule.

Required checks map 1:1 to the issues that built them:

* ``knowledge-index``  (#139) — the institutional knowledge index is valid.
* ``conformance``      (#140) — CMR class/pattern/template conformance.
* ``lessons``           (#141) — RCA + lessons ledger is complete and traceable.
* ``remediation``       (#142) — violator remediation issues are current.

Exceptions (``governance/board/exceptions.yaml``) let the board grant a
named, timeboxed, reasoned exemption for exactly one check. An exception that
has expired is worth nothing — it is reported as ``expired_exceptions`` and
the underlying failure counts in full.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from model import (
    STATUS_CANNOT_ASSESS,
    STATUS_EXCEPTED,
    STATUS_NOT_OK,
    STATUS_OK,
    BoardReport,
    CheckResult,
    Exception_,
    ExceptionInvalid,
    REPEATED_VIOLATION_THRESHOLD,
    aggregate_status,
)

EXCEPTIONS_RELPATH = Path("governance") / "board" / "exceptions.yaml"
LEDGER_RELPATH = Path("governance") / "board" / "violations.jsonl"
REPORT_RELPATH = Path(".verify") / "board-report.json"

REQUIRED_CHECKS: Tuple[Tuple[str, str], ...] = (
    ("knowledge-index", "bash scripts/check-knowledge-index.sh"),
    ("conformance", "bash scripts/check-conformance.sh"),
    ("lessons", "bash scripts/check-lessons.sh"),
    ("remediation", "bash scripts/check-remediation.sh"),
)

_TAIL_LINES = 10


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _exit_to_status(rc: int) -> str:
    if rc == 0:
        return STATUS_OK
    if rc == 2:
        return STATUS_CANNOT_ASSESS
    return STATUS_NOT_OK


def load_exceptions(path: Path) -> List[Exception_]:
    if not path.exists():
        return []
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ExceptionInvalid("PyYAML is not installed: %s" % exc) from exc

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("exceptions", [])
    if not isinstance(entries, list):
        raise ExceptionInvalid("exceptions.yaml: 'exceptions' must be a list")

    out: List[Exception_] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ExceptionInvalid("exceptions.yaml: each exception must be a mapping")
        missing = [k for k in ("check", "reason", "approved_by", "expires") if k not in entry]
        if missing:
            raise ExceptionInvalid(
                "exceptions.yaml: exception missing field(s) %s" % ", ".join(missing)
            )
        out.append(
            Exception_(
                check=str(entry["check"]),
                reason=str(entry["reason"]),
                approved_by=str(entry["approved_by"]),
                expires=str(entry["expires"]),
            )
        )
    return out


def run_check(root: Path, name: str, command: str) -> CheckResult:
    proc = subprocess.run(
        ["bash", "-c", command],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    status = _exit_to_status(proc.returncode)
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail_lines = [ln for ln in combined.splitlines() if ln.strip()][-_TAIL_LINES:]
    return CheckResult(
        name=name,
        command=command,
        status=status,
        exit_code=proc.returncode,
        output_tail="\n".join(tail_lines),
    )


def _load_violation_counts(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    counts: Dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        import json

        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = rec.get("check")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _record_violation(path: Path, check: str, at: str) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"check": check, "at": at}) + "\n")


def run_gate(
    root: Path,
    checks: Sequence[Tuple[str, str]] = REQUIRED_CHECKS,
    exceptions_path: Path = None,
    ledger_path: Path = None,
) -> BoardReport:
    exceptions_path = root / EXCEPTIONS_RELPATH if exceptions_path is None else exceptions_path
    ledger_path = root / LEDGER_RELPATH if ledger_path is None else ledger_path

    exceptions = {e.check: e for e in load_exceptions(exceptions_path)}
    generated_at = now_iso()

    results: List[CheckResult] = []
    expired: List[str] = []
    escalations: List[Dict[str, Any]] = []
    violation_counts = _load_violation_counts(ledger_path)

    for name, command in checks:
        result = run_check(root, name, command)
        if result.status == STATUS_NOT_OK:
            exc = exceptions.get(name)
            if exc is not None:
                if exc.is_active():
                    result.status = STATUS_EXCEPTED
                    result.exception_applied = exc.reason
                else:
                    expired.append(name)
            if result.status == STATUS_NOT_OK:
                _record_violation(ledger_path, name, generated_at)
                count = violation_counts.get(name, 0) + 1
                if count >= REPEATED_VIOLATION_THRESHOLD:
                    escalations.append(
                        {
                            "check": name,
                            "occurrences": count,
                            "reason": "repeated violation — board review required",
                        }
                    )
        results.append(result)

    report = BoardReport(
        generated_at=generated_at,
        status=aggregate_status(results),
        checks=results,
        expired_exceptions=expired,
        escalations=escalations,
    )
    return report


def write_report(report: BoardReport, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
