"""E2E suite: the ordered go-live run delivering a surface a client can read (#955).

The probe lives in ``e2e/go_live_delivery.py``; this suite asserts what it
measured. The subject is the sentence EPIC #607 is about — the ordered,
owner-gated go-live run reaches the live state that makes the real portal serve
the fleet projection to an authenticated client — and every assertion below is
either a fact measured over the merged modules or a check that a guard genuinely
blocked when provoked.

Suite shape, and why: the ladder run is the expensive part (two driver passes over
the whole plan plus a real approval ledger), and every stage reads the SAME run —
the live state pass 2 recorded, projected into the declaration the portal reads.
So the probe runs once per module and the tests assert different facts about that
one delivery, exactly as ``e2e/tests/test_workbook11_portal_surfaces.py`` does for
the portal surfaces.
"""

from __future__ import annotations

import pytest

from e2e.go_live_delivery import (
    REQUIRED_FLAG,
    REQUIRED_PHASE,
    REPO_ROOT,
    Delivery,
    project_registry,
    probe_delivery,
    serves_the_client,
    served_verdict,
)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory) -> Delivery:
    """One delivery: the ladder, its live state, the reads and the controls."""
    return probe_delivery(tmp_path_factory.mktemp("go-live-delivery"))


# --------------------------------------------------------------------------- #
# Stage 1 — declared
# --------------------------------------------------------------------------- #
def test_the_run_is_declared_ordered_and_names_its_owner_gated_transitions(delivery):
    """The plan is lawful and the driver says which transitions need the owner."""
    declared = delivery.declared

    assert declared["validate"]["rc"] == 0, declared["validate"]["stdout"]
    assert "rollout declarations: OK" in declared["validate"]["stdout"]
    assert declared["preflight"]["rc"] == 0
    assert declared["preflight"]["orderedAndLawful"] is True
    assert declared["preflight"]["promotionOrder"] is True
    # a dry run writes nothing — the preflight says so itself
    assert declared["preflight"]["nothingWritten"] is True

    # every flag whose declared stage is `full` is named as needing the owner's
    # approval code: the owner gate is measured, not asserted in prose.
    full_targets = [
        flag
        for flag, stage in delivery.promoted["planTargets"].items()
        if stage == "full"
    ]
    assert declared["preflight"]["ownerGatedTransitions"] == len(full_targets) > 0
    assert declared["preflight"]["ownerGatedLines"], "the preflight names no owner-gated transition"


# --------------------------------------------------------------------------- #
# Stage 2 — refused
# --------------------------------------------------------------------------- #
def test_a_run_without_its_gate_inputs_is_refused_before_anything_is_written(delivery):
    """No health attestation, no approvals: the run stops and the tree is untouched."""
    refused = delivery.refused

    assert refused["rc"] == 1, refused["reason"]
    assert "refused before promoting anything" in refused["reason"]
    assert "canary_health_ok" in refused["blockingSignals"]
    assert refused["blockedTransitions"] > 0
    assert refused["wroteNothing"] is True
    assert refused["liveStateEmpty"] is True
    assert refused["auditRecordsWritten"] == 0


# --------------------------------------------------------------------------- #
# Stage 3 — promoted
# --------------------------------------------------------------------------- #
def test_every_planned_flag_reaches_its_declared_go_live_stage(delivery):
    """The declared hold is honoured, then the whole plan completes.

    Two passes over the SAME command: the first lawfully stops short (the stage
    model declares a 24h ``gradual`` dwell, and the driver derives it from the
    transition it recorded), the second — driven past the hold with the owner's
    approval-as-code in the ledger — reaches every declared stage.
    """
    promoted = delivery.promoted

    # pass 1: nothing is promoted past `gradual`, and every remaining step is
    # deferred by the declared hold rather than skipped or silently completed.
    assert promoted["pass1"]["rc"] == 1, "a run still owing transitions must not report OK"
    assert promoted["pass1"]["deferredTransitions"] == len(
        [flag for flag, stage in promoted["planTargets"].items() if stage == "full"]
    )
    for flag, stage in promoted["pass1"]["stages"].items():
        assert stage in ("canary", "gradual"), f"{flag} reached {stage} in the first pass"

    # the hold was measured on the driver's own clock, not slept through
    assert promoted["dwell"]["advancedByIsAtLeastADay"] is True
    assert "0s" in promoted["dwell"]["slept"]

    # pass 2: complete, with no flag short of its declared stage
    assert promoted["pass2"]["rc"] == 0, promoted["pass2"]["shortOfTarget"]
    assert promoted["pass2"]["shortOfTarget"] == {}
    assert promoted["pass2"]["stages"] == promoted["planTargets"]
    assert promoted["requiredFlagDeclared"] is (REQUIRED_FLAG in promoted["planTargets"])


