"""One provocation per refusal — and a driver that fails when one is missing.

The doctrine behind this file is the repository's own: **a validator that is
never shown to refuse is a formality.** Every schema, every workflow, every
rule in this lane has a happy path, and a suite that only walks the happy path
passes just as green when the rule has been replaced by ``return None``.

So all :data:`~.model.REFUSALS` are provoked here. Each provocation declares the
code it must raise and a **needle** its message must contain — the offender, so
"something was refused" is not accepted in place of "the work order was refused,
by its id, for already being completed". Three properties make the report worth
more than a list of assertions:

* **coverage is computed, not claimed.** :func:`run` compares the provoked code
  set against ``REFUSALS`` and reports every code with no control and every
  control for no declared code.
* **the driver can fail.** ``tests/test_negative_control.py`` neuters a real
  rule with ``monkeypatch`` and requires :func:`run` to go non-zero.
* **a provocation that is not refused is reported as such.** An exception that
  is not a :class:`~.model.Refused` is reported as NOT-REFUSED with the
  exception named, rather than being swallowed — that verdict is what
  ``scripts/check-erp-ops.sh`` reads when it runs the driver against a mutant
  with the receipt-without-order rule removed.

Run it with ``python3 -m integrations.erp.ops.negative_control``; it exits 0
only when every declared refusal is provoked, by name.

---knowledge---
module_id: integrations.erp.ops.negative_control
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Provocation, provocations, Result, Report, run, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, TextIO, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.erp.ops import catalog as catalog_mod  # noqa: E402
from integrations.erp.ops import documents, flows, ledger, manufacturing, procurement, provenance  # noqa: E402
from integrations.erp.ops.model import REFUSALS, Refused, load_model  # noqa: E402
from integrations.erp.ops.workspace import Workspace  # noqa: E402

__all__ = ["Provocation", "Report", "Result", "main", "provocations", "run"]

#: The ids the provocation fixtures drive the purchase cycle with.
IDS: Mapping[str, str] = {
    "rfq": "RFQ-0001",
    "order": "PO-0001",
    "receipt": "PR-0001",
    "invoice": "PI-0001",
}

#: The instants those fixtures use — the same deterministic clock the golden
#: path uses, so a provocation and the transcript cannot drift apart.
AT: Mapping[str, str] = {
    "rfq": flows.T["rfq"],
    "order": flows.T["order"],
    "receipt": flows.T["receipt"],
    "invoice": flows.T["invoice"],
}

LINES: Sequence[Mapping[str, Any]] = (
    {"item_code": "RAW-A", "qty": 40, "rate": 5.0},
    {"item_code": "RAW-B", "qty": 60, "rate": 2.0},
)


# --- fixtures ---------------------------------------------------------------


def _space() -> Workspace:
    """A workspace with the masters seeded and nothing else."""
    return flows.workspace()


def _cycle(space: Optional[Workspace] = None) -> Workspace:
    """A workspace carrying a completed purchase cycle."""
    space = space if space is not None else _space()
    procurement.purchase_cycle(
        space,
        ids=IDS,
        supplier="SUP-1",
        lines=LINES,
        warehouse=flows.RAW_WAREHOUSE,
        at=AT,
        tax_rate=0.10,
    )
    return space


def _boms(space: Workspace) -> Workspace:
    """A workspace carrying an active sub-assembly and finished-good bill."""
    for bom_id, item, components in (
        ("BOM-SUB-1", "SUB-1", [{"item_code": "RAW-A", "qty": 2}, {"item_code": "RAW-B", "qty": 1}]),
        ("BOM-FG-1", "FG-1", [{"item_code": "SUB-1", "qty": 1}, {"item_code": "RAW-B", "qty": 3}]),
    ):
        manufacturing.define_bom(
            space,
            bom_id=bom_id,
            item=item,
            quantity=1,
            components=components,
            at=flows.T["bom"],
        )
        manufacturing.activate_bom(space, bom_id=bom_id, at=flows.T["bom"])
    return space


def _valid_rfq(**overrides: Any) -> Dict[str, Any]:
    """A request for quotation that validates, with any field overridden."""
    document: Dict[str, Any] = {
        "doctype": "rfq",
        "id": "RFQ-NC",
        "state": "draft",
        "docstatus": 0,
        "company": "COMPANY-1",
        "currency": "EUR",
        "transaction_date": "2026-09-15",
        "suppliers": ["SUP-1"],
        "lines": [{"item_code": "RAW-A", "qty": 1, "rate": 5.0}],
    }
    document.update(overrides)
    return document


def _draft(kind: str, **fields: Any) -> Dict[str, Any]:
    """A document of ``kind`` in draft, created directly rather than through a flow."""
    document: Dict[str, Any] = {"doctype": kind, "state": "draft", "docstatus": 0}
    document.update(fields)
    return document


def _work_order(**overrides: Any) -> Dict[str, Any]:
    """A work order that validates, with any field overridden."""
    document: Dict[str, Any] = {
        "doctype": "work-order",
        "id": "WO-NC",
        "state": "submitted",
        "docstatus": 1,
        "company": "COMPANY-1",
        "bom": "BOM-SUB-1",
        "item": "SUB-1",
        "quantity": 1,
        "from_warehouse": flows.RAW_WAREHOUSE,
        "to_warehouse": flows.FINISHED_WAREHOUSE,
    }
    document.update(overrides)
    return document


# --- the provocations -------------------------------------------------------


@dataclass(frozen=True)
class Provocation:
    """One declared refusal, the input that provokes it, and the offender."""

    name: str
    code: str
    needle: str
    check: Callable[[], None]


def _refuse_with_finding(code: str, findings: Iterable[Any]) -> None:
    """Turn a whole-set finding into the refusal the driver checks.

    A chain check reports rather than raises (it inspects a set), so a
    provocation that tampers with the rail has to fail *here* when nothing was
    reported — otherwise a neutered chain check would look like a provocation
    that "passed" because it raised nothing.
    """
    for finding in findings:
        if getattr(finding, "code", None) == code:
            raise Refused(code, getattr(finding, "detail", ""))
    raise AssertionError(f"no {code} finding was reported")


def provocations() -> Tuple[Provocation, ...]:
    """Every provocation, each building its own workspace."""

    # --- declaration + schema layer ---------------------------------------

    def declarations_invalid() -> None:
        with tempfile.TemporaryDirectory(prefix="ao-erp-ops-nc-") as scratch:
            root = Path(scratch)
            shutil.copytree(catalog_mod.CATALOG_ROOT, root, dirs_exist_ok=True)
            target = root / catalog_mod.CATALOG_FILE
            document = json.loads(target.read_text(encoding="utf-8"))
            document.pop("lane", None)
            target.write_text(json.dumps(document), encoding="utf-8")
            catalog_mod.load(root)

    def unsupported_schema_keyword() -> None:
        catalog_mod.assert_supported_schema(
            {"type": "object", "minProperties": 1}, where="scratch.schema.json"
        )

    def schema_violation() -> None:
        documents.parse(
            load_model(), _valid_rfq(docstatus=7), kind="rfq", where="RFQ-NC"
        )

    def unknown_kind() -> None:
        documents.parse(
            load_model(), {"doctype": "sprocket", "id": "SPR-1"}, where="SPR-1"
        )

    def unknown_vocabulary() -> None:
        catalog_mod.load().vocabulary("sprocket-purposes")

    def unknown_vocabulary_term() -> None:
        catalog_mod.load().term("stock-purposes", "material_teleport")

    # --- workflow ---------------------------------------------------------

    def unknown_state() -> None:
        document = dict(_cycle().get(IDS["rfq"]))
        document["state"] = "sprocketed"
        load_model().advance(document, "submit")

    def unknown_action() -> None:
        space = _space()
        document = space.create(_valid_rfq(), at=AT["rfq"], actor="nc")
        load_model().advance(document, "launch")

    def illegal_transition() -> None:
        load_model().validate_move("rfq", "draft", "completed")

    # --- envelope + store -------------------------------------------------

    def unknown_document() -> None:
        _space().get("NOPE-1")

    def duplicate_id() -> None:
        space = _space()
        space.create(dict(flows.ITEMS[0]), at=flows.T["masters"], actor="nc")

    def missing_field() -> None:
        documents.parse(load_model(), {"doctype": "rfq", "id": "RFQ-X"}, where="RFQ-X")

    def unknown_field() -> None:
        documents.parse(
            load_model(), _valid_rfq(planet="Mars"), kind="rfq", where="RFQ-NC"
        )

    def invalid_value() -> None:
        documents.parse(
            load_model(), _valid_rfq(suppliers="SUP-1"), kind="rfq", where="RFQ-NC"
        )

    # --- procurement ------------------------------------------------------

    def unknown_supplier() -> None:
        _space().party("CUST-1", expect="supplier")

    def unknown_item() -> None:
        _space().item("RAW-Z")

    def receipt_without_order() -> None:
        # The mutation harness in scripts/check-erp-ops.sh removes the rule this
        # provokes, so this check must fail loudly when the rule is gone.
        procurement.receive_order(
            _space(),
            receipt_id="PR-NC",
            order_id="PO-9999",
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        )

    def purchase_order_not_submitted() -> None:
        space = _space()
        space.create(
            _draft(
                "purchase-order",
                id="PO-DRAFT-1",
                company="COMPANY-1",
                currency="EUR",
                transaction_date="2026-09-15",
                supplier="SUP-1",
                lines=[{"item_code": "RAW-A", "qty": 5, "rate": 5.0}],
            ),
            at=AT["order"],
            actor="nc",
        )
        procurement.receive_order(
            space,
            receipt_id="PR-NC",
            order_id="PO-DRAFT-1",
            lines=[{"item_code": "RAW-A", "qty": 1}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        )

    def over_receipt() -> None:
        procurement.receive_order(
            _cycle(),
            receipt_id="PR-NC",
            order_id=IDS["order"],
            lines=[{"item_code": "RAW-A", "qty": 100}],
            warehouse=flows.RAW_WAREHOUSE,
            at=AT["receipt"],
        )

    def already_converted() -> None:
        procurement.convert_rfq(
            _cycle(), rfq_id=IDS["rfq"], order_id="PO-0002", at=AT["order"]
        )

    def rfq_not_submitted() -> None:
        space = _space()
        space.create(
            _valid_rfq(id="RFQ-DRAFT-1"), at=AT["rfq"], actor="nc"
        )
        procurement.convert_rfq(
            space, rfq_id="RFQ-DRAFT-1", order_id="PO-0009", at=AT["order"]
        )

    # --- manufacturing ----------------------------------------------------

    def unknown_bom() -> None:
        manufacturing.raise_work_order(
            _space(),
            wo_id="WO-NC",
            bom_id="BOM-NOPE",
            quantity=1,
            at=flows.T["wo_sub"],
            from_warehouse=flows.RAW_WAREHOUSE,
            to_warehouse=flows.FINISHED_WAREHOUSE,
        )

    def bom_not_submitted() -> None:
        space = _space()
        manufacturing.define_bom(
            space,
            bom_id="BOM-DRAFT-1",
            item="SUB-1",
            quantity=1,
            components=[{"item_code": "RAW-A", "qty": 1}],
            at=flows.T["bom"],
        )
        manufacturing.explode(space, space.get("BOM-DRAFT-1"), quantity=1)

    def bom_cycle() -> None:
        space = _boms(_space())
        manufacturing.define_bom(
            space,
            bom_id="BOM-CYCLE-1",
            item="SUB-1",
            quantity=1,
            components=[{"item_code": "SUB-1", "qty": 1}],
            at=flows.T["bom"],
        )
        manufacturing.activate_bom(space, bom_id="BOM-CYCLE-1", at=flows.T["bom"])
        manufacturing.explode(space, space.get("BOM-CYCLE-1"), quantity=1)

    def unknown_work_order() -> None:
        manufacturing.complete_work_order(
            _boms(_cycle()), wo_id="WO-NOPE", at=flows.T["wo_sub"]
        )

    def work_order_not_submitted() -> None:
        space = _boms(_cycle())
        space.create(_work_order(id="WO-DRAFT-1", state="draft", docstatus=0), at=flows.T["wo_sub"], actor="nc")
        manufacturing.complete_work_order(
            space, wo_id="WO-DRAFT-1", at=flows.T["wo_sub"]
        )

    def work_order_already_completed() -> None:
        # BOM-SUB-1 consumes only raw material the cycle has already bought, so
        # the FIRST completion succeeds and the second is the thing being
        # provoked — a provocation that failed on stock would be asserting the
        # wrong refusal.
        space = _boms(_cycle())
        order = manufacturing.raise_work_order(
            space,
            wo_id="WO-0001",
            bom_id="BOM-SUB-1",
            quantity=10,
            at=flows.T["wo_fg"],
            from_warehouse=flows.RAW_WAREHOUSE,
            to_warehouse=flows.FINISHED_WAREHOUSE,
        )
        manufacturing.complete_work_order(
            space, wo_id=order["id"], at=flows.T["wo_fg"]
        )
        manufacturing.complete_work_order(
            space, wo_id=order["id"], at=flows.T["wo_fg"]
        )

    def insufficient_stock() -> None:
        space = _boms(_cycle())
        order = manufacturing.raise_work_order(
            space,
            wo_id="WO-0001",
            bom_id="BOM-SUB-1",
            quantity=1000,
            at=flows.T["wo_fg"],
            from_warehouse=flows.RAW_WAREHOUSE,
            to_warehouse=flows.FINISHED_WAREHOUSE,
        )
        manufacturing.complete_work_order(
            space, wo_id=order["id"], at=flows.T["wo_fg"]
        )

    def bom_item_mismatch() -> None:
        space = _boms(_cycle())
        space.create(
            _work_order(id="WO-MISMATCH", item="FG-1"), at=flows.T["wo_fg"], actor="nc"
        )
        manufacturing.complete_work_order(
            space, wo_id="WO-MISMATCH", at=flows.T["wo_fg"]
        )

    # --- posting ----------------------------------------------------------

    def unbalanced_posting() -> None:
        ledger.build_gl_posting(
            load_model(),
            catalog_mod.load(),
            "material-issue",
            {"id": "STE-NC", "company": "COMPANY-1", "lines": []},
            posting_id="GL-NC",
            currency="EUR",
            items={},
        )

    def unknown_posting_rule() -> None:
        catalog_mod.load().posting("material-teleport")

    # --- audit rail -------------------------------------------------------

    def audit_broken() -> None:
        rail = _cycle().rail
        tampered = dataclasses.replace(
            rail,
            entries=(dataclasses.replace(rail.entries[0], detail="tampered"),)
            + rail.entries[1:],
        )
        _refuse_with_finding("audit-broken", tampered.verify())

    # --- provenance -------------------------------------------------------

    def provenance_invalid() -> None:
        provenance.enforce_harvest(
            {"upstream_doctype": None, "harvest": ["something"]}, "scratch.schema.json"
        )

    def code_copied() -> None:
        provenance.enforce_catalogue(
            {
                "upstream": "frappe/erpnext",
                "license": "GPL-3.0",
                "pattern_source_only": True,
                "code_copied": True,
                "retrieved": "2026-09-15",
                "source_doc": "docs/ERP-MODULE-GAP-ANALYSIS.md",
            },
            "scratch-catalog",
        )

    return (
        Provocation("a malformed catalogue", "declarations-invalid", "does not match", declarations_invalid),
        Provocation("a schema using an unenforceable keyword", "unsupported-schema-keyword", "minProperties", unsupported_schema_keyword),
        Provocation("a document outside its family schema", "schema-violation", "is not one of", schema_violation),
        Provocation("a family no schema declares", "unknown-kind", "sprocket", unknown_kind),
        Provocation("a vocabulary the catalogue does not declare", "unknown-vocabulary", "sprocket-purposes", unknown_vocabulary),
        Provocation("a term outside its vocabulary", "unknown-vocabulary-term", "material_teleport", unknown_vocabulary_term),
        Provocation("a state the workflow lacks", "unknown-state", "sprocketed", unknown_state),
        Provocation("an action the state does not declare", "unknown-action", "launch", unknown_action),
        Provocation("a move between unconnected states", "illegal-transition", "completed", illegal_transition),
        Provocation("an id nothing holds", "unknown-document", "NOPE-1", unknown_document),
        Provocation("an id already spent", "duplicate-id", "RAW-A", duplicate_id),
        Provocation("a required field absent", "missing-field", "must declare", missing_field),
        Provocation("a field the family does not declare", "unknown-field", "planet", unknown_field),
        Provocation("a field of the wrong shape", "invalid-value", "suppliers must be array", invalid_value),
        Provocation("a party that is not a supplier", "unknown-supplier", "CUST-1", unknown_supplier),
        Provocation("an item outside the master", "unknown-item", "RAW-Z", unknown_item),
        Provocation("a receipt with no order behind it", "receipt-without-order", "PO-9999", receipt_without_order),
        Provocation("a receipt against a draft order", "purchase-order-not-submitted", "PO-DRAFT-1", purchase_order_not_submitted),
        Provocation("a receipt beyond what was ordered", "over-receipt", "RAW-A", over_receipt),
        Provocation("a second conversion of one RFQ", "already-converted", "RFQ-0001", already_converted),
        Provocation("a conversion of an unsent RFQ", "rfq-not-submitted", "RFQ-DRAFT-1", rfq_not_submitted),
        Provocation("a work order citing no bill", "unknown-bom", "BOM-NOPE", unknown_bom),
        Provocation("an explosion of a draft bill", "bom-not-submitted", "BOM-DRAFT-1", bom_not_submitted),
        Provocation("a bill that contains its own item", "bom-cycle", "SUB-1", bom_cycle),
        Provocation("a work order the store does not hold", "unknown-work-order", "WO-NOPE", unknown_work_order),
        Provocation("completing an unstarted order", "work-order-not-submitted", "WO-DRAFT-1", work_order_not_submitted),
        Provocation("completing one order twice", "work-order-already-completed", "WO-0001", work_order_already_completed),
        Provocation("consuming stock that is not there", "insufficient-stock", "RAW-A", insufficient_stock),
        Provocation("an order that produces another item", "bom-item-mismatch", "FG-1", bom_item_mismatch),
        Provocation("a ledger row with no counter-row", "unbalanced-posting", "a double entry needs two", unbalanced_posting),
        Provocation("a family with no posting rule", "unknown-posting-rule", "material-teleport", unknown_posting_rule),
        Provocation("a tampered audit rail", "audit-broken", "does not re-derive", audit_broken),
        Provocation("a harvest record with no upstream doctype", "provenance-invalid", "upstream_doctype", provenance_invalid),
        Provocation("a harvest record claiming copied code", "code-copied", "copied upstream code", code_copied),
    )


# --- the driver -------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    """What happened when one provocation ran."""

    name: str
    code: str
    verdict: str
    detail: str = ""


@dataclass(frozen=True)
class Report:
    """The driver's whole verdict: every provocation, and the coverage."""

    results: Tuple[Result, ...]
    missing: Tuple[str, ...]
    extra: Tuple[str, ...]

    @property
    def refused(self) -> Tuple[str, ...]:
        return tuple(
            result.code for result in self.results if result.verdict == "refused"
        )

    @property
    def ok(self) -> bool:
        return not self.missing and not self.extra and all(
            result.verdict == "refused" for result in self.results
        )

    def coverage_line(self) -> str:
        return (
            f"{len(self.refused)} of {len(REFUSALS)} declared refusal(s) provoked"
        )


