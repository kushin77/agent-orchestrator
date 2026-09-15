"""The negative controls for every refusal this package can raise (issue #648).

Issue #648's acceptance criterion 4 requires "unit + integration tests with
negative controls", and this repository's doctrine says why: **a validator that is
never shown to refuse is a formality.** A state machine, a ledger and a quantity
rule all have a happy path, and a suite that only walks the happy path passes just
as green when the validator has been replaced by ``return True``.

So every refusal in :data:`~.model.REFUSALS` is **provoked here**, and the driver
fails if any provocation is not refused, is refused with the *wrong* code, or is
refused without naming the offender. Three properties make that worth more than a
list of assertions:

* **coverage is computed, not claimed.** :func:`run` compares the provoked code
  set against ``REFUSALS`` and fails when they diverge — in *both* directions, so
  a refusal added without a control fails the driver and a code in the vocabulary
  that nothing can raise fails it too (which is how ``reversal-mismatch`` was
  caught: under a total inversion the net of a cancelled document is zero *by
  construction*, so a "did the reversal net to zero" refusal could never fire and
  was removed rather than shipped as a check that cannot fail);
* **the driver can fail.** The lane's gate neuters a real control — the reversal
  of a stock movement — in a scratch copy of the tree and requires *this* driver
  to turn non-zero, so a driver that cannot fail cannot pass the gate;
* **the offender must be named.** Each provocation declares the substring its
  refusal has to contain, so "something was refused" is not accepted in place of
  "the delivery was refused for citing an item the item master does not carry".

Run it directly with ``python3 integrations/erp/tx/negative_control.py``; it exits
0 only when every provocation is refused by name.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, TextIO, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.erp.tx import audit, indexer, ledger, spine, stock  # noqa: E402
from integrations.erp.tx.definitions import DefinitionSet  # noqa: E402
from integrations.erp.tx.definitions import load as load_definitions  # noqa: E402
from integrations.erp.tx.ledger import PostingPolicy  # noqa: E402
from integrations.erp.tx.model import REFUSALS, Refused, TxDocument  # noqa: E402
from integrations.erp.tx.spine import Scenario, Workspace  # noqa: E402


@dataclass(frozen=True)
class Provocation:
    """One refusal, the input that provokes it, and the offender it must name."""

    name: str
    code: str
    needle: str
    check: Callable[[], None]


def _refuse_with_finding(code: str, findings: Sequence[object]) -> None:
    """Turn an observed whole-set finding into the refusal the driver checks.

    A chain check reports rather than raises (it inspects a set), so a provocation
    that tampers with the chain has to fail *here* when nothing was reported —
    otherwise a neutered chain check would look like a provocation that "passed"
    because it raised nothing.
    """
    for finding in findings:
        if getattr(finding, "code", None) == code:
            raise Refused(code, str(getattr(finding, "detail", "")))
    raise AssertionError(f"no {code} finding was reported")


def _cycle_up_to_order(defs: DefinitionSet) -> Tuple[Workspace, Scenario]:
    """A workspace with the cycle's first document submitted and the second drafted."""
    scenario = spine.golden_scenario()
    space = spine.workspace(defs, scenario)
    chain = defs.chain
    at = spine.TIMELINE["quoted"]
    space = spine.draft(space, chain[0], scenario.ids[0], scenario.bodies[0], at=at)
    space = spine.submit(space, scenario.ids[0], at=at)
    space = spine.raise_document(
        space, chain[1], scenario.ids[1], scenario.ids[0], scenario.bodies[1], at=at
    )
    return space, scenario


def _cycle_with_delivery(defs: DefinitionSet) -> Tuple[Workspace, Scenario]:
    """A workspace through the delivery: an order submitted and its delivery live."""
    space, scenario = _cycle_up_to_order(defs)
    chain = defs.chain
    at = spine.TIMELINE["delivered"]
    space = spine.submit(space, scenario.ids[1], at=at)
    space = spine.deliver(
        space, scenario.ids[2], scenario.ids[1], scenario.bodies[2], at=at
    )
    return space, scenario


