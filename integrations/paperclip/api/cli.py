#!/usr/bin/env python3
"""CLI for the paperclip HTTP surface projection (issue #413).

Verbs an operator (or the gate) needs:

* ``emit``     — write (or print) the deterministic OpenAPI document.
* ``check``    — re-derive the sources and verify the document + committed artifact.
* ``health``   — read the real dependencies and report the verdict.
* ``company``  — resolve a path company id onto the fleet tenancy (or refuse).
* ``controls`` — provoke, in process, every failure this surface must refuse
  (a document drifting from its contracts, a missing taxonomy entry, an
  undeclared company mapping, and health reporting ok while a dependency is
  absent), each refused **by name**.

No secret is read, written or echoed here (GR-6): the surface describes routes
and reads local state.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Sequence, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.paperclip.api import company as company_mod  # noqa: E402
from integrations.paperclip.api import health as health_mod  # noqa: E402
from integrations.paperclip.api import openapi as openapi_mod  # noqa: E402

#: Overall exit for a healthy read.
RC_OK = 0
#: Overall exit for a degraded read (a named dependency is stale).
RC_DEGRADED = 1
#: Overall exit for an unhealthy read (a named dependency is unreachable).
RC_UNHEALTHY = 2


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def _cmd_emit(args: argparse.Namespace) -> int:
    root = _root(args)
    out = Path(args.out) if args.out else None
    text = openapi_mod.emit(root, out)
    if out is None:
        sys.stdout.write(text)
        return RC_OK
    print(f"  OK    emitted {out} ({len(text)} bytes)")
    return RC_OK


def _cmd_check(args: argparse.Namespace) -> int:
    root = _root(args)
    document = openapi_mod.build_document(root)
    findings: List[str] = list(openapi_mod.validate_document(document, root))

    artifact = root / openapi_mod.EMITTED_ARTIFACT
    fresh = openapi_mod.serialize(document)
    if not artifact.exists():
        findings.append(f"the emitted artifact {openapi_mod.EMITTED_ARTIFACT.as_posix()} is missing")
    else:
        committed = artifact.read_text(encoding="utf-8")
        if committed != fresh:
            findings.append(
                f"the emitted artifact {openapi_mod.EMITTED_ARTIFACT.as_posix()} is stale or "
                "hand-edited (re-emit with `cli.py emit`)"
            )
            # Validate the committed document too, so a hand-edit is refused
            # naming the contract it drifted from, not only the artifact.
            try:
                findings.extend(openapi_mod.validate_document(json.loads(committed), root))
            except ValueError:
                findings.append(
                    f"the emitted artifact {openapi_mod.EMITTED_ARTIFACT.as_posix()} is not valid JSON"
                )

    # The health read must be honest: cross-check it against fresh probe readings.
    now = datetime.now(timezone.utc)
    report = health_mod.health(root, now=now)
    findings.extend(health_mod.check_report(report, root, now=now))

    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    print(
        "  OK    the document matches the frozen contracts, the taxonomy, the company "
        "mapping and the committed artifact; the health read is honest"
    )
    return RC_OK


def _beat_live_read(root: Path, report: "health_mod.HealthReport") -> None:
    """Report this live dependency read as `paperclip`'s runtime beat (#1412).

    `paperclip` is registered in `fleet/runtimes.yaml` and must beat, but this
    repo runs no loop for it: the adapter is a CLI, and the only moment it is
    verifiably ALIVE is a live read of its own dependencies — which is what
    `health` does and what `check` cross-checks.

    The beat is written when the read came back OK or DEGRADED (the surface
    answered, one dependency is merely stale) and NOT when it is unhealthy (a
    named dependency is unreachable): the record means "paperclip is alive", so an
    unhealthy read must let the old beat age out and be judged
    `runtime-stale:paperclip` rather than refresh the claim with a stamp the
    surface cannot honour.

    Never fatal, and never on stdout: `health`'s JSON document is its contract
    with `check` and with the gate, and a liveness stamp must not be able to
    change it.
    """
    if report.status not in (health_mod.STATUS_OK, health_mod.STATUS_DEGRADED):
        print(
            "health: beat SKIPPED — the read is %s, so nothing here is alive to report"
            % report.status,
            file=sys.stderr,
        )
        return
    try:
        from fleet import beats  # noqa: PLC0415 - lazy: the producer lives with the fleet
    except ImportError as exc:  # a scratch tree that copied the adapter alone
        print("health: beat SKIPPED — fleet/beats.py is not importable: %s" % exc, file=sys.stderr)
        return
    beats.best_effort("paperclip", "running", root=root, cwd=root)


def _cmd_health(args: argparse.Namespace) -> int:
    root = _root(args)
    report = health_mod.health(root)
    _beat_live_read(root, report)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return {health_mod.STATUS_OK: RC_OK, health_mod.STATUS_DEGRADED: RC_DEGRADED}.get(
        report.status, RC_UNHEALTHY
    )


def _cmd_company(args: argparse.Namespace) -> int:
    root = _root(args)
    print(
        json.dumps(
            {
                "mapping": company_mod.mapping(),
                "declared_companies": list(company_mod.declared_companies(root)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return RC_OK


def _absent_probes(name: str) -> Callable[[Path, Any], health_mod.DependencyState]:
    """A probe that reports its dependency missing, without touching the tree."""

    def probe(_root: Path, _now: Any) -> health_mod.DependencyState:
        return health_mod.DependencyState(name, health_mod.STATE_MISSING, "provoked absent")

    return probe


def _controls(root: Path) -> List[Tuple[str, bool, List[str]]]:
    """Provoke each failure this surface must refuse; return (name, refused, findings)."""
    document = openapi_mod.build_document(root)
    results: List[Tuple[str, bool, List[str]]] = []

    # (A) a document that drifts from the frozen contracts.
    drifted = copy.deepcopy(document)
    drifted["components"]["schemas"]["Ticket"]["properties"]["status"]["enum"] = ["open"]
    findings = openapi_mod.validate_document(drifted, root)
    results.append(
        (
            "contract-drift",
            any("drifts from the frozen contract" in f and "Ticket" in f for f in findings),
            findings,
        )
    )

    # (B) a missing error-taxonomy entry.
    missing_taxonomy = copy.deepcopy(document)
    del missing_taxonomy["components"]["responses"]["422"]
    findings = openapi_mod.validate_document(missing_taxonomy, root)
    results.append(
        ("taxonomy-422", any("'422'" in f and "missing" in f for f in findings), findings)
    )

    # (C) an undeclared company mapping.
    undeclared = copy.deepcopy(document)
    undeclared.pop("x-company-mapping", None)
    undeclared["components"]["parameters"]["companyId"].pop("x-company-mapping", None)
    findings = openapi_mod.validate_document(undeclared, root)
    results.append(
        ("company-mapping", any("company mapping is undeclared" in f for f in findings), findings)
    )

    # (D) health reporting ok while a named dependency is absent.
    lying = health_mod.HealthReport(
        status=health_mod.STATUS_OK,
        http_status=200,
        dependencies=(
            health_mod.DependencyState("claim_ledger", health_mod.STATE_OK, "claimed ok"),
            health_mod.DependencyState("ticket_projection", health_mod.STATE_OK, "claimed ok"),
        ),
    )
    probes: Sequence[Callable[[Path, Any], health_mod.DependencyState]] = (
        _absent_probes("claim_ledger"),
        _absent_probes("ticket_projection"),
    )
    findings = health_mod.check_report(lying, root, probes=probes)
    results.append(
        (
            "health-lying-ok",
            any("'claim_ledger'" in f and "ok while" in f for f in findings),
            findings,
        )
    )
    return results


def _cmd_controls(args: argparse.Namespace) -> int:
    root = _root(args)
    results = _controls(root)
    failed = False
    for name, refused, findings in results:
        if refused:
            print(f"  refused -> {name}: {findings[0]}")
        else:
            failed = True
            print(f"  NOT-REFUSED -> {name}: {findings or 'no finding'} (a control passed)")
    if failed:
        print("openapi: FAIL — a provoked failure was not refused", file=sys.stderr)
        return 1
    print(f"openapi: OK — all {len(results)} provoked failures refused by name")
    return RC_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperclip-openapi", description=__doc__)
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    verbs = parser.add_subparsers(dest="verb", required=True)

    emit = verbs.add_parser("emit", help="write or print the OpenAPI document")
    emit.add_argument("--out", default="", help="output path (default: stdout)")
    emit.set_defaults(func=_cmd_emit)

    check = verbs.add_parser("check", help="verify the document and the committed artifact")
    check.set_defaults(func=_cmd_check)

    health = verbs.add_parser("health", help="read the real dependencies")
    health.set_defaults(func=_cmd_health)

    comp = verbs.add_parser("company", help="print the declared company mapping")
    comp.set_defaults(func=_cmd_company)

    controls = verbs.add_parser("controls", help="provoke every refusal, in process")
    controls.set_defaults(func=_cmd_controls)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