def run(stream: Optional[TextIO] = None) -> Report:
    """Provoke every declared refusal and report what happened to each."""
    out: TextIO = stream if stream is not None else sys.stdout
    print("erp-ops negative controls", file=out)
    results: List[Result] = []
    for provocation in provocations():
        try:
            provocation.check()
        except Refused as refusal:
            if refusal.reason != provocation.code:
                verdict, detail = (
                    "wrong-code",
                    f"expected {provocation.code}, got {refusal.reason}: {refusal.detail}",
                )
            elif provocation.needle and provocation.needle not in refusal.detail:
                verdict, detail = (
                    "unnamed",
                    f"refused without naming {provocation.needle!r}: {refusal.detail}",
                )
            else:
                verdict, detail = "refused", refusal.detail
        except Exception as exc:  # any other failure is NOT a refusal
            verdict, detail = "not-refused", f"{type(exc).__name__}: {exc}"
        results.append(Result(provocation.name, provocation.code, verdict, detail))
        label = {
            "refused": "OK          ",
            "wrong-code": "WRONG-CODE  ",
            "unnamed": "UNNAMED     ",
            "not-refused": "NOT-REFUSED ",
        }[verdict]
        print(f"  {label} {provocation.code}  {provocation.name}: {detail}", file=out)

    provoked = {result.code for result in results}
    missing = tuple(sorted(REFUSALS - provoked))
    extra = tuple(sorted(provoked - REFUSALS))
    report = Report(results=tuple(results), missing=missing, extra=extra)
    if report.ok:
        print(f"  OK          {report.coverage_line()}", file=out)
    else:
        print(
            f"  FAIL        {report.coverage_line()}"
            + (f" — no control for {list(missing)}" if missing else "")
            + (f" — control(s) for undeclared code(s) {list(extra)}" if extra else "")
            + (
                " — "
                + str(
                    [
                        f"{result.code}: {result.verdict}"
                        for result in report.results
                        if result.verdict != "refused"
                    ]
                )
                if any(result.verdict != "refused" for result in report.results)
                else ""
            ),
            file=out,
        )
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    report = run()
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