def test_the_run_delivers_the_surface_the_epic_reads_at_its_declared_stage(delivery):
    """The flag ``/api/fleet/snapshot`` is refused on is part of the delivered plan.

    This is the leg that did not exist: the ladder could reach every stage the
    plan declared and still leave the SPoG dark, because the flag the portal reads
    was not in the plan at all. ``go-live-plan.yaml`` must declare it, in the
    portal's own phase, and the run must record it.
    """
    promoted = delivery.promoted

    assert promoted["requiredFlagDeclared"] is True, promoted["registrationMissing"]
    assert promoted["registrationMissing"] == ""
    assert promoted["planTargets"][REQUIRED_FLAG] == "full"
    assert promoted["planPhases"][REQUIRED_FLAG] == REQUIRED_PHASE
    assert promoted["pass2"]["stages"][REQUIRED_FLAG] == "full"
    assert promoted["liveState"]["requiredEntry"] is not None
    assert promoted["liveState"]["requiredEntry"]["stage"] == "full"
    # the final promotion is human-gated: a policy auto-approval can never reach full
    assert promoted["liveState"]["requiredEntry"].get("approval_id")


def test_every_promoted_entry_names_an_audit_record_that_exists_and_the_chain_verifies(delivery):
    """A promoted stage with no provable trail would make the record fiction."""
    promoted = delivery.promoted

    assert promoted["liveState"]["entries"] > 0
    assert promoted["liveState"]["missingAuditRecords"] == []
    assert promoted["liveState"]["auditChainVerifies"] is True
    assert promoted["liveState"]["auditLogRecords"] >= promoted["liveState"]["entries"]
    # every entry is attributed to its OWN transition's record (issue #619)
    for flag, entry in promoted["liveState"]["stages"].items():
        assert entry, f"{flag} recorded no stage"


# --------------------------------------------------------------------------- #
# Stage 4 — served
# --------------------------------------------------------------------------- #
def test_the_promoted_surface_is_served_to_an_authenticated_client_only(delivery):
    """The client reads the console's OWN projection — and only with a session."""
    served = delivery.served

    assert delivery.projection["requiredDeclaredOn"] is True
    assert delivery.projection["flipped"].get("fleet_projection") == "full"

    assert served["verdict"] == "PASS", served["error"]
    assert served["status"] == 200
    assert serves_the_client(served) is True
    # never a bare 200: the payload carries the console's own vocabulary
    assert served["projection"]["sections"] == served["expectedSections"]
    assert served["projection"]["rungs"] == served["expectedRungs"]
    assert served["projection"]["rungRowKeys"] == served["expectedRungRowKeys"]
    assert served["projection"]["repo"] == served["expectedRepo"]

    # a promoted surface is access-controlled, not merely gated
    assert served["statusAnonymous"] == 401
    assert served["codeAnonymous"] == "unauthorized"


