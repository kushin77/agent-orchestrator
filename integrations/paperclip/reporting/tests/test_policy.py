"""The declared claim-resolution policy is READ, not restated (issue #592).

Every test here is a proof of the same property, from a different angle: the
composer and the claim vocabulary get their rules from ``claim-policy.json``.
Doctoring that file changes what the machine refuses — if any of these tests
still saw the old behaviour, the artifact would be a decoration and the rule
would still be duplicated in the code.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conftest import build_pending_document
from governance.modules import registry as module_registry
from governance.modules.model import CannotAssess, TARGET_PENDING

from integrations.paperclip.reporting import composer, policy as claim_policy

HUB = "vendor/CMR"
POLICY_FILE = "integrations/paperclip/reporting/claim-policy.json"


def _rewrite(tree: Path, mutate) -> Path:
    """Doctoring helper: rewrite the scratch tree's policy and prove it landed."""
    path = tree / POLICY_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    before = json.dumps(data, sort_keys=True)
    mutate(data)
    after = json.dumps(data, sort_keys=True)
    assert after != before, "the policy mutation did not land"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def test_the_declared_policy_is_loaded_from_the_package(repo_root: Path):
    declared = claim_policy.load()
    assert declared.registry_prefix == "registry:"
    assert declared.bases == ("repository-root", "hub-root")
    assert declared.unresolved_code == "BRIEF-CLAIM-UNRESOLVED"
    assert declared.artifact == "docs/MODULE-BRIEF.md"
    assert declared.pending_state == TARGET_PENDING
    assert declared.pending_renders == TARGET_PENDING
    assert declared.pending_shipped_must_be is False
    assert declared.pending_blocker_required is True
    assert declared.pending_shipped_code == "BRIEF-PENDING-RENDERED-SHIPPED"
    assert declared.pending_blocker_code == "BRIEF-PENDING-NO-BLOCKER"


def test_the_policy_states_the_pending_rule_the_issue_pins(repo_root: Path):
    """A target-set module with no vendor module.json renders target-pending."""
    raw = json.loads((repo_root / POLICY_FILE).read_text(encoding="utf-8"))
    pending = raw["pending"]
    assert pending["state"] == pending["renders"] == TARGET_PENDING
    assert "no vendor module.json" in pending["when"]
    assert pending["never_rendered_as_shipped"] is True
    assert pending["shipped_must_be"] is False
    assert raw["resolution"]["unresolved"]["code"] == claim_policy.load().unresolved_code
    assert "never prose" in raw["resolution"]["unresolved"]["produces"]


def test_the_declared_rules_are_the_lanes_own_vocabulary(repo_root: Path):
    """The artifact and the package may not drift: one code, one artifact, one name.

    ``model.BRIEF_CODES`` and ``model.ARTIFACT`` are the package's published
    vocabulary; the policy declares the values the code actually *uses*. Binding
    the two here means a rename in either place fails loudly instead of quietly
    producing a refusal nobody's vocabulary names.
    """
    from integrations.paperclip.reporting import model

    declared = claim_policy.load()
    assert declared.unresolved_code in model.BRIEF_CODES
    assert declared.pending_shipped_code in model.BRIEF_CODES
    assert declared.pending_blocker_code in model.BRIEF_CODES
    assert declared.artifact == model.ARTIFACT
    assert declared.pending_state in model.STATES


def test_a_policy_that_calls_something_else_pending_is_cannot_assess(tree: Path):
    _rewrite(
        tree,
        lambda data: data["pending"].__setitem__("state", "probably-shipped"),
    )
    with pytest.raises(CannotAssess):
        claim_policy.load(tree / "integrations/paperclip/reporting")


def test_a_policy_that_lets_pending_render_as_shipped_is_cannot_assess(tree: Path):
    """The declaration is binding: the loader refuses a policy that contradicts it."""
    _rewrite(
        tree,
        lambda data: data["pending"].__setitem__("never_rendered_as_shipped", False),
    )
    with pytest.raises(CannotAssess):
        claim_policy.load(tree / "integrations/paperclip/reporting")


