"""The declared acceptance policy: read by the code, closed, and impossible to weaken.

Every test that mutates the policy writes the mutant to a **tmp** file and drives
the real reader (or the real ``registry.build``) with it. The packaged
``controls.yaml`` is never edited: it is the declaration the tree is judged by.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Dict

import pytest
import yaml
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
    "governance_modules_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
BASE_ROWS = _conftest.BASE_ROWS

from governance.modules import policy, registry
from governance.modules.model import REFUSAL_CODES, Refusal

#: The declaration as data, so a mutant is one small edit rather than a fixture.
BASE: Dict[str, Any] = yaml.safe_load(policy.DEFAULT_CONTROLS.read_text(encoding="utf-8"))


def write_mutant(path: Path, mutate: Callable[[Dict[str, Any]], None]) -> Path:
    data = copy.deepcopy(BASE)
    mutate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def condition(data: Dict[str, Any], condition_id: str) -> Dict[str, Any]:
    for entry in data["conditions"]:
        if entry["id"] == condition_id:
            return entry
    raise AssertionError("no condition {}".format(condition_id))


# --------------------------------------------------------------------------- #
# the packaged declaration
# --------------------------------------------------------------------------- #
def test_the_packaged_policy_judges_every_refusal_code() -> None:
    loaded = policy.load()
    assert loaded.schema == policy.SCHEMA
    assert set(loaded.refusal_codes()) == set(REFUSAL_CODES)
    assert len(loaded.refusal_codes()) == len(REFUSAL_CODES)
    assert loaded.fatal_codes() == tuple(sorted(REFUSAL_CODES))
    assert loaded.dispositions == policy.DISPOSITIONS


def test_the_packaged_policy_declares_every_judged_state() -> None:
    loaded = policy.load()
    assert [judgment.subject for judgment in loaded.judgments] == list(policy.JUDGED_SUBJECTS)
    assert all(
        judgment.disposition == policy.DISPOSITION_RECORDED for judgment in loaded.judgments
    )


def test_the_packaged_policy_names_the_authority_and_the_claim_rule() -> None:
    loaded = policy.load()
    assert loaded.authority["membership"] == policy.MEMBERSHIP_AUTHORITY
    assert loaded.authority["claim_effect"] == policy.CLAIM_EFFECT_NONE
    assert loaded.authority["citation"] == "kushin77/CMR#952"


def test_every_refusal_code_resolves_to_a_declared_condition() -> None:
    loaded = policy.load()
    for code in REFUSAL_CODES:
        stamped = loaded.judge(_refusal(code))
        assert stamped.code == code
        assert stamped.disposition == policy.DISPOSITION_FATAL
        assert stamped.condition == loaded.condition_for(code).id
        assert stamped.render().endswith("({}, fatal)".format(stamped.condition))


# --------------------------------------------------------------------------- #
# the mutants the gate provokes
# --------------------------------------------------------------------------- #
def test_a_refusal_code_filed_as_recorded_is_refused_by_name(tmp_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        condition(data, "module-identity")["disposition"] = policy.DISPOSITION_RECORDED

    path = write_mutant(tmp_path / "controls-recorded.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    message = str(excinfo.value)
    assert "MODULE-DUPLICATE-ID" in message
    assert "recorded" in message and "fatal" in message


def test_an_undeclared_refusal_code_is_refused_by_name(tmp_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = condition(data, "no-vendoring")
        entry["codes"] = [code for code in entry["codes"] if code != "VENDOR-EXTRA-SUBMODULE"]

    path = write_mutant(tmp_path / "controls-missing-code.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    assert "VENDOR-EXTRA-SUBMODULE" in str(excinfo.value)


def test_a_code_the_registry_cannot_emit_is_refused(tmp_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        condition(data, "module-identity")["codes"].append("MODULE-NOT-IN-THE-VOCABULARY")

    path = write_mutant(tmp_path / "controls-extra-code.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    assert "cannot emit" in str(excinfo.value)


def test_a_policy_that_would_let_a_claim_confer_membership_is_refused(
    tmp_path: Path,
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        data["authority"]["claim_effect"] = "confers-membership"

    path = write_mutant(tmp_path / "controls-claim.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    message = str(excinfo.value)
    assert "membership-not-inferred-from-a-claim" in message
    assert "claim" in message


def test_an_undeclared_judged_state_is_refused(tmp_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        data["judgments"] = [
            judgment for judgment in data["judgments"] if judgment["subject"] != "not-a-module"
        ]

    path = write_mutant(tmp_path / "controls-unjudged.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    assert "not-a-module" in str(excinfo.value)


def test_a_judged_state_declared_fatal_is_refused(tmp_path: Path) -> None:
    """A judged state is evidence, not drift — the weight belongs to the codes."""

    def mutate(data: Dict[str, Any]) -> None:
        for judgment in data["judgments"]:
            if judgment["subject"] == "not-a-module":
                judgment["disposition"] = policy.DISPOSITION_FATAL

    path = write_mutant(tmp_path / "controls-judgment-fatal.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        policy.load(path)
    assert "not-a-module" in str(excinfo.value)


def test_a_condition_without_a_statement_is_refused(tmp_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        condition(data, "hub-mandatory-drift")["statement"] = "  "

    path = write_mutant(tmp_path / "controls-no-statement.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable):
        policy.load(path)


# --------------------------------------------------------------------------- #
# the policy is load bearing, not decorative
# --------------------------------------------------------------------------- #
def test_the_registry_reads_the_policy_when_it_builds(hub, consumer, targets, tmp_path) -> None:
    """A policy the build path does not read could not stop it. This one does."""

    def mutate(data: Dict[str, Any]) -> None:
        data["authority"]["membership"] = "the-admission-register"

    path = write_mutant(tmp_path / "controls-authority.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        registry.build(consumer, hub, targets, controls_path=path)
    assert "confer membership" in str(excinfo.value)


def test_a_refusal_the_policy_cannot_judge_stops_the_registry(
    consumer, targets, make_hub, tmp_path
) -> None:
    """The drift is real, and the policy that cannot judge it is CANNOT-ASSESS."""
    rows = list(BASE_ROWS) + [["ghost-module", "ghost-module", "ghost.json", "provoked"]]
    drifting = make_hub("hub-ghost", rows=rows)

    # With the packaged policy the drift is refused, by name, as fatal.
    refusal = _only_refusal(registry.build(consumer, drifting, targets))
    assert refusal.code == "MODULE-UNREGISTERED-MANDATORY"
    assert refusal.subject == "ghost-module"
    assert refusal.disposition == policy.DISPOSITION_FATAL

    def mutate(data: Dict[str, Any]) -> None:
        entry = condition(data, "hub-mandatory-drift")
        entry["codes"] = [code for code in entry["codes"] if code != "MODULE-UNREGISTERED-MANDATORY"]

    path = write_mutant(tmp_path / "controls-cannot-judge.yaml", mutate)
    with pytest.raises(policy.PolicyUnavailable) as excinfo:
        registry.build(consumer, drifting, targets, controls_path=path)
    assert "MODULE-UNREGISTERED-MANDATORY" in str(excinfo.value)


def test_the_document_carries_the_declaration_that_judged_it(built) -> None:
    declared = built["policy"]
    assert declared["schema"] == policy.SCHEMA
    assert declared["path"].endswith("controls.yaml")
    assert declared["authority"]["claim_effect"] == policy.CLAIM_EFFECT_NONE
    assert declared["dispositions"] == list(policy.DISPOSITIONS)
    assert declared["refusal_codes"] == sorted(REFUSAL_CODES)
    assert [condition["id"] for condition in declared["conditions"]] == [
        entry.id for entry in policy.load().conditions
    ]
    assert built["refusals"] == []


def test_the_default_policy_is_the_packaged_one(consumer, targets, make_hub) -> None:
    """No caller's working directory decides which policy judged a registry.

    The path is recorded **as given** (relative, never absolutised) and resolves
    to the packaged declaration.
    """
    doc = registry.build(consumer, make_hub("hub-default"), targets)
    recorded = Path(doc["policy"]["path"])
    assert not recorded.is_absolute()
    assert (Path(consumer) / recorded).resolve() == policy.DEFAULT_CONTROLS


def _refusal(code: str) -> Refusal:
    return Refusal(code=code, subject="probe", detail="probe")


def _only_refusal(doc) -> Refusal:
    refusals = registry.findings(doc)
    assert len(refusals) == 1, [finding.render() for finding in refusals]
    return refusals[0]
