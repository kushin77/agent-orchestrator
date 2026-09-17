"""Mapper tests: determinism, conformance, boundary and the negative control."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from integrations.hermes import mapping as mapping_mod

ROOT = Path(__file__).resolve().parents[3]

#: The source files the projection reads — copied into scratch trees so the
#: negative control mutates a throwaway, never the real tree.
SOURCE_FILES = (
    mapping_mod.PERSONA_PATH,
    mapping_mod.PROFILE_PATH,
    mapping_mod.TIERS_PATH,
    mapping_mod.MODULE_JSON_PATH,
)


def _scratch_tree() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="ao942-hermes-"))
    for rel in SOURCE_FILES:
        dest = scratch / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    return scratch


def test_projection_is_deterministic():
    first = mapping_mod.build_projection(ROOT)
    second = mapping_mod.build_projection(ROOT)
    assert mapping_mod.canonical_document(first) == mapping_mod.canonical_document(second)
    assert mapping_mod.canonical_sha(first) == mapping_mod.canonical_sha(second)


def test_projection_conforms_to_the_contract():
    findings, projection = mapping_mod.check_projection(ROOT)
    assert findings == [], findings
    assert projection["persona"]["tier"] == "MED"
    assert set(projection["persona"]["capabilities"]) == {
        "code-author", "test-author", "test-run", "memory-ops"
    }


def test_boundary_names_the_service_and_excludes_the_namesake():
    projection = mapping_mod.build_projection(ROOT)
    assert projection["hermes"]["bound"] == mapping_mod.BOUND
    assert projection["hermes"]["service"]["port"] == 9501
    assert projection["hermes"]["service"]["endpoints"] == [
        "/health", "/api/capabilities", "/api/router", "/api/tiering"
    ]
    assert projection["hermes"]["namesake_excluded"]["provider"] == (
        "gateway/providers/hermes.py"
    )
    assert projection["hermes"]["namesake_excluded"]["model"] == "hermes3"


def test_negative_control_phantom_capability_is_refused_by_name():
    scratch = _scratch_tree()
    try:
        persona = scratch / mapping_mod.PERSONA_PATH
        original = persona.read_text(encoding="utf-8")
        mutated = original.replace("  - test-run\n", "  - test-run\n  - phantom-ops\n", 1)
        assert mutated != original, "the mutation did not change the persona card"
        persona.write_text(mutated, encoding="utf-8")

        findings, _ = mapping_mod.check_projection(scratch)
        assert any("phantom-ops" in finding for finding in findings), findings
        assert any("capabilitySet drift" in finding for finding in findings), findings
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
