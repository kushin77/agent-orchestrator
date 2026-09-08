"""Tests for the versioned prompt registry (issue #13, acceptance criteria 1-2, 5).

Covers:
- PromptModule files validate against prompt-module.schema.json
- published versions are immutable: publishing twice fails, and editing a
  published module is detected as an integrity violation (no-false-green)
- runtime resolves the pinned version per taskType
- unregistered taskTypes are refused (governance: no ad-hoc unversioned prompts)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

import registry
import registry as registry_mod
from registry import (
    IntegrityError,
    PromptRegistry,
    UnknownTaskTypeError,
    VersionAlreadyPublishedError,
    VersionNotFoundError,
)

SEED_MODULES = sorted(p.name for p in (registry_mod.MODULES_DIR).glob("*.yaml"))


def _repo_registry() -> PromptRegistry:
    return PromptRegistry()


def _tmp_registry(tmp_path: Path) -> PromptRegistry:
    """A writable copy of the prompt-library tree for mutation tests."""
    root = tmp_path / "reg"
    shutil.copytree(registry_mod.PKG_DIR, root)
    return PromptRegistry(root=root)


# ---------------------------------------------------------------- schema
@pytest.mark.parametrize("module_file", SEED_MODULES)
def test_seed_modules_validate_against_schema(module_file: str) -> None:
    module = yaml.safe_load((registry_mod.MODULES_DIR / module_file).read_text())
    registry.validate_with_schema(module)  # raises PromptModuleError on failure


def test_schema_requires_governance_fields() -> None:
    with pytest.raises(registry.PromptModuleError):
        registry.validate_with_schema(
            {"taskType": "ad-hoc-inline", "version": "v1"}  # missing bodyRefs etc
        )


# --------------------------------------------------------------- integrity
def test_repo_manifest_integrity_passes() -> None:
    reg = _repo_registry()
    for task_type in reg._manifest["taskTypes"]:
        resolved = reg.resolve(task_type)  # raises IntegrityError if content drifted
        assert resolved.module["version"] == reg._manifest["taskTypes"][task_type]["pinned"]


def test_publish_twice_fails(tmp_path: Path) -> None:
    reg = _tmp_registry(tmp_path)
    reg.register(
        module={
            "taskType": "translate",
            "version": "v1",
            "modelTierHint": "med",
            "bodyRefs": {
                "system": "../bodies/summarize.v1.system.md",
                "user": "../bodies/summarize.v1.user.md",
            },
            "outputSchema": "../output-schemas/summarize.schema.json",
            "description": "test-only module for immutability coverage",
        }
    )
    reg.publish("translate", "v1")
    with pytest.raises(VersionAlreadyPublishedError):
        reg.publish("translate", "v1")


def test_published_content_is_frozen(tmp_path: Path) -> None:
    """Editing a published module is an integrity violation, not a silent change."""
    reg = _tmp_registry(tmp_path)
    reg.resolve("summarize")  # healthy before the edit
    module_file = reg.modules_dir / "summarize.v1.yaml"
    text = module_file.read_text(encoding="utf-8")
    module_file.write_text(text.replace("control-plane portal", "edited after publish"))
    with pytest.raises(IntegrityError):
        reg.resolve("summarize")


def test_publish_missing_module_fails(tmp_path: Path) -> None:
    reg = _tmp_registry(tmp_path)
    with pytest.raises(VersionNotFoundError):
        reg.publish("never-declared", "v1")


# ---------------------------------------------------------- pinned resolution
def test_resolve_returns_pinned_version() -> None:
    reg = _repo_registry()
    resolved = reg.resolve("classify-route")
    assert resolved.version == "v2"  # manifest pins v2
    assert resolved.prompt_id == "classify-route@v2"
    assert resolved.module["version"] == "v2"


def test_pin_rolls_resolution_to_published_version(tmp_path: Path) -> None:
    reg = _tmp_registry(tmp_path)
    assert reg.resolve("classify-route").version == "v2"
    reg.pin("classify-route", "v1")  # rollback to an already-published version
    assert reg.resolve("classify-route").version == "v1"


def test_pin_to_unpublished_version_fails(tmp_path: Path) -> None:
    reg = _tmp_registry(tmp_path)
    with pytest.raises(VersionNotFoundError):
        reg.pin("classify-route", "v99")


# ---------------------------------------------------------------- governance
def test_unregistered_task_type_is_refused() -> None:
    reg = _repo_registry()
    with pytest.raises(UnknownTaskTypeError):
        reg.resolve("ad-hoc-inline")


def test_governance_check_rejects_unregistered_calls() -> None:
    reg = _repo_registry()
    violations = reg.governance_check({"calls": [{"taskType": "classify-route"}, {"taskType": "ad-hoc-inline"}]})
    assert len(violations) == 1
    assert "ad-hoc-inline" in violations[0]


def test_governance_check_passes_seed_call_plan() -> None:
    reg = _repo_registry()
    plan_path = registry_mod.PKG_DIR / "seed" / "call-plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    assert reg.governance_check(plan) == []


# ------------------------------------------------------------------- render
def test_render_prompt_substitutes_variables() -> None:
    reg = _repo_registry()
    rendered = reg.render_prompt("classify-route", {"input": "refund please"})
    assert "{{input}}" not in rendered["bodies"]["user"]
    assert "refund please" in rendered["bodies"]["user"]
    assert rendered["outputSchema"].endswith("classify-route.schema.json")


def test_render_rejects_leftover_placeholders() -> None:
    reg = _repo_registry()
    with pytest.raises(registry.RenderError):
        reg.render_prompt("classify-route", {})  # user body needs {{input}}