def _body(scenario: Scenario, position: int, **overrides: object) -> Dict[str, object]:
    body = dict(scenario.bodies[position])
    body.update(overrides)
    return body


def provocations(defs: DefinitionSet) -> Tuple[Provocation, ...]:
    """Every provocation, built against the resolved definitions."""
    scenario = spine.golden_scenario()
    chain = defs.chain
    at = spine.TIMELINE["quoted"]

    def index_unavailable() -> None:
        indexer.load_index(Path("/nonexistent-ao-erp-tx"))

    def catalogue_invalid() -> None:
        directory = tempfile.mkdtemp(prefix="ao-erp-tx-nc-")
        path = Path(directory) / "broken.json"
        path.write_text(json.dumps({"id": "broken"}), encoding="utf-8")
        indexer.read_lane_document(path, where="catalogue/broken.json")

    def undeclared_document() -> None:
        indexer.catalogue_documents(issue=999999)

    def definitions_invalid() -> None:
        dataclasses.replace(defs, stock_kinds=()).stock_kind()

    def unknown_document_kind() -> None:
        defs.require("sprocket")

    def not_drivable() -> None:
        defs.require("stock-entry")

    def duplicate_id() -> None:
        space = spine.workspace(defs, scenario)
        space = spine.draft(space, chain[0], scenario.ids[0], _body(scenario, 0), at=at)
        spine.draft(space, chain[0], scenario.ids[0], _body(scenario, 0), at=at)

    def invalid_value() -> None:
        space = spine.workspace(defs, scenario)
        spine.draft(
            space,
            chain[0],
            scenario.ids[0],
            _body(scenario, 0, state="submitted"),
            at=at,
        )

    def missing_field() -> None:
        TxDocument.of(chain[-1], "INV-NC", {"state": "submitted"})

    def schema_violation() -> None:
        defs.validate(chain[0], {"doctype": chain[0], "id": "QUO-NC", "state": "draft"})

    def unknown_account_role() -> None:
        PostingPolicy(accounts={}).resolve("receivable")

    def unbalanced_posting() -> None:
        invoice = TxDocument.of(
            chain[-1],
            "INV-NC",
            {
                "doctype": chain[-1],
                "id": "INV-NC",
                "state": "submitted",
                "docstatus": 1,
                "lines": [{"item_code": "ITEM-GOLDEN", "qty": 5, "rate": 20.0}],
                "taxes": [{"account": "TAX-PAYABLE", "amount": 5.0}],
                "total": 999.0,
            },
        )
        ledger.line_totals(invoice)

    def unknown_action() -> None:
        audit.Rail().append(
            at=at, actor="nc", action="signed", kind=chain[0], ref="QUO-NC"
        )

    def audit_broken() -> None:
        rail = audit.Rail().append(
            at=at, actor="nc", action="draft", kind=chain[0], ref="QUO-NC"
        )
        tampered = [entry.to_dict() for entry in rail]
        tampered[0]["note"] = "rewritten"
        _refuse_with_finding("audit-broken", audit.Rail.from_list(tampered).verify())

    def already_submitted() -> None:
        space = spine.workspace(defs, scenario)
        space = spine.draft(space, chain[0], scenario.ids[0], _body(scenario, 0), at=at)
        space = spine.submit(space, scenario.ids[0], at=at)
        spine.submit(space, scenario.ids[0], at=at)

    def cancelled_document() -> None:
        space = spine.workspace(defs, scenario)
        space = spine.draft(space, chain[0], scenario.ids[0], _body(scenario, 0), at=at)
        space = spine.submit(space, scenario.ids[0], at=at)
        space = spine.cancel(space, scenario.ids[0], at=at)
        spine.submit(space, scenario.ids[0], at=at)

    def already_reversed() -> None:
        space = spine.workspace(defs, scenario)
        space = spine.draft(space, chain[0], scenario.ids[0], _body(scenario, 0), at=at)
        space = spine.submit(space, scenario.ids[0], at=at)
        space = spine.cancel(space, scenario.ids[0], at=at)
        spine.cancel(space, scenario.ids[0], at=at)

    def not_submitted() -> None:
        space, scenario_state = _cycle_up_to_order(defs)
        spine.deliver(
            space, scenario_state.ids[2], scenario_state.ids[1], _body(scenario, 2), at=at
        )

    def unknown_document() -> None:
        spine.workspace(defs, scenario).get("QUO-NOPE")

    def live_dependant() -> None:
        space, scenario_state = _cycle_with_delivery(defs)
        spine.cancel(space, scenario_state.ids[1], at=at)

    def wrong_document() -> None:
        space, scenario_state = _cycle_up_to_order(defs)
        space = spine.submit(space, scenario_state.ids[1], at=at)
        spine.raise_document(
            space,
            chain[2],
            "DN-NC",
            scenario_state.ids[0],
            _body(scenario, 2),
            at=at,
        )

    def currency_mismatch() -> None:
        space, scenario_state = _cycle_up_to_order(defs)
        space = spine.submit(space, scenario_state.ids[1], at=at)
        spine.deliver(
            space,
            "DN-NC",
            scenario_state.ids[1],
            _body(scenario, 2, currency="EUR"),
            at=at,
        )

    def unknown_item() -> None:
        stock.movements_for(
            _body(scenario, 2),
            kind=chain[2],
            reference="DN-NC",
            items={"OTHER": {"is_stock_item": True}},
            warehouses=scenario.warehouses,
            at=at,
            sign=-1,
        )

    def not_a_stock_item() -> None:
        stock.movements_for(
            _body(scenario, 2),
            kind=chain[2],
            reference="DN-NC",
            items={scenario.item["id"]: {"is_stock_item": False}},
            warehouses=scenario.warehouses,
            at=at,
            sign=-1,
        )

    def unknown_warehouse() -> None:
        stock.movements_for(
            _body(scenario, 2, warehouse="WH-NOPE"),
            kind=chain[2],
            reference="DN-NC",
            items={scenario.item["id"]: scenario.item},
            warehouses=scenario.warehouses,
            at=at,
            sign=-1,
        )

    def over_delivery() -> None:
        space, scenario_state = _cycle_up_to_order(defs)
        space = spine.submit(space, scenario_state.ids[1], at=at)
        spine.deliver(
            space,
            "DN-NC",
            scenario_state.ids[1],
            _body(
                scenario,
                2,
                lines=[{"item_code": scenario.item["id"], "qty": 6, "rate": 20.0}],
            ),
            at=at,
        )

    def over_invoice() -> None:
        space, scenario_state = _cycle_with_delivery(defs)
        spine.invoice(
            space,
            "INV-NC",
            scenario_state.ids[2],
            _body(
                scenario,
                3,
                lines=[{"item_code": scenario.item["id"], "qty": 6, "rate": 20.0}],
            ),
            at=at,
        )

    def illegal_transition() -> None:
        defs.action_reaching(
            chain[1], "completed", lambda state: state.docstatus == 1 and not state.terminal
        )

    return (
        Provocation("index-unavailable", "index-unavailable", "does not exist", index_unavailable),
        Provocation("catalogue-invalid", "catalogue-invalid", "broken.json", catalogue_invalid),
        Provocation("undeclared-document", "undeclared-document", "999999", undeclared_document),
        Provocation("definitions-invalid", "definitions-invalid", "stock", definitions_invalid),
        Provocation("unknown-document-kind", "unknown-document-kind", "sprocket", unknown_document_kind),
        Provocation("not-drivable", "not-drivable", "stock-entry", not_drivable),
        Provocation("duplicate-id", "duplicate-id", "QUO-1001", duplicate_id),
        Provocation("invalid-value", "invalid-value", "may not set state", invalid_value),
        Provocation("missing-field", "missing-field", "docstatus", missing_field),
        Provocation("schema-violation", "schema-violation", "quotation", schema_violation),
        Provocation("unknown-account-role", "unknown-account-role", "receivable", unknown_account_role),
        Provocation("unbalanced-posting", "unbalanced-posting", "INV-NC", unbalanced_posting),
        Provocation("unknown-action", "unknown-action", "signed", unknown_action),
        Provocation("audit-broken", "audit-broken", "does not reproduce", audit_broken),
        Provocation("already-submitted", "already-submitted", "submitted once", already_submitted),
        Provocation("cancelled-document", "cancelled-document", "cannot be submitted", cancelled_document),
        Provocation("already-reversed", "already-reversed", "already cancelled", already_reversed),
        Provocation("not-submitted", "not-submitted", "must be submitted", not_submitted),
        Provocation("unknown-document", "unknown-document", "QUO-NOPE", unknown_document),
        Provocation("live-dependant", "live-dependant", "DN-1001", live_dependant),
        Provocation("wrong-document", "wrong-document", "DN-NC", wrong_document),
        Provocation("currency-mismatch", "currency-mismatch", "EUR", currency_mismatch),
        Provocation("unknown-item", "unknown-item", "OTHER", unknown_item),
        Provocation("not-a-stock-item", "not-a-stock-item", "ITEM-GOLDEN", not_a_stock_item),
        Provocation("unknown-warehouse", "unknown-warehouse", "WH-NOPE", unknown_warehouse),
        Provocation("over-delivery", "over-delivery", "outstanding", over_delivery),
        Provocation("over-invoice", "over-invoice", "unbilled", over_invoice),
        Provocation("illegal-transition", "illegal-transition", "completed", illegal_transition),
    )