def test_a_policy_naming_an_unimplemented_citation_base_is_cannot_assess(tree: Path):
    _rewrite(
        tree,
        lambda data: data["resolution"].__setitem__("bases", ["somewhere-else"]),
    )
    with pytest.raises(CannotAssess):
        claim_policy.load(tree / "integrations/paperclip/reporting")


def test_the_composer_refuses_under_the_code_the_policy_declares(tree: Path):
    """Doctor the policy's refusal code; the composer must refuse under the new one."""
    _rewrite(
        tree,
        lambda data: data["resolution"]["unresolved"].__setitem__(
            "code", "BRIEF-CLAIM-UNRESOLVED-MUT"
        ),
    )
    doctored = _unresolvable(tree)
    proc = _compose(tree, doctored)
    assert proc.returncode == 1, proc.stderr
    assert "BRIEF-CLAIM-UNRESOLVED-MUT:" in proc.stderr
    assert "BRIEF-CLAIM-UNRESOLVED:" not in proc.stderr


def test_the_composer_refuses_a_pending_module_under_the_policys_code(pending_tree: Path):
    tree = pending_tree
    _rewrite(
        tree,
        lambda data: data["pending"]["refusals"].__setitem__(
            "rendered_shipped", "BRIEF-PENDING-RENDERED-SHIPPED-MUT"
        ),
    )
    document = build_pending_document(tree)
    for entry in document["modules"]:
        if entry["state"] == TARGET_PENDING:
            entry["shipped"] = True
            break
    else:  # pragma: no cover - the registry always carries a target
        raise AssertionError("no target-pending entry to doctor")
    doctored = tree / "pending-shipped.json"
    doctored.write_text(json.dumps(document, indent=2), encoding="utf-8")

    proc = _compose(tree, doctored)
    assert proc.returncode == 1, proc.stderr
    assert "BRIEF-PENDING-RENDERED-SHIPPED-MUT:" in proc.stderr
    assert "BRIEF-PENDING-RENDERED-SHIPPED:" not in proc.stderr


def test_the_composer_builds_citations_with_the_policys_prefix(repo_root: Path, registry_document):
    """A prefix the policy does not declare would make every claim a finding."""
    doctored = copy.deepcopy(registry_document)
    policy = claim_policy.ClaimPolicy(
        registry_prefix="row:",
        bases=("repository-root", "hub-root"),
        unresolved_code="BRIEF-CLAIM-UNRESOLVED",
        artifact="docs/MODULE-BRIEF.md",
        pending_state=TARGET_PENDING,
        pending_renders=TARGET_PENDING,
        pending_shipped_must_be=False,
        pending_blocker_required=True,
        pending_shipped_code="BRIEF-PENDING-RENDERED-SHIPPED",
        pending_blocker_code="BRIEF-PENDING-NO-BLOCKER",
    )
    with_policy = composer.compose(doctored, repo_root, HUB, policy=policy)
    assert not [
        finding
        for finding in with_policy.findings
        if finding.code == "BRIEF-CLAIM-UNRESOLVED"
    ], "the composer emitted citations the policy does not resolve"
    citations = [
        citation for claim in with_policy.claims for citation in claim.citations
    ]
    assert any(citation.startswith("row:") for citation in citations)
    assert not any(citation.startswith("registry:") for citation in citations), (
        "the composer is still emitting the literal prefix instead of the policy's"
    )


def _unresolvable(tree: Path) -> Path:
    """A registry document whose claim cites a seed that is not there."""
    document = module_registry.build(tree, tree / HUB)
    for entry in document["modules"]:
        if entry["state"] == "registered-mandatory":
            entry["assets"][0]["seed"] = "templates/module/not-here.json"
            entry["assets"][0]["seed_present"] = True
            break
    else:  # pragma: no cover - the registry always carries a mandatory module
        raise AssertionError("no registered-mandatory module to doctor")
    path = tree / "policy-unresolved.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _compose(tree: Path, registry_document: Path):
    import subprocess
    import sys

    return subprocess.run(
        [
            sys.executable,
            str(tree / "integrations/paperclip/reporting/cli.py"),
            "compose",
            "--registry",
            str(registry_document),
            "--repo",
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
