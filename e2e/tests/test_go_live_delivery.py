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

WHY this file moved to a fixture registry (issue #1043). ``REQUIRED_SURFACE``
(``surfaces.fleet_projection``) was deliberately promoted by #1027
(commit ``32e8c24``, the #607 go-live): the committed
``infra/feature-flags/registry.yaml`` now ships it with ``default: on`` /
``promoted: true``. That is the product doing exactly what EPIC #607 asked for,
not a regression — so "the surface ships unpromoted" is no longer a fact this
repository's committed declarations can prove, and a capstone that asserted it
anyway would be asserting a product state the product has left behind it.

The decision (#1043): promotion stays, the test moves. The claims this suite
still owes — "an unpromoted surface is absent to every caller" and "a rollback
of a promoted surface returns it to dark without a commit" — are general
properties of the rollout engine and the portal's surface gate, not properties
that only hold while the committed registry happens to ship the flag off. So
they are proven here against a CONTROLLED FIXTURE: a copy of this repository's
own rollout declarations with only ``infra/feature-flags/registry.yaml``
edited to hold the required surface unpromoted, fed to ``probe_delivery``
through its own ``declarations_root`` seam — the same seam the module already
uses to point the probe at another checkout — and driven by the real
``project_registry`` / ``observe_surface`` / ``registry_declares_on`` loaders,
never a reimplementation of what "declared" or "served" means. Every other
stage (``declared``, ``refused``, ``promoted``, ``served``, the negative
controls) is untouched: none of them depend on the shipped registry's default
for this surface, only on the rollout declarations and the live-state the
ladder itself records, so the fixture proves the dark/rollback claims without
weakening anything the other stages already proved over the real, shipped
posture.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from e2e.go_live_delivery import (
    DECLARATIONS,
    REPO_ROOT,
    REQUIRED_FLAG,
    REQUIRED_PHASE,
    REQUIRED_SURFACE,
    Delivery,
    project_registry,
    probe_delivery,
    registry_declares_on,
    serves_the_client,
    served_verdict,
)


@pytest.fixture(scope="module")
def fixture_declarations_root(tmp_path_factory) -> object:
    """A controlled copy of this repo's rollout declarations.

    Every file the probe reads through ``declarations_root`` is copied
    byte-for-byte from the real checkout EXCEPT ``registry.yaml``, where the
    required surface's ``default``/``promoted`` are forced back to how #1027
    found them (off / unpromoted). This is the fixture #1043 calls for: it lets
    the suite still prove "unpromoted ships dark" and "rollback lands dark"
    without asserting that the committed, shipped registry is itself
    unpromoted — which, post-go-live, it deliberately is not.
    """
    repo_root = Path(REPO_ROOT)
    root = tmp_path_factory.mktemp("go-live-declarations-fixture")

    rollout_dir = root / "infra" / "rollout"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    for name in DECLARATIONS:
        shutil.copy2(repo_root / "infra" / "rollout" / name, rollout_dir / name)

    flags_dir = root / "infra" / "feature-flags"
    flags_dir.mkdir(parents=True, exist_ok=True)
    registry_src = repo_root / "infra" / "feature-flags" / "registry.yaml"
    document = yaml.safe_load(registry_src.read_text(encoding="utf-8"))
    surfaces = document["surfaces"]
    assert REQUIRED_SURFACE in surfaces, (
        f"fixture setup: {REQUIRED_SURFACE!r} is not declared in {registry_src}"
    )
    surfaces[REQUIRED_SURFACE]["default"] = False
    surfaces[REQUIRED_SURFACE]["promoted"] = False
    (flags_dir / "registry.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    return root


@pytest.fixture(scope="module")
def delivery(tmp_path_factory, fixture_declarations_root) -> Delivery:
    """One delivery: the ladder, its live state, the reads and the controls.

    Driven with ``declarations_root=fixture_declarations_root`` (#1043): the
    real repository's plan/stage-model/live-state, but a registry copy that
    still ships the required surface unpromoted, so the dark/rollback stages
    keep proving what they always proved — over a fixture the suite controls,
    not the shipped posture #1027 promoted.
    """
    return probe_delivery(
        tmp_path_factory.mktemp("go-live-delivery"),
        declarations_root=fixture_declarations_root,
    )


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
    full_targets = [
        flag for flag, stage in delivery.promoted["planTargets"].items() if stage == "full"
    ]

    assert refused["rc"] == 1, refused["reason"]
    assert "refused before promoting anything" in refused["reason"]
    assert "canary_health_ok" in refused["blockingSignals"]
    # every `full`-target flag's canary -> gradual step is the one that is blocked
    assert refused["blockedTransitions"] == len(full_targets) > 0
    assert sorted(refused["blockedFlags"]) == sorted(full_targets)
    assert refused["planNamesThemToo"] == len(full_targets)
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
# Stage 4.5 — the shipped, promoted posture itself (#1043)
# --------------------------------------------------------------------------- #
def test_the_required_surface_ships_promoted_in_the_committed_registry():
    """The premise the fixture above exists BECAUSE of: measured on the real file.

    Not delivery-derived, not fixture-derived — read straight off the checkout's
    own ``infra/feature-flags/registry.yaml``, the same file ``dark``'s fixture
    deliberately does NOT use below. #1027 (commit 32e8c24, the #607 go-live)
    promoted this surface for real; if that promotion were ever reverted the
    fixture-based tests below would keep passing on the fixture alone and this
    is the one assertion that would catch it — the capstone still asserts the
    promoted posture, just not by making the dark/rollback claims depend on it.
    """
    committed = Path(REPO_ROOT) / "infra" / "feature-flags" / "registry.yaml"
    document = yaml.safe_load(committed.read_text(encoding="utf-8"))
    entry = document["surfaces"][REQUIRED_SURFACE]

    assert registry_declares_on(committed, REQUIRED_SURFACE) is True, (
        f"{REQUIRED_SURFACE} no longer ships promoted in {committed} — "
        "the #1027 go-live this suite is named after has been reverted"
    )
    assert entry.get("promoted") is True
    assert entry.get("default") in (True, "on")


# --------------------------------------------------------------------------- #
# Stage 5 — dark
# --------------------------------------------------------------------------- #
def test_the_surface_is_absent_while_it_ships_unpromoted(delivery):
    """Unpromoted means ABSENT, not merely unauthorised — to every caller.

    Measured over the controlled fixture registry (#1043), not the committed
    one: #1027 promoted this surface for real, so the committed registry no
    longer ships it off. The fixture holds it off so this general claim about
    the portal's surface gate still has somewhere true to be measured.
    """
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
    """Both halves of the withdrawal: the ladder's rollback and the runtime overlay.

    Also over the fixture (#1043): the ladder promotes the surface to `full`
    on the fixture's own live-state (asserted below as `stageBefore == "full"`,
    i.e. genuinely promoted first), then rolls it back — proving the rollback
    claim without depending on the shipped registry being unpromoted, which
    post-#1027 it is not.
    """
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
