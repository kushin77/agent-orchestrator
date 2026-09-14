#!/usr/bin/env python3
"""The diagrams blueprint projection over the paperclip.ing seam (issue #465).

ADR-0017 (issue #463, EPIC #461) froze *where a diagram signal lives on a
ticket*: it rides the ticket's structured ``evidence[]`` as the additive v2
receipt ``{kind, ref, result, checks}`` — **never** a new ``facets.diagrams``
(the closed set in ADR-0014 is ``lessons`` / ``raid`` / ``budget``) and **never**
a new ticket ``kind`` (a drift finding is a ``task`` whose evidence is the
finding). This adapter is the read-only producer of that signal.

It is deliberately narrow, exactly as ADR-0017 requires:

* **Read-only and one-way** — diagram -> decision -> ticket. The adapter writes
  nothing and takes no authority; the ticket stays the single join node
  (ADR-0014) and the fleet stays authoritative for dispatch, claims, budgets and
  audit (ADR-0012).
* **On the transport that already exists** — it reuses the seam ``Transport``
  from ``client.py``: ``FixtureTransport`` offline (the tests and the gate), and
  ``HttpTransport`` only on the live path, which no test or gate exercises.
* **Stdlib-only** — the whole module is a pure function over an HTTP-shaped
  payload, so it needs no third-party dependency. The one external shape it
  reuses is the frozen v2 receipt, validated against the seam schema through
  ``mapping.py``'s stdlib JSON-Schema subset validator.
* **Deterministic** — the same fixture yields a byte-identical plan.

Three projections, each a frozen v2 receipt:

| Signal | Receipt | ``ref`` |
|---|---|---|
| the rendered blueprint's identity | ``diagram-blueprint`` | the ``content_hash`` |
| the rendered blueprint's location | ``diagram-render`` | the rendered path |
| one drift Finding | ``diagram-drift`` | the Finding's resource id |

A Finding is drift **only when its declared and live attributes differ**. A
Finding whose attributes are *equal* is aligned and is never reported as drift —
a false-positive drift is a lying signal, so the adapter refuses a Finding that
claims ``status: drift`` while its attributes agree (naming the ``status``).

Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS is never a pass.

Usage: ``python3 integrations/paperclip/diagrams.py check --fixture F --company C``
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from integrations.paperclip import mapping as mapping_mod  # noqa: E402
from integrations.paperclip.client import (  # noqa: E402
    API_PREFIX,
    FixtureTransport,
    PaperclipClient,
    Transport,
)
from integrations.paperclip.model import PaperclipError  # noqa: E402

#: The projection resource family on the seam (company-scoped, like the others).
DIAGRAMS_RESOURCE = "diagrams"

#: The ADR this projection implements (cited, never restated as our own).
ADR = "ADR-0017"

#: The closed vocabulary a Finding's ``status`` is drawn from.
DRIFT_STATUSES: Tuple[str, ...] = ("aligned", "drift")

#: The three receipt kinds this projection emits. They are *receipt* kinds, not
#: ticket kinds: the closed ticket ``kind`` vocabulary (ADR-0014) is untouched.
RECEIPT_KIND_BLUEPRINT = "diagram-blueprint"
RECEIPT_KIND_RENDER = "diagram-render"
RECEIPT_KIND_DRIFT = "diagram-drift"

#: The frozen v2 receipt result vocabulary (ticket.schema.json #evidence_receipt).
RECEIPT_RESULTS: Tuple[str, ...] = ("PASS", "FAIL", "CANNOT-ASSESS")

#: The ``$defs`` entry of the ticket schema a receipt must validate against.
_EVIDENCE_RECEIPT_DEF = "evidence_receipt"


# ==========================================================================
# The seam reader (read-only; reuses the canonical Transport)
# ==========================================================================


def diagrams_path(company_id: str) -> str:
    """The company-scoped seam path for the diagrams blueprint projection."""
    if not company_id:
        raise ValueError("company_id is required for a company-scoped diagrams read")
    return f"{API_PREFIX}/companies/{company_id}/{DIAGRAMS_RESOURCE}"


def transport_of(client: Any) -> Transport:
    """The seam transport behind ``client`` — a ``PaperclipClient`` or a Transport.

    Accepting either proves the adapter is coupled to the seam's ``Transport``
    protocol alone, not to the client's method set: it reuses whatever transport
    the canonical client carries (a ``FixtureTransport`` offline, an
    ``HttpTransport`` live).
    """
    return getattr(client, "transport", client)


def read_blueprint(client: Any, *, company_id: str = "") -> Tuple[Optional[dict], str]:
    """Read the diagrams payload off the seam.

    Returns ``(payload, reason)``. On any seam failure ``payload`` is ``None`` and
    ``reason`` is a named refusal: a ``403``/``404``-class response names the HTTP
    status (``http-status``); a missing/unreadable fixture names the fixture.
    """
    company = company_id or str(getattr(client, "company_id", "") or "")
    if not company:
        return None, (
            "the diagrams projection is company-scoped but no company id was given "
            "(field: company_id)"
        )
    try:
        response = transport_of(client).request("GET", diagrams_path(company))
    except PaperclipError as exc:
        return None, (
            f"cannot read the diagrams blueprint over the seam — the seam answered "
            f"HTTP {exc.status} for {exc.path} (field: http-status)"
        )
    except (KeyError, ValueError, OSError) as exc:
        return None, (
            f"cannot read the diagrams blueprint over the seam — no fixture response "
            f"or an unreadable fixture ({exc}) (field: fixture)"
        )
    payload = response.body
    if not isinstance(payload, dict) or not payload:
        return None, (
            "the diagrams response carried no blueprint payload (field: fixture)"
        )
    return payload, ""


def open_fixture_client(fixture: Any, *, company_id: str) -> Tuple[Optional[Any], str]:
    """Build the offline client around ``fixture`` (a mapping or a JSON path).

    Returns ``(client, reason)``. An empty or missing fixture is a named refusal
    naming the fixture, never a silent default.
    """
    try:
        transport = FixtureTransport(fixture)
    except (OSError, ValueError) as exc:
        return None, f"cannot open the diagrams fixture ({exc}) (field: fixture)"
    return PaperclipClient(company_id=company_id, transport=transport), ""


# ==========================================================================
# The projection
# ==========================================================================


@dataclass(frozen=True)
class Report:
    """The projection: the evidence receipts plus refusals / cannot-assess."""

    source: Dict[str, Any] = field(default_factory=dict)
    evidence: Tuple[Dict[str, Any], ...] = ()
    findings: Tuple[str, ...] = ()
    cannot_assess: Tuple[str, ...] = ()

    def exit_code(self) -> int:
        """The tri-state contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""
        if self.cannot_assess:
            return 2
        if self.findings:
            return 1
        return 0


