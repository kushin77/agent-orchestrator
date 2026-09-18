"""The declared control policy is read, not duplicated (issue #885).

Each test mutates a *temporary copy* of ``controls.yaml`` and proves the code
that is supposed to read it actually changes behaviour — never touching the
real file. A test that only asserts the packaged declaration parses would not
prove the code reads it at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.isolation import policy  # noqa: E402


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_packaged_controls_load_cleanly():
    controls = policy.load()
    assert controls.repo_slug_default == "kushin77/agent-orchestrator"
    assert controls.speculative_default_base == "master"
    assert controls.session_id_len == 12


def test_mutating_repo_slug_changes_identity_module_behaviour(tmp_path):
    """identity.py must READ the policy, not hard-code REPO_SLUG_DEFAULT."""
    doc = _load_yaml(policy.DEFAULT_CONTROLS)
    doc["identity"]["repo_slug_default"] = "acme-corp/mutated-repo"
    mutant = tmp_path / "controls.yaml"
    _write_yaml(mutant, doc)

    controls = policy.load(mutant)
    assert controls.repo_slug_default == "acme-corp/mutated-repo"

    # Reload identity.py against the mutant policy and prove the mutation is
    # load-bearing: the module-level constant tracks the file, not a copy.
    import governance.isolation.identity as identity_module

    original_controls = identity_module._CONTROLS
    try:
        identity_module._CONTROLS = controls
        identity_module.REPO_SLUG_DEFAULT = controls.repo_slug_default
        mint = identity_module.mint(1, "test-agent", worktree_root=tmp_path)
        assert mint.repo_slug == "acme-corp/mutated-repo"
        assert mint.trailer == "Refs acme-corp/mutated-repo#1"
    finally:
        identity_module._CONTROLS = original_controls
        identity_module.REPO_SLUG_DEFAULT = original_controls.repo_slug_default


def test_mutating_speculative_default_base_changes_speculative_module():
    """speculative.py must READ the policy, not hard-code DEFAULT_BASE='master'."""
    doc = _load_yaml(policy.DEFAULT_CONTROLS)
    doc["speculative"]["default_base"] = "mutated-trunk"
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mutant = Path(tmp) / "controls.yaml"
        _write_yaml(mutant, doc)
        controls = policy.load(mutant)
        assert controls.speculative_default_base == "mutated-trunk"

    import governance.isolation.speculative as speculative_module

    original = speculative_module.DEFAULT_BASE
    try:
        speculative_module.DEFAULT_BASE = controls.speculative_default_base
        assert speculative_module.DEFAULT_BASE == "mutated-trunk"
    finally:
        speculative_module.DEFAULT_BASE = original


def test_missing_refusal_code_is_refused_at_load(tmp_path):
    """A declaration missing a code the surface can emit is CANNOT-ASSESS, never a silent pass."""
    doc = _load_yaml(policy.DEFAULT_CONTROLS)
    doc["refusal_codes"] = [c for c in doc["refusal_codes"] if c != "identity-mismatch"]
    mutant = tmp_path / "controls.yaml"
    _write_yaml(mutant, doc)
    with pytest.raises(policy.PolicyUnavailable, match="identity-mismatch"):
        policy.load(mutant)


def test_unknown_refusal_code_is_refused_at_load(tmp_path):
    doc = _load_yaml(policy.DEFAULT_CONTROLS)
    doc["refusal_codes"].append("made-up-code-nobody-emits")
    mutant = tmp_path / "controls.yaml"
    _write_yaml(mutant, doc)
    with pytest.raises(policy.PolicyUnavailable, match="made-up-code-nobody-emits"):
        policy.load(mutant)


def test_wrong_schema_tag_is_refused(tmp_path):
    doc = _load_yaml(policy.DEFAULT_CONTROLS)
    doc["schema"] = "not-the-declared-schema"
    mutant = tmp_path / "controls.yaml"
    _write_yaml(mutant, doc)
    with pytest.raises(policy.PolicyUnavailable):
        policy.load(mutant)
