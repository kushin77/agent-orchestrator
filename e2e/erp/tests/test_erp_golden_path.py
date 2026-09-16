"""The ERP module end to end (ERP-10, issue #655): the tenant journey, stage by stage.

Every assertion here reads a *measurement* the stage made — a value the module's own
code produced — rather than re-deriving the fact from a constant this file wrote. The
one place a constant appears it is the acceptance criterion itself (the cycle's four
families), and the stage reports where each of them was resolved from.
"""

from __future__ import annotations

from e2e.erp.golden_path import CYCLE_FAMILIES, run_erp_golden_path


def test_no_stage_reported_a_failure(journey):
    """The journey is green: every stage's own failure list is empty."""
    assert journey["failures"] == [], journey["failures"]
    assert journey["passed"] is True


def test_the_module_provisions_with_its_own_flag(journey):
    """AC1: a tenant's console serves the ERP module with the module's flag ON."""
    provision = journey["stages"]["provision"]
    assert set(provision["routes"]) == {"module", "dashboard", "reports", "documents", "frame"}
    for label, row in provision["routes"].items():
        assert row["status"] == 200, (label, row)
    assert provision["flag"]["shipped"] == "off"  # GR-5: it ships OFF
    assert provision["flag"]["promoted"] == "on"  # and a reviewed promotion turns it on
    assert provision["flag"]["served"] == "on"
    assert provision["manifest"]["mandatory"] is True
    assert provision["manifest"]["dataSource"] == "indexer"
    assert provision["manifest"]["features"][0]["id"] == "erp-module"
    # The manifest declares the switch as a boolean (ERP-01's schema); the console's
    # flag reader is what speaks "off"/"on", and the assertion above is about *it*.
    assert provision["manifest"]["features"][0]["default"] is False


def test_the_module_declares_the_cycle_it_serves(journey):
    """The provisioned module serves the families the cycle runs through."""
    provision = journey["stages"]["provision"]
    kinds = provision["contract"]["kinds"]
    for family in CYCLE_FAMILIES:
        assert family in kinds, family
    # The projection re-derives nothing: the dashboard's families ARE the served
    # contract's own kinds, in the contract's own order.
    assert provision["dashboard"]["families"] == kinds
    assert provision["dashboard"]["generatedFrom"]["contract"]


def test_the_cycle_is_the_acceptance_cycle(journey):
    """AC1: quotation -> sales-order -> delivery-note -> sales-invoice, resolved."""
    cycle = journey["stages"]["cycle"]
    assert tuple(cycle["chain"]) == CYCLE_FAMILIES
    # ...and resolved rather than restated: each hop's declaration is named by the
    # knowledge index, and carries the issue that declared it.
    for family in CYCLE_FAMILIES:
        declaration = cycle["hopDeclarations"][family]
        assert declaration["id"] == family
        assert declaration["owning_issue"] in (647, 648)
        assert f"{family}.json" in cycle["indexedDeclarations"]
    assert [link["kind"] for link in cycle["links"]]
    for link in cycle["links"]:
        assert link["field"] and link["target"]


def test_every_hop_left_its_initial_state_with_an_audit_step(journey):
    """Every document the cycle raised was moved, and the rail recorded each one."""
    cycle = journey["stages"]["cycle"]
    states = {document_id: row["state"] for document_id, row in cycle["documents"].items()}
    for family in CYCLE_FAMILIES:
        document = next(
            document_id for document_id, row in cycle["documents"].items() if row["kind"] == family
        )
        assert states[document] != "draft", (family, states)
    assert cycle["rail"]["entries"] > len(CYCLE_FAMILIES)
    assert cycle["rail"]["head"]


def test_the_delivery_moves_stock_and_the_invoice_posts_the_ledger(journey):
    """AC1: the cycle has stock and GL effects, and they are the documents' own."""
    cycle = journey["stages"]["cycle"]
    stock = cycle["stock"]
    assert stock["moved"] != 0
    assert stock["moved"] == -stock["deliveredQuantity"], stock
    assert stock["balances"] and any(value != 0 for value in stock["balances"].values())

    ledger = cycle["ledger"]
    totals = ledger["totals"]
    assert totals["debits"] == totals["credits"] != 0
    # The receivable side carries the invoice's OWN total, and the income side was
    # credited: the posting is the invoice's, not a figure this suite invented.
    roles = cycle["postingRoles"]
    by_account = ledger["byAccount"]
    assert by_account[roles["receivable"]]["debits"] == ledger["invoiceTotal"]
    assert by_account[roles["income"]]["credits"] > 0
    assert ledger["derivedPostings"], "the invoice derived no posting document"
    assert cycle["accountingKind"] in {row["kind"] for row in cycle["documents"].values()}


def test_the_scope_stage_decided_every_hop_for_the_owning_tenant(journey):
    """AC1: the cycle is scoped by ERP-08 — every hop allowed for its own tenant."""
    scope = journey["stages"]["scope"]
    assert [hop["kind"] for hop in scope["hops"]] == list(CYCLE_FAMILIES)
    for hop in scope["hops"]:
        assert hop["readAllowed"] is True, hop
        assert hop["writeAllowed"] is True, hop
        assert not hop["readReason"], hop
        assert hop["fieldsProjected"], hop
    # The derived ledger document is the cycle's effect, and is the only kind outside
    # the role map: a second one would be a cycle document nothing authorizes.
    assert scope["kindsOutsideRoleMap"] == [journey["stages"]["cycle"]["accountingKind"]]


def test_every_operation_the_cycle_performed_is_metered_onto_the_ledger(journey):
    """AC1: the cycle is metered on the ledger, and the cost is certified."""
    metering = journey["stages"]["metering"]
    assert metering["operations"] > 0
    assert metering["creates"] >= len(CYCLE_FAMILIES)
    assert metering["ledger"]["records"] == metering["operations"]
    assert metering["ledger"]["sequenceMatchesMeter"] is True
    assert metering["ledger"]["verdict"]["status"] == "OK"
    assert metering["usage"]["records"] == metering["operations"]
    bill = metering["rollup"]["bill"]
    assert bill["operations"] == metering["operations"]
    assert bill["unmeteredOperations"] == 0
    assert bill["costUsd"] > 0
    assert metering["certifiedTail"] == metering["ledger"]["tail"]
    assert metering["budget"]["declared"] is True


def test_the_journey_is_deterministic(run_dir, repo_root, session):
    """Two runs, one digest — which is what makes the metered figures quotable."""
    first = run_erp_golden_path(work_dir=run_dir, repo_root=repo_root, session=session)
    second = run_erp_golden_path(
        work_dir=f"{run_dir}{'-second'}", repo_root=repo_root, session=session
    )
    assert first["digest"] == second["digest"]
    assert first["stages"]["cycle"]["rail"]["head"] == second["stages"]["cycle"]["rail"]["head"]