def _attribute_text(value: Any) -> str:
    """Canonical text for an attribute, so ``3`` and ``"3"`` compare equal."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, sort_keys=True)


def _receipt(kind: str, ref: str, result: str, checks: int) -> Dict[str, Any]:
    """The frozen v2 evidence receipt — exactly ``{kind, ref, result, checks}``."""
    return {"kind": kind, "ref": ref, "result": result, "checks": checks}


def project(client: Any, *, company_id: str = "") -> Report:
    """Project the diagrams blueprint state into frozen v2 evidence receipts.

    Pure and read-only: it issues exactly one ``GET`` over the seam and returns
    the receipts. A malformed Finding is refused *by name* (``resource`` /
    ``status``) and is never emitted as a signal.
    """
    payload, reason = read_blueprint(client, company_id=company_id)
    if payload is None:
        return Report(cannot_assess=(reason,))

    blueprint = payload.get("blueprint")
    if not isinstance(blueprint, dict) or not blueprint:
        return Report(
            cannot_assess=(
                "the diagrams response carried no `blueprint` object (field: blueprint)",
            )
        )
    content_hash = str(blueprint.get("content_hash") or "").strip()
    if not content_hash:
        return Report(
            findings=(
                "blueprint: the diagram blueprint carries no content_hash — a projection "
                "with no immutable identity cannot be a receipt (field: content_hash)",
            )
        )
    rendered = str(blueprint.get("rendered") or blueprint.get("ref") or "").strip()

    findings_raw = payload.get("findings")
    if findings_raw is None:
        findings_raw = []
    if not isinstance(findings_raw, list):
        return Report(
            cannot_assess=(
                "the diagrams response `findings` is not a list (field: findings)",
            )
        )

    company = company_id or str(getattr(client, "company_id", "") or "")
    source: Dict[str, Any] = {
        "adr": ADR,
        "authority": "none",
        "read_only": True,
        "resource": f"GET {diagrams_path(company)}",
        "blueprint": {"content_hash": content_hash, "rendered": rendered},
    }

    evidence: List[Dict[str, Any]] = []
    findings: List[str] = []
    drift_count = 0

    for index, raw in enumerate(findings_raw):
        where = f"findings[{index}]"
        if not isinstance(raw, dict):
            findings.append(
                f"{where}: a diagram Finding must be an object (field: resource)"
            )
            continue
        resource = raw.get("resource")
        if not isinstance(resource, str) or not resource.strip():
            findings.append(
                f"{where}: a diagram Finding carries no resource id "
                "(field: resource)"
            )
            continue
        resource = resource.strip()

        status = raw.get("status")
        if status not in DRIFT_STATUSES:
            findings.append(
                f"{where} ({resource}): status {status!r} is outside the closed "
                f"vocabulary {list(DRIFT_STATUSES)} (field: status)"
            )
            continue

        declared = _attribute_text(raw.get("declared"))
        live = _attribute_text(raw.get("live"))
        actual = "drift" if declared != live else "aligned"
        if status != actual:
            findings.append(
                f"{where} ({resource}): declared status {status!r} contradicts the "
                f"attributes (declared={declared!r} live={live!r} => {actual!r}) — a "
                "false-positive drift is a lying signal (field: status)"
            )
            continue

        evidence.append(
            {
                "ticket": raw.get("ticket") or None,
                "receipt": _receipt(
                    RECEIPT_KIND_DRIFT,
                    resource,
                    "FAIL" if actual == "drift" else "PASS",
                    1,
                ),
            }
        )
        if actual == "drift":
            drift_count += 1

    evidence.append(
        {
            "ticket": None,
            "receipt": _receipt(
                RECEIPT_KIND_BLUEPRINT,
                content_hash,
                "FAIL" if drift_count else "PASS",
                len(findings_raw),
            ),
        }
    )
    if rendered:
        evidence.append(
            {
                "ticket": None,
                "receipt": _receipt(RECEIPT_KIND_RENDER, rendered, "PASS", 1),
            }
        )

    evidence.sort(
        key=lambda item: (
            str(item.get("ticket") or ""),
            item["receipt"]["kind"],
            item["receipt"]["ref"],
        )
    )
    findings.sort()
    return Report(
        source=source,
        evidence=tuple(evidence),
        findings=tuple(findings),
        cannot_assess=(),
    )


def project_fixture(fixture: Any, *, company_id: str) -> Report:
    """Project a fixture mapping/path, building the offline client first."""
    client, reason = open_fixture_client(fixture, company_id=company_id)
    if client is None:
        return Report(cannot_assess=(reason,))
    return project(client, company_id=company_id)


# ==========================================================================
# Serialization (deterministic) + schema conformance
# ==========================================================================


def to_dict(report: Report) -> Dict[str, Any]:
    """The plan document: the receipts and the refusals, and nothing else."""
    return {
        "diagrams": dict(report.source),
        "evidence": [dict(item) for item in report.evidence],
        "findings": list(report.findings),
    }


def render(report: Report) -> str:
    """The canonical, byte-stable serialization the determinism check hashes."""
    return json.dumps(to_dict(report), indent=2, sort_keys=True)


def receipt_schema(root: Path) -> Dict[str, Any]:
    """The frozen v2 evidence-receipt sub-schema from the ticket contract."""
    ticket = mapping_mod.load_schema(Path(root), "ticket")
    return ticket["$defs"][_EVIDENCE_RECEIPT_DEF]


# ==========================================================================
# CLI
# ==========================================================================


def cmd_check(args: argparse.Namespace) -> int:
    """Project the fixture and print the tri-state report; return its exit code."""
    report = project_fixture(args.fixture, company_id=args.company)
    if report.exit_code() == 2:
        for reason in report.cannot_assess:
            print(f"check: CANNOT-ASSESS — {reason}", file=sys.stderr)
        return 2
    print(render(report))
    if report.findings:
        print(
            f"check: NOT-OK — {len(report.findings)} finding(s) over "
            f"{len(report.evidence)} projected receipt(s)",
            file=sys.stderr,
        )
        for finding in report.findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        return 1
    drifts = sum(
        1 for item in report.evidence if item["receipt"]["result"] == "FAIL"
    )
    print(
        f"check: OK — {len(report.evidence)} evidence receipt(s) projected "
        f"({drifts} drift signal(s)); no facet, no new ticket kind (ADR-0017)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperclip-diagrams",
        description="read-only diagrams blueprint projection over the paperclip.ing "
        "seam (issue #465, ADR-0017)",
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    check = sub.add_parser("check", help="project a fixture and report tri-state")
    check.add_argument(
        "--fixture", required=True, help="the FixtureTransport fixture (path or -)"
    )
    check.add_argument(
        "--company", default="", help="the company scope for the seam read"
    )
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
