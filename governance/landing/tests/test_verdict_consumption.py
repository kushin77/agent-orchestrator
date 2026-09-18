"""The merge verdict is CONSUMED from governance/merge, never re-derived (#764).

Issue #764's fourth acceptance criterion is a "consume, never redefine" rule:
``governance/merge/model.merge_verdict`` is the single rule for "may this merge",
and the landing driver must consult it. These tests make that mechanical rather
than a claim, so a future edit that re-implements the rule locally fails here
*twice*: once for the sidecar rule and once for the fake reason injected below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_landing_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
HEAD = _conftest.HEAD

from governance.landing import verdict

MERGE = verdict.merge_modules()


def _decide(**overrides):
    params = dict(
        number=11,
        title="feat(landing): the driver",
        author="copilot-lane",
        subject="the landing driver",
        branch="issue-764",
        outcome=verdict.gate_outcome(0, HEAD, "green (fixture)"),
    )
    params.update(overrides)
    return verdict.decide(**params)


class TestTheSeamResolvesToTheRealModules:
    def test_every_consumed_module_resolves_inside_governance_merge(self):
        assert MERGE, "governance/merge did not load at all"
        for name, module in MERGE.items():
            resolved = Path(module.__file__).resolve()
            assert resolved.parent == verdict.MERGE_DIR, f"{name} resolved to {resolved}"

    def test_the_verdict_names_its_rule_source(self):
        assert Path(_decide().source).name == "model.py"
        assert Path(_decide().source).parent == verdict.MERGE_DIR


class TestTheRuleIsTheMergeModels:
    def test_a_green_lane_with_an_independent_reviewer_is_mergeable(self):
        answer = _decide()
        assert answer.mergeable and answer.state == "mergeable"
        assert answer.reviewer_id, "governance/merge assigns the independent reviewer; the landing must not"

    def test_a_red_gate_is_blocked_by_the_merge_models_own_reason(self):
        answer = _decide(outcome=verdict.gate_outcome(1, HEAD, "red (fixture)"))
        assert not answer.mergeable and answer.state == "blocked"
        assert answer.reasons == ("verify-gate-not-green",)

    def test_a_green_gate_that_names_no_commit_is_blocked(self):
        answer = _decide(outcome=verdict.gate_outcome(0, None, "green but unnamed"))
        assert not answer.mergeable
        assert answer.reasons == ("verify-gate-no-commit-attestation",)

    def test_cannot_assess_is_never_green(self):
        answer = _decide(outcome=verdict.gate_outcome(2, HEAD, "no verdict"))
        assert not answer.mergeable
        assert answer.reasons == ("verify-gate-cannot-assess",)

    def test_the_owner_carve_out_is_the_merge_rules_switch_not_the_landings(self):
        answer = _decide(owner_carve_out=False)
        assert not answer.mergeable
        assert answer.reasons == ("self-merge-without-owner-carve-out",)

    def test_every_reason_comes_from_the_merge_vocabulary(self):
        allowed = {reason.value for reason in MERGE["model"].BlockReason}
        assert set(self._all_reasons()) <= allowed

    @staticmethod
    def _all_reasons():
        reasons = []
        for rc, commit in ((1, HEAD), (2, HEAD), (0, None)):
            reasons.extend(_decide(outcome=verdict.gate_outcome(rc, commit, "fixture")).reasons)
        reasons.extend(_decide(owner_carve_out=False).reasons)
        return reasons


class TestTheRuleCannotBeRedefinedLocally:
    def test_patching_the_merge_rule_changes_the_landing_verdict(self, monkeypatch):
        """The decisive control: the landing reads the merge model's function.

        If `verdict.decide` had its own copy of the rule (or cached a positive
        answer), injecting a refusal into `model.merge_verdict` would not reach
        it. The injected reason is drawn from the merge model's own vocabulary,
        because the machine validates it (`BlockReason(first)`): a re-implemented
        rule would answer `mergeable` here and fail this test.
        """
        injected = MERGE["model"].BlockReason.REVIEW_REJECTED.value
        monkeypatch.setattr(MERGE["model"], "merge_verdict", lambda _signals: (False, [injected]))
        answer = _decide()
        assert not answer.mergeable
        assert answer.reasons == (injected,)
        assert answer.state == "blocked"

    def test_the_landing_modules_do_not_restate_the_rule(self):
        package = Path(verdict.__file__).resolve().parent
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "def merge_verdict" not in source, f"{path.name} defines a second merge rule"
            assert "def conclude_merge" not in source, f"{path.name} re-implements the merge machine"

    def test_a_missing_merge_tree_is_a_refusal_not_a_fallback(self, monkeypatch, tmp_path):
        """A landing that cannot consult the verdict must not invent one."""
        monkeypatch.setattr(verdict, "MERGE_DIR", tmp_path / "missing-merge")
        monkeypatch.setattr(verdict, "_CACHE", {})
        with pytest.raises(verdict.MergeSeamError) as raised:
            verdict.merge_modules()
        assert "missing-merge" in str(raised.value)