def run(sink: Optional[TextIO] = None, *, definitions: Optional[DefinitionSet] = None) -> int:
    """Provoke every refusal; 0 when each is refused by name and coverage matches."""
    out = sink if sink is not None else sys.stdout
    try:
        defs = definitions if definitions is not None else load_definitions()
        baselines = (
            spine.golden_path("negative-control", defs),
            spine.cancellation_path("negative-control", defs),
        )
    except Refused as refusal:
        print(f"negative-control: CANNOT-ASSESS — {refusal.detail}", file=out)
        return 2

    broken = [path for path in baselines if not path.ok]
    if broken:
        print(
            "negative-control: NOT-OK — the baseline path(s) this driver measures "
            "against are not themselves sound, so its provocations would be measured "
            "against a broken baseline",
            file=out,
        )
        for path in broken:
            for finding in path.findings:
                print(f"  FAIL  {path.name}: {finding.code}: {finding.detail}", file=out)
        return 1

    provoked: Dict[str, str] = {}
    failures: List[str] = []
    for provocation in provocations(defs):
        try:
            provocation.check()
        except Refused as refusal:
            if refusal.reason != provocation.code:
                failures.append(
                    f"{provocation.name}: refused as {refusal.reason!r}, expected "
                    f"{provocation.code!r} ({refusal.detail})"
                )
            elif provocation.needle not in refusal.detail:
                failures.append(
                    f"{provocation.name}: refused without naming {provocation.needle!r} "
                    f"({refusal.detail})"
                )
            else:
                provoked[provocation.code] = refusal.detail
        except AssertionError as assertion:
            failures.append(f"{provocation.name}: {assertion}")
        else:
            failures.append(
                f"{provocation.name}: nothing was refused — the control does not "
                "demonstrate the refusal it claims"
            )

    unprovoked = sorted(REFUSALS - set(provoked))
    for code in unprovoked:
        failures.append(f"{code}: in the refusal vocabulary but no control provokes it")
    undeclared = sorted(set(provoked) - REFUSALS)
    for code in undeclared:
        failures.append(f"{code}: provoked but not in the refusal vocabulary")

    print(
        f"negative-control: {len(provoked)} of {len(REFUSALS)} refusal(s) provoked",
        file=out,
    )
    for code in sorted(provoked):
        print(f"  OK    {code}: {provoked[code]}", file=out)
    if failures:
        print(
            f"negative-control: NOT-OK — {len(failures)} problem(s)", file=out
        )
        for failure in failures:
            print(f"  FAIL  {failure}", file=out)
        return 1
    print("negative-control: OK — every refusal is provoked and named", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
