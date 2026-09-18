"""The lane's own tri-state check, and its two read-only verbs.

``check`` is what ``scripts/check-erp-ops.sh`` runs, and it measures the claims
this lane makes rather than restating them:

1. **the declaration set** loads and matches its frozen schema, and the schema
   itself is refused if it asks for a keyword the repository's validator does
   not enforce;
2. **the document model** — both halves — satisfies ERP-02's asset contract
   (coverage both ways, state/docstatus parity, provenance completeness), and
   the catalogue's claims about the shipped assets resolve;
3. **every posting rule balances** on a probe, and the golden path's real
   ledger rows balance and tie out to zero across all accounts;
4. **the golden path is deterministic** — two runs, one digest — which is the
   property the whole lane rests on and the one a hidden clock would break;
5. **every declared refusal is provoked** by name, by
   ``negative_control.py``.

Exit contract (tear-state, repository convention): ``0`` OK, ``1`` NOT-OK,
``2`` CANNOT-ASSESS. A catalogue or model that will not load is a 2, never a 0:
a lane that cannot read its own declarations cannot report OK.
"""

from __future__ import annotations

import io
import sys
from typing import Dict, Optional, Sequence, TextIO

from . import catalog as catalog_mod
from . import flows, ledger, negative_control, provenance
from .model import Refused, load_model

__all__ = ["EXIT_CANNOT_ASSESS", "EXIT_NOT_OK", "EXIT_OK", "check", "definitions", "demo", "main"]

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The transaction documents the empty-posting probe is not about; kept here so
#: a reader can see the probe is a synthetic figure set, not a document.
PROBE: Dict[str, float] = {"net_total": 100.0, "taxes": 20.0, "total": 120.0}


def check(stream: TextIO = sys.stdout) -> int:
    """Measure the lane's own claims; see the module docstring for the steps."""
    good = 0
    bad = 0

    def ok(message: str) -> None:
        nonlocal good
        good += 1
        print(f"  OK    {message}", file=stream)

    def fail(message: str) -> None:
        nonlocal bad
        bad += 1
        print(f"  FAIL  {message}", file=stream)

    print("erp-ops check", file=stream)

    print("== 1. the declaration set ==", file=stream)
    try:
        catalog = catalog_mod.load()
    except Refused as refusal:
        fail(f"the catalogue does not load: {refusal}")
        print(
            "erp-ops check: CANNOT-ASSESS — the lane cannot read its own declarations",
            file=stream,
        )
        return EXIT_CANNOT_ASSESS
    ok(
        f"the catalogue validates against {catalog_mod.SCHEMA_FILE}: "
        f"{len(catalog.kinds)} kind(s), {len(catalog.postings)} posting rule(s), "
        f"flag {catalog.flag['id']} default {catalog.flag['default']}"
    )
    for name in sorted(catalog.vocabularies):
        ok(f"vocabulary {name}: {', '.join(catalog.vocabulary(name))}")

    print("== 2. the document model ==", file=stream)
    try:
        model = load_model()
    except Refused as refusal:
        fail(f"the model does not load: {refusal}")
        print(
            "erp-ops check: CANNOT-ASSESS — the lane cannot load its document model",
            file=stream,
        )
        return EXIT_CANNOT_ASSESS
    ok(
        f"{len(model.local_kinds())} declared famil(ies) and "
        f"{len(model.core_kinds())} ERP-02 famil(ies) load"
    )
    for problem in model.check_assets():
        fail(problem)
    if not model.check_assets():
        ok("the asset contract holds for both halves (coverage, state parity, provenance)")
    for problem in model.check_declarations(catalog):
        fail(problem)
    if not model.check_declarations(catalog):
        ok("the catalogue's claims about the shipped assets all resolve")
    harvest = provenance.audit()
    if harvest:
        for problem in harvest:
            fail(problem)
    else:
        ok("the harvest record accounts for every shipped schema (GR-10)")

    print("== 3. the posting rules ==", file=stream)
    for rule in catalog.postings:
        if ledger.balances(rule, PROBE):
            ok(f"posting rule {rule.key} balances on the probe {PROBE}")
        else:
            fail(f"posting rule {rule.key} does not balance on the probe {PROBE}")

    print("== 4. the golden path ==", file=stream)
    try:
        first = flows.golden_path(model, catalog)
        second = flows.golden_path(model, catalog)
    except Refused as refusal:
        fail(f"the golden path is refused: {refusal}")
        print("erp-ops check: NOT-OK — the golden path does not run", file=stream)
        return EXIT_NOT_OK
    except Exception as exc:  # a crash is a failure, never a pass
        fail(f"the golden path raised {type(exc).__name__}: {exc}")
        print("erp-ops check: NOT-OK — the golden path does not run", file=stream)
        return EXIT_NOT_OK
    left, right = flows.digest(first), flows.digest(second)
    if left == right:
        ok(f"two runs of the golden path are identical ({left[:16]})")
    else:
        fail(f"two runs of the golden path differ: {left[:16]} != {right[:16]}")
    accounts = first["workspace"]["accounts"]
    imbalance = round(sum(accounts.values()), 2)
    if imbalance == 0:
        ok(f"the books tie out: {len(accounts)} account(s) summing to 0.00")
    else:
        fail(f"the books do not tie out: the account balances sum to {imbalance}")
    rail = first["workspace"]["audit"]
    ok(f"the rail carries {len(rail)} entries, head {first['workspace']['audit_head'][:16]}")
    for step, document in sorted(first["purchase_cycle"]["documents"].items()):
        ok(f"purchase cycle {step}: {document['id']} in state {document['state']}")

    print("== 5. the refusals ==", file=stream)
    report = negative_control.run(stream=io.StringIO())
    for result in report.results:
        if result.verdict != "refused":
            fail(f"control {result.code} ({result.name}): {result.detail}")
    if report.ok:
        ok(report.coverage_line())
    else:
        fail(
            report.coverage_line()
            + (f" — no control for {list(report.missing)}" if report.missing else "")
            + (f" — undeclared code(s) {list(report.extra)}" if report.extra else "")
        )

    print("", file=stream)
    if bad:
        print(f"erp-ops check: NOT-OK — {bad} problem(s)", file=stream)
        return EXIT_NOT_OK
    print(f"erp-ops check: OK — {good} check(s) measured", file=stream)
    return EXIT_OK


def demo(stream: TextIO = sys.stdout) -> int:
    """Print the whole golden-path transcript as JSON."""
    try:
        transcript = flows.golden_path()
    except Refused as refusal:
        print(f"erp-ops demo: NOT-OK — {refusal}", file=stream)
        return EXIT_NOT_OK
    stream.write(flows.render(transcript))
    return EXIT_OK


def definitions(stream: TextIO = sys.stdout) -> int:
    """Print the validated declaration set as JSON."""
    try:
        catalog = catalog_mod.load()
    except Refused as refusal:
        print(f"erp-ops definitions: CANNOT-ASSESS — {refusal}", file=stream)
        return EXIT_CANNOT_ASSESS
    stream.write(flows.render(catalog.to_dict()))
    return EXIT_OK


USAGE = "usage: python3 -m integrations.erp.ops.cli <check|demo|definitions>"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verb = args[0] if args else "check"
    if verb == "check":
        return check()
    if verb == "demo":
        return demo()
    if verb == "definitions":
        return definitions()
    print(USAGE, file=sys.stderr)
    return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