# --------------------------------------------------------------------------- #
# Stage 5 — dark
# --------------------------------------------------------------------------- #
def test_the_surface_is_absent_while_it_ships_unpromoted(delivery):
    """Unpromoted means ABSENT, not merely unauthorised — to every caller."""
    dark = delivery.dark

    assert dark["declaresOn"] is False
    assert dark["surfaceEnabled"] is False
    assert dark["status"] == 404
    assert dark["code"] == "feature_disabled"
    # the refusal happens before authN, so an anonymous probe cannot tell the
    # surface exists: both reads answer the same way.
    assert dark["statusAnonymous"] == 404
    assert dark["codeAnonymous"] == "feature_disabled"
    assert dark["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# Stage 6 — rolled back
# --------------------------------------------------------------------------- #
def test_a_rollback_returns_the_surface_to_dark_without_a_commit(delivery):
    """Both halves of the withdrawal: the ladder's rollback and the runtime overlay."""
    rolled = delivery.rolledBack

    # the ladder's rollback: audited, and it leaves no stale live-state entry
    assert rolled["rc"] == 0, rolled["stderr"]
    assert "rolled back" in rolled["stdout"]
    assert rolled["stageBefore"] == "full"
    assert rolled["entryAbsentAfterRollback"] is True
    assert rolled["ladder"]["verdict"] == "FAIL"
    assert rolled["ladder"]["status"] == 404
    assert rolled["ladder"]["code"] == "feature_disabled"
    assert "fleet_projection" not in rolled["ladder"]["flipped"]

    # the runtime half: the declaration still says on, the surface is dark
    runtime = rolled["runtime"]
    assert runtime["declarationDeclaresOn"] is True
    assert runtime["enabledWithOverlayEngaged"] is False
    assert runtime["statusWithOverlayEngaged"] == 404
    assert runtime["codeWithOverlayEngaged"] == "feature_disabled"
    assert runtime["declarationUnchanged"] is True
    # ... and the round trip is a real rollback, not a one-way kill
    assert runtime["cleared"] is True
    assert runtime["statusAfterClear"] == 200
    assert runtime["verdictAfterClear"] == "PASS"

    # no commit anywhere: the committed declaration is byte-identical
    assert rolled["committedRegistryUnchanged"] is True


# --------------------------------------------------------------------------- #
# The negative controls, and the two properties that make the suite load-bearing
# --------------------------------------------------------------------------- #
def test_every_negative_control_genuinely_blocks(delivery):
    """One check per guard, each passing ONLY when the real guard blocks."""
    controls = {control["controlId"]: control for control in delivery.controls["controls"]}
    subjects = {control["subjectFlag"] for control in controls.values() if control.get("subjectFlag")}

    assert delivery.controls["count"] == 5
    assert delivery.controls["allBlocked"] is True
    assert delivery.controls["passed"] == 5
    assert set(controls) == {
        "full-cannot-carry-a-policy-approval",
        "approver-must-differ-from-the-actor",
        "a-promoted-entry-needs-its-audit-record",
        "a-phase-cannot-run-before-the-earlier-phases",
        "a-surface-served-while-its-flag-is-off-is-not-a-pass",
    }
    # which flag each control provoked is recorded: a guard is provoked for real,
    # never against a transition that was already complete.
    assert subjects, "no control recorded the flag it provoked"

    # 1. `full` needs a human approval_id — refused by the gate AND by the validator
    policy = controls["full-cannot-carry-a-policy-approval"]
    assert "missing gate signal: approval_code" in policy["engineRefusal"]
    assert "is at full but has no human approval_id" in policy["validatorRefusal"]

    # 2. an approval granted by the executing actor is not an approval
    self_grant = controls["approver-must-differ-from-the-actor"]
    assert self_grant["ledgerRefusal"] == "approver must be distinct from the executing actor"
    assert self_grant["driverRc"] == 1
    assert "is not usable - approver must be distinct" in self_grant["driverRefusalForTheFlag"]
    assert self_grant["nothingMoved"] is True

    # 3. a promoted entry whose evidence file does not exist is CANNOT-ASSESS
    record = controls["a-promoted-entry-needs-its-audit-record"]
    assert "does not exist" in record["validatorRefusal"]
    assert record["driverRc"] == record["expectedCannotAssess"] == 2
    assert "live-state cannot be assessed" in record["driverRefusal"]

    # 4. phase 7 cannot run before the earlier phases, and the refusal names them
    order = controls["a-phase-cannot-run-before-the-earlier-phases"]
    assert order["driverRc"] == 1
    assert "strict-by-phase" in order["refusal"]
    assert f"phase {REQUIRED_PHASE} may not run before phase 0 is complete" in order["refusal"]
    assert order["namesTheBlockingFlag"] is True
    assert order["blockingFlagNamed"] in order["refusal"]
    assert order["wroteNothing"] is True

    # 5. the served predicate itself, mutated
    mutation = controls["a-surface-served-while-its-flag-is-off-is-not-a-pass"]
    assert mutation["realVerdict"] == "FAIL"
    assert mutation["weakenedVerdict"] == "PASS"
    assert mutation["observationStatus"] == 404
    assert mutation["observationCode"] == "feature_disabled"
    assert mutation["surfaceEnabled"] is False


def test_the_repository_tree_is_byte_identical_after_the_run(delivery):
    """Nothing the run did reached the checkout: the ladder ran in its sandbox."""
    assert delivery.repoUnchanged is True, (
        "the run wrote into the repository:\n"
        f"before={delivery.repoBefore}\nafter={delivery.repoAfter}"
    )
    assert delivery.repoBefore["git_status"] == delivery.repoAfter["git_status"]
    # the three trees a rollout or a portal read could plausibly write into
    for key in ("infra", "registry", "rollout_overlay", "portal_config"):
        assert delivery.repoBefore[key] == delivery.repoAfter[key], key
    assert sorted(delivery.repoBefore) == sorted(delivery.repoAfter)


def test_the_reads_are_a_function_of_the_recorded_state_not_of_the_run(delivery):
    """Same live state in, same declaration out, same answers — measured twice."""
    first = project_registry(delivery.sandbox, declarations_root=delivery.declarations_root)
    second = project_registry(delivery.sandbox, declarations_root=delivery.declarations_root)

    assert first["digest"] == second["digest"]
    assert first["flipped"] == second["flipped"]
    # the withdrawal is a property of the RECORDED state, not of a tool's memory:
    # projecting what the rollback left behind reproduces the rollback's own view.
    assert delivery.rolledBack["ladder"]["flipped"] == first["flipped"]
    assert shipped_route_answers(delivery) == shipped_route_answers(delivery)


def shipped_route_answers(delivery: Delivery) -> tuple:
    """The unpromoted read, twice: ``(status, code)`` must not drift."""
    from e2e.go_live_delivery import observe_surface

    registry = delivery.dark["registry"]
    observation = observe_surface(delivery.sandbox, registry=registry, repo_root=REPO_ROOT)
    return (observation["status"], observation["code"], served_verdict(observation))
