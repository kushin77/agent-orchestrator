"""SME reviewer doctrine: every PR/verdict gets an assigned persona, and
separation of duties is enforced (an auditor can never be the executing
persona of the task it audits).
"""

import pytest

import mapping
import registry

REAL = registry.PersonaRegistry()
REAL_CARDS = REAL.discover()


def test_reviewer_assigned_to_a_pr_is_distinct_from_executor():
    reviewer = mapping.assign_reviewer(
        REAL_CARDS, "platform", "coder", "code review of the coder diff"
    )
    assert reviewer["id"] != "coder"
    assert reviewer["posture"] in ("reviewer", "auditor")


def test_domain_scoring_picks_the_security_sme():
    subject = "security review: tenant isolation, authnz fail closed, secrets handling"
    reviewer = mapping.assign_reviewer(REAL_CARDS, "platform", "coder", subject)
    assert reviewer["id"] == "security-sme"


def test_auditor_is_assigned_and_distinct_from_executor():
    auditor = mapping.assign_auditor(
        REAL_CARDS, "platform", "coder", "audit the coder claims against evidence"
    )
    assert auditor["posture"] == "auditor"
    assert auditor["id"] == "auditor"
    assert auditor["id"] != "coder"


@pytest.mark.parametrize(
    "role,exc",
    [
        ("executor", mapping.AuditorCannotExecuteError),
        ("reviewer", mapping.SeparationViolationError),
    ],
)
def test_auditor_posture_never_executes_or_reviews_primary_work(role, exc):
    """An auditor persona can never be the executing persona of the task it
    audits (nor a primary-work reviewer) - separation of duties."""
    auditor_card = REAL_CARDS[("platform", "auditor")]
    with pytest.raises(exc):
        mapping.guard_dispatch(auditor_card, role)


def test_auditor_as_executor_of_its_own_audit_is_rejected():
    """Negative control: assigning the auditor persona as the executor of the
    same task it audits is rejected."""
    auditor_card = REAL_CARDS[("platform", "auditor")]
    executor_card = dict(auditor_card)  # the offending assignment
    with pytest.raises(mapping.AuditorCannotExecuteError):
        mapping.assert_auditor_not_executor(auditor_card, executor_card)
    # The same is true through the dispatcher: request executor duty.
    with pytest.raises(mapping.AuditorCannotExecuteError):
        mapping.guard_dispatch(auditor_card, "executor")


def test_self_review_is_rejected():
    reviewer = REAL_CARDS[("platform", "reviewer")]
    with pytest.raises(mapping.SelfReviewError):
        mapping.assert_reviewer_distinct(reviewer, reviewer)


def test_reviewer_never_reviews_own_work_when_only_candidate():
    only_reviewer = {("platform", "reviewer"): REAL_CARDS[("platform", "reviewer")]}
    with pytest.raises(mapping.NoReviewerAvailableError):
        mapping.assign_reviewer(only_reviewer, "platform", "reviewer", "review the verdict")


def test_executor_posture_cannot_be_dispatched_as_reviewer():
    coder = REAL_CARDS[("platform", "coder")]
    with pytest.raises(mapping.SeparationViolationError):
        mapping.guard_dispatch(coder, "reviewer")
    mapping.guard_dispatch(coder, "executor")  # ... but it may execute
