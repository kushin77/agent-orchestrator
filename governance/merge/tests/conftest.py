"""Pytest bootstrap + fixtures for the governance/merge suite (issue #43).

The modules under governance/merge/ are standalone scripts with no package
__init__.py (mirroring registry/personas). Inserting the package directory at
the front of sys.path lets the tests import them plainly as ``model``,
``gate``, ``reviewer`` and ``engine``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)


def _card(
    persona_id: str,
    posture: str,
    *,
    tenant: str = "platform",
    name: str = "",
    expertise=None,
    owned_lanes=None,
    summary: str = "",
) -> dict:
    return {
        "id": persona_id,
        "version": "1.0.0",
        "tenant": tenant,
        "name": name or persona_id.title(),
        "summary": summary,
        "expertise": list(expertise or []),
        "ownedLanes": list(owned_lanes or []),
        "posture": posture,
        "defaultModelTier": "LOW",
        "guardrailPolicyRef": "reviewer-bundle",
        "systemPromptRef": f"{persona_id}/primary@v1",
        "toolAllowlist": ["file_read", "shell_exec"],
        "capabilitySet": ["code-review"],
        "constraintSet": ["verify-before-done"],
        "memoryScope": ["session"],
        "provenance": ["example/provenance-source"],
    }


@pytest.fixture
def mini_cards() -> dict:
    """A tiny offline persona library with all three posture classes.

    Mirrors the issue #11 platform library shape:
    {(tenant, id): card}. ``coder`` is the executor; three reviewers
    (general ``reviewer`` owning ``merge-governance``, a ``security-sme`` and
    a ``qa-sme``); one auditor. Plus a tenant-scoped reviewer to exercise
    tenant visibility.
    """
    cards = {
        ("platform", "coder"): _card(
            "coder",
            "executor",
            name="Coder",
            expertise=["code authoring", "pull requests"],
            owned_lanes=["code-authoring"],
            summary="Authors code changes and PRs in its owned lane",
        ),
        ("platform", "reviewer"): _card(
            "reviewer",
            "reviewer",
            name="Reviewer",
            expertise=["independent review", "merge governance", "verification"],
            owned_lanes=["code-review", "merge-governance"],
            summary="General independent reviewer",
        ),
        ("platform", "security-sme"): _card(
            "security-sme",
            "reviewer",
            name="Security SME",
            expertise=["security review", "fail closed"],
            owned_lanes=["security", "guardrails"],
            summary="Adversarial security reviewer",
        ),
        ("platform", "qa-sme"): _card(
            "qa-sme",
            "reviewer",
            name="QA SME",
            expertise=["gate honesty", "negative controls"],
            owned_lanes=["verification"],
            summary="Gate-honesty reviewer",
        ),
        ("platform", "auditor"): _card(
            "auditor",
            "auditor",
            name="Auditor",
            expertise=["adversarial audit", "evidence"],
            owned_lanes=["audit"],
            summary="Adversarial evidence auditor; never executes",
        ),
        ("acme", "acme-reviewer"): _card(
            "acme-reviewer",
            "reviewer",
            tenant="acme",
            name="Acme Reviewer",
            expertise=["acme domain review"],
            owned_lanes=["acme"],
            summary="Tenant-scoped reviewer",
        ),
    }
    return cards


@pytest.fixture
def executor_only_cards() -> dict:
    """A library with no reviewer — used to prove no-reviewer blocks."""
    return {
        ("platform", "coder"): _card(
            "coder", "executor", expertise=["code"], owned_lanes=["code"]
        ),
        ("platform", "auditor"): _card(
            "auditor", "auditor", expertise=["audit"], owned_lanes=["audit"]
        ),
    }


@pytest.fixture
def pr_factory():
    """Factory for a fresh MergePr under test."""
    from model import MergePr

    def make(
        number: int = 1,
        author: str = "coder",
        author_tenant: str = "platform",
        subject: str = "merge governance of a verify-gate change",
        branch: str = "issue-1-fix",
        title: str = "a change under governance",
    ) -> MergePr:
        return MergePr(
            number=number,
            title=title,
            author=author,
            author_tenant=author_tenant,
            subject=subject,
            branch=branch,
        )

    return make


@pytest.fixture
def assigner(mini_cards):
    """An offline assigner over the fixture persona library."""
    from reviewer import PersonaAssigner

    return PersonaAssigner(cards=mini_cards, use_real_mapping=False)


@pytest.fixture
def green_outcome():
    """A green verify outcome naming a commit (attestation present)."""
    from gate import outcome_from_exit_code

    return outcome_from_exit_code(0, "abc123", evidence="make verify green")


@pytest.fixture
def red_outcome():
    """A NOT-OK verify outcome (the gate genuinely failed)."""
    from gate import outcome_from_exit_code

    return outcome_from_exit_code(1, "abc123", evidence="tests failed")


@pytest.fixture
def cannot_outcome():
    """A CANNOT-ASSESS verify outcome (no verdict — never a pass)."""
    from gate import outcome_from_exit_code

    return outcome_from_exit_code(2, "abc123", evidence="timeout, no verdict")
