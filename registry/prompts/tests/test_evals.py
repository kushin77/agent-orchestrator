"""Tests for the regression-eval harness + publish gate (issue #639).

Acceptance criteria covered:

- a module with **failing** evals cannot publish (the gate refuses it), and a
  module with **no** evals cannot publish either - an unevaluated module is
  never silently green;
- the commit-time eval gate blocks publication *before* the manifest is
  written, so a refused publish leaves no trace;
- every published C-suite module renders with no leftover ``{{...}}``
  placeholders, and each is mechanically mapped to its workbook-5 policy id.

The last test class is the negative control for the gate: it mutates the eval
cases and the manifest in a scratch copy of the tree, never the tracked ones.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import evals
import registry as registry_mod
from evals import EvalGateError, UnevaluatedPromptError
from registry import PromptModuleError, PromptRegistry

PKG_DIR = registry_mod.PKG_DIR
CASES_PATH = PKG_DIR / "evals" / "eval-cases.yaml"

# The five workbook-8 C-suite modules and the workbook-5 policy id each one's
# mechanical enforcement rule maps to. The mapping is measured from
# guardrails/policy/workbook-mechanical-rules.md (issue #636); this lane is a
# read-only consumer of those policy ids and declares the pairing as data.
CSUITE_POLICY_MAP = {
    "ceo-primary": (
        "workbook-vector-memory-frontload",
        "workbook.prefetch",
        "prefetch.frontloaded",
    ),
    "cto-primary": (
        "workbook-drawio-mcp-diagramming",
        "workbook.drawio",
        "drawio.mcp_tool_list_cacheable",
    ),
    "coo-primary": (
        "workbook-external-state-caching",
        "workbook.external_state",
        "external_state.cacheable",
    ),
    "cfo-primary": (
        "workbook-zero-token-arithmetic",
        "workbook.arithmetic",
        "arithmetic.cacheable",
    ),
    "cmo-primary": (
        "workbook-webhook-caching",
        "workbook.webhook",
        "webhook.cacheable",
    ),
}
CSUITE_MODULES = sorted(CSUITE_POLICY_MAP)


def _repo_registry() -> PromptRegistry:
    return PromptRegistry()


def _tmp_registry(tmp_path: Path) -> PromptRegistry:
    """A writable copy of the prompt-library tree for mutation tests."""
    root = tmp_path / "reg"
    shutil.copytree(PKG_DIR, root)
    return PromptRegistry(root=root)


def _cases() -> list:
    return evals.load_cases(CASES_PATH)


# ------------------------------------------------------------------ the cases
def test_eval_cases_are_wired_to_published_modules() -> None:
    """Every C-suite module has at least one eval case under `cases`."""
    cases = _cases()
    covered = {c.prompt_id for c in cases if c.prompt_id in
               {f"{m}@v1" for m in CSUITE_MODULES}}
    assert covered == {f"{m}@v1" for m in CSUITE_MODULES}


def test_published_csuite_evals_all_pass() -> None:
    """The shipped cases for the five modules are green (no FP, no FN)."""
    reports = evals.evaluate(_cases())
    for module in CSUITE_MODULES:
        report = reports[f"{module}@v1"]
        assert report.ok, f"{module}@v1 has failing evals: {report.failing_cases}"


def test_publish_gate_requires_evals() -> None:
    """A promptId with no cases is refused - unevaluated is never green."""
    with pytest.raises(UnevaluatedPromptError):
        evals.require_ok("no-such-module@v1", _cases())


def test_eval_case_pass_requires_zero_fp_and_fn() -> None:
    """A fully-matching case passes; a dropped expected label fails as an FN."""
    ok = evals.EvalCase("p@v1", "a", frozenset({"x"}), frozenset({"x"}))
    missing = evals.EvalCase("p@v1", "b", frozenset({"x"}), frozenset())
    extra = evals.EvalCase("p@v1", "c", frozenset(), frozenset({"x"}))
    assert ok.passes()
    assert missing.failures() == ([], ["x"])
    assert extra.failures() == (["x"], [])


# ------------------------------------------------------------- publish refusal
def test_publish_refuses_module_with_failing_evals(tmp_path: Path) -> None:
    """The gate refuses a module whose evals fail (the core acceptance test)."""
    reg = _tmp_registry(tmp_path)
    # The scratch tree's eval cases are replaced with a case for a registered
    # module, so the refusal comes from the eval verdict and nothing else.
    cases = reg.root / "evals" / "eval-cases.yaml"
    cases.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "caseId": "boom",
                        "promptId": "ao-boom@v1",
                        "expected": ["a", "b"],
                        "observed": ["a"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reg.register(
        module={
            "taskType": "ao-boom",
            "version": "v1",
            "modelTierHint": "low",
            "bodyRefs": {
                "system": "../bodies/summarize.v1.system.md",
                "user": "../bodies/summarize.v1.user.md",
            },
            "outputSchema": "../output-schemas/summarize.schema.json",
            "description": "test-only module with failing regression evals",
        }
    )
    manifest_before = reg.manifest_path.read_text(encoding="utf-8")
    with pytest.raises(PromptModuleError) as excinfo:
        reg.publish("ao-boom", "v1")
    assert "failed regression evals" in str(excinfo.value)
    # No-false-green: a refused publish leaves the manifest untouched, so the
    # version cannot later resolve as if it had been frozen.
    assert reg.manifest_path.read_text(encoding="utf-8") == manifest_before
    assert reg._published_versions("ao-boom") == []
    with pytest.raises(PromptModuleError):
        reg.resolve("ao-boom")


def test_publish_refuses_unevaluated_module(tmp_path: Path) -> None:
    """A module with no eval cases at all cannot publish."""
    reg = _tmp_registry(tmp_path)
    reg.register(
        module={
            "taskType": "ao-unevaluated",
            "version": "v1",
            "modelTierHint": "low",
            "bodyRefs": {
                "system": "../bodies/summarize.v1.system.md",
                "user": "../bodies/summarize.v1.user.md",
            },
            "outputSchema": "../output-schemas/summarize.schema.json",
            "description": "test-only module with no eval cases",
        }
    )
    with pytest.raises(PromptModuleError) as excinfo:
        reg.publish("ao-unevaluated", "v1")
    assert "no regression-eval cases" in str(excinfo.value)
    assert reg._published_versions("ao-unevaluated") == []


def test_publish_passes_when_evals_pass(tmp_path: Path) -> None:
    """A module with green evals publishes (the gate is not a blanket refusal)."""
    reg = _tmp_registry(tmp_path)
    cases = reg.root / "evals" / "eval-cases.yaml"
    cases.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "caseId": "ok",
                        "promptId": "ao-good@v1",
                        "expected": ["a"],
                        "observed": ["a"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    reg.register(
        module={
            "taskType": "ao-good",
            "version": "v1",
            "modelTierHint": "low",
            "bodyRefs": {
                "system": "../bodies/summarize.v1.system.md",
                "user": "../bodies/summarize.v1.user.md",
            },
            "outputSchema": "../output-schemas/summarize.schema.json",
            "description": "test-only module with passing regression evals",
        }
    )
    digest = reg.publish("ao-good", "v1")
    assert digest.startswith("sha256:")
    assert reg._published_versions("ao-good") == ["v1"]


def test_eval_gate_is_the_only_difference_in_the_refusal(tmp_path: Path) -> None:
    """Same module body, one green case and one failing case: only the verdict changes.

    This is the mutation proof in test form - if the gate were removed, both
    publishes would behave identically.
    """
    cases = [
        {"caseId": "c", "promptId": "ao-mut@v1", "expected": ["a"], "observed": ["a"]},
    ]
    failing = [
        {"caseId": "c", "promptId": "ao-mut@v1", "expected": ["a"], "observed": []},
    ]

    def _publish(case_list: list) -> str:
        root = tmp_path / ("reg-" + ("fail" if case_list == failing else "pass"))
        shutil.copytree(PKG_DIR, root)
        reg = PromptRegistry(root=root)
        (root / "evals" / "eval-cases.yaml").write_text(
            yaml.safe_dump({"cases": case_list}), encoding="utf-8"
        )
        reg.register(
            module={
                "taskType": "ao-mut",
                "version": "v1",
                "modelTierHint": "low",
                "bodyRefs": {
                    "system": "../bodies/summarize.v1.system.md",
                    "user": "../bodies/summarize.v1.user.md",
                },
                "outputSchema": "../output-schemas/summarize.schema.json",
                "description": "mutation-proof module",
            }
        )
        try:
            reg.publish("ao-mut", "v1")
            return "published"
        except PromptModuleError:
            return "refused"

    assert _publish(cases) == "published"
    assert _publish(failing) == "refused"


def test_candidate_fixture_fails_and_is_not_published() -> None:
    """The deliberately-failing candidate is present, red, and unpublished."""
    reports = evals.evaluate(_cases())
    candidate = reports["ao-empty@v1"]
    assert not candidate.ok
    assert candidate.failing_cases
    assert "ao-empty" not in _repo_registry()._manifest["taskTypes"]
    with pytest.raises(EvalGateError):
        evals.require_ok("ao-empty@v1", _cases())


# ------------------------------------------------------- policy-id mapping
@pytest.mark.parametrize("module", CSUITE_MODULES)
def test_csuite_module_maps_to_its_workbook_policy(module: str) -> None:
    """Each C-suite module declares the workbook-5 policy id it enforces.

    The mapping lives in this lane (a read-only consumer of guardrails/policy)
    as the module's `enforcement` block - policyId + action + gate attribute -
    so the pairing is machine-checkable rather than prose. The declared policy
    id is cross-checked against the id shipped in guardrails/policy, and the
    workbook source (#614) is recorded under GR-10 provenance.
    """
    policy_id, action, attribute = CSUITE_POLICY_MAP[module]
    resolved = _repo_registry().resolve(module)
    enforcement = resolved.module["enforcement"]
    assert enforcement["policyId"] == policy_id
    assert enforcement["action"] == action
    assert enforcement["attribute"] == attribute
    # GR-10: the module records the workbook it adapted from.
    provenance = " ".join(resolved.module["provenance"])
    assert "#614" in provenance
    assert "#636" in provenance  # the policy-id declaration it maps onto
    # The declared policy id is shipped by the guardrails lane.
    policy_doc = (
        PKG_DIR.parent.parent
        / "guardrails"
        / "policy"
        / "bundles"
        / "platform"
        / "workbook-mechanical-rules.yaml"
    )
    assert policy_id in policy_doc.read_text(encoding="utf-8")


@pytest.mark.parametrize("module", CSUITE_MODULES)
def test_csuite_module_renders_without_leftovers(module: str) -> None:
    """Every C-suite module renders cleanly with all variables supplied."""
    reg = _repo_registry()
    variables = {"input": "x"}
    # Collect every placeholder the bodies declare, then fill them all.
    import re

    bodies = "\n".join(
        path.read_text(encoding="utf-8") for path in reg.resolve(module).bodies.values()
    )
    for name in re.findall(r"\{\{([A-Za-z0-9_.-]+)\}\}", bodies):
        variables[name] = "filled"
    rendered = reg.render_prompt(module, variables)
    for part, text in rendered["bodies"].items():
        assert "{{" not in text, f"{module} {part} kept a placeholder"
        assert "}}" not in text, f"{module} {part} kept a placeholder"
    assert rendered["modelTierHint"] in {"low", "med", "high"}


def test_csuite_module_tier_hints_match_persona_cards() -> None:
    """The module tier hint agrees with the persona card's declared tier.

    The C-suite PersonaCards (issue #632) carry `systemPromptRef: <role>/primary@v1`
    and a workbook tier ladder; the module must not contradict it.
    """
    cards_dir = PKG_DIR.parent / "personas" / "cards"
    expected = {
        "ceo-primary": "high",  # workbook Tier 1  -> MAX (pro)
        "cto-primary": "high",  # workbook Tier 1-2 -> HIGH (pro)
        "coo-primary": "med",   # workbook Tier 3-4 -> MED
        "cfo-primary": "low",   # workbook Tier 5   -> LOW
        "cmo-primary": "med",   # workbook Tier 2-3 -> MED
    }
    reg = _repo_registry()
    for module, tier in expected.items():
        card = yaml.safe_load(
            (cards_dir / f"{module.split('-')[0]}.yaml").read_text(encoding="utf-8")
        )
        assert card["systemPromptRef"] == f"{module.split('-')[0]}/primary@v1"
        assert reg.resolve(module).model_tier_hint == tier


# ------------------------------------------------------------ CLI surfaces
def test_evals_cli_reports_failure(capsys: pytest.CaptureFixture) -> None:
    """`evals.py report --fail-on-eval-failure` exits nonzero on a red case."""
    rc = evals.main(["report", "--data", str(CASES_PATH), "--fail-on-eval-failure"])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_registry_evals_cli_passes_for_published_module(
    capsys: pytest.CaptureFixture,
) -> None:
    rc = registry_mod.main(["evals", "ceo-primary", "v1"])
    assert rc == 0
    assert "evals: PASS" in capsys.readouterr().out


def test_registry_evals_cli_fails_for_failing_candidate(
    capsys: pytest.CaptureFixture,
) -> None:
    """The CLI surfaces the refusal with a nonzero exit (no-false-green)."""
    rc = registry_mod.main(["evals", "ao-empty", "v1"])
    assert rc == 1
    assert "evals: FAIL" in capsys.readouterr().err
