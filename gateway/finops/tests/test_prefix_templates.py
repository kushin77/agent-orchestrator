"""Prefix-template contract tests (PF-5, issue #670).

Covers:
- every registered call class has a canonical, normalisation-stable template
- normalize strips volatile ids/timestamps while keeping the prefix identical
- clean fixtures (canonical prefix + a user delta) pass validation
- a deviating prefix is refused by name
- the predicate is MUTATION-PROVED: a template deviation must FAIL the test
  (the mutated module refuses the previously-clean fixture, naming the class),
  with the mutation's sha256 before/after quoted as evidence it landed.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

import prefix_templates


# Independent literals (NOT derived from PREFIX_TEMPLATES), so they stay clean
# even when the mutation test mutates the module. Each is the canonical prefix
# with real volatile tokens substituted, followed by a user delta.
CLEAN_FIXTURES = {
    "system-head": (
        "You are an enterprise AI-agent orchestration assistant.\n"
        "Session 3f2a1b9c-8d4e-4f1a-9b2c-3d4e5f6a7b8c started at "
        "2026-09-14T10:30:00Z.\n"
        "Respond concisely and accurately.\n"
        "User asks: summarize the report."
    ),
    "tool-schema-head": (
        "Tools available to this agent:\n"
        "- read_file(path: str) -> str\n"
        "- list_dir(path: str) -> list[str]\n"
        "- run_command(cmd: str) -> str\n"
        "Use them as needed."
    ),
    "memory-head": (
        "Memory context (scoped to this tenant's agent org):\n"
        "agent_id=agt-9f3c2b1a | retrieved_at=2026-09-14T09:00:00Z\n"
        "Memory entries follow."
    ),
    "instructions-head": (
        "Follow these instructions precisely:\n"
        "1. Stay in your lane.\n"
        "2. Verify before you declare done.\n"
        "Then do the task."
    ),
    "user-head": "User request:\nSummarize the report.",
}


# --- registry ---------------------------------------------------------------


def test_registry_covers_the_five_call_classes():
    assert prefix_templates.REGISTERED_CLASSES == {
        "system-head",
        "tool-schema-head",
        "memory-head",
        "instructions-head",
        "user-head",
    }


def test_every_class_has_a_canonical_normalised_template():
    assert prefix_templates.REGISTERED_CLASSES == frozenset(CLEAN_FIXTURES)
    for cls in sorted(prefix_templates.REGISTERED_CLASSES):
        template = prefix_templates.canonical_template(cls)
        assert template  # non-empty
        # fully-static input is a fixed point of normalization
        assert prefix_templates.normalize(template) == template


# --- normalization ----------------------------------------------------------


def test_normalize_strips_volatile_tokens_and_keeps_prefix_identical():
    norm = prefix_templates.normalize(CLEAN_FIXTURES["system-head"])
    assert "3f2a1b9c" not in norm
    assert "2026-09-14" not in norm
    assert "<uuid>" in norm
    assert "<timestamp>" in norm
    assert norm.startswith(
        "You are an enterprise AI-agent orchestration assistant.\n"
        "Session <uuid> started at <timestamp>.\n"
        "Respond concisely and accurately."
    )


def test_normalize_keyed_id_becomes_dynamic_placeholder():
    norm = prefix_templates.normalize(CLEAN_FIXTURES["memory-head"])
    assert "agent_id=<dynamic>" in norm
    assert "agt-9f3c2b1a" not in norm


def test_normalize_makes_volatile_variants_share_an_identical_prefix():
    template = prefix_templates.canonical_template("system-head")
    base = prefix_templates.normalize(CLEAN_FIXTURES["system-head"])
    # same prefix, different volatile tokens + a different user delta
    variant = prefix_templates.normalize(
        "You are an enterprise AI-agent orchestration assistant.\n"
        "Session 00000000-0000-4000-8000-000000000000 started at "
        "2026-09-13T08:00:00Z.\n"
        "Respond concisely and accurately.\n"
        "User asks: a different question."
    )
    assert base.startswith(template)
    assert variant.startswith(template)
    # the cache-key-relevant prefix is byte-identical; only the delta differs
    assert base[: len(template)] == variant[: len(template)] == template


def test_footprint_is_stable_for_identical_input():
    assert (
        prefix_templates.footprint(CLEAN_FIXTURES["system-head"])
        == prefix_templates.footprint(CLEAN_FIXTURES["system-head"])
    )
    # a deviating prefix produces a different fingerprint
    assert prefix_templates.footprint("Other prefix.") != prefix_templates.footprint(
        CLEAN_FIXTURES["system-head"]
    )


# --- validator --------------------------------------------------------------


def test_clean_fixtures_pass_validation():
    for cls, composed in CLEAN_FIXTURES.items():
        prefix_templates.validate_prefix(cls, composed)  # must not raise


def test_deviating_prefix_is_refused_by_name():
    with pytest.raises(prefix_templates.PrefixTemplateError) as excinfo:
        prefix_templates.validate_prefix("system-head", "Totally different prefix.")
    assert "system-head" in str(excinfo.value)


def test_unknown_class_is_refused():
    with pytest.raises(prefix_templates.PrefixTemplateError):
        prefix_templates.validate_prefix("no-such-class", "anything")


# --- mutation proof ---------------------------------------------------------

#: The mutation: flip one token in the system-head canonical template so the
#: clean fixture (which still spells it correctly) no longer matches.
_MUTATION = (
    "AI-agent orchestration assistant.",
    "AI-agent orchestration asistant.",
)


def _load_mutated_module(tmp_path: Path):
    """Copy the module, flip one template token, import the copy, return shas."""
    src_path = Path(prefix_templates.__file__)
    before = hashlib.sha256(src_path.read_bytes()).hexdigest()
    text = src_path.read_text(encoding="utf-8")
    old, new = _MUTATION
    assert text.count(old) == 1  # the mutation target is unambiguous
    mutated = text.replace(old, new)
    assert mutated != text  # the mutation actually changed the source
    out_path = tmp_path / "prefix_templates.py"
    out_path.write_text(mutated, encoding="utf-8")
    after = hashlib.sha256(out_path.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location(
        "mutated_prefix_templates", str(out_path)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return before, after, module


def test_template_deviation_is_mutation_proved_and_refused(tmp_path):
    """A template deviation must FAIL: mutate the system-head template and prove
    the validator refuses the previously-clean fixture, naming the class."""
    before, after, mutated = _load_mutated_module(tmp_path)

    assert before != after  # sha256 before/after differ -> the mutation landed
    assert "asistant" in mutated.PREFIX_TEMPLATES["system-head"]

    with pytest.raises(mutated.PrefixTemplateError) as excinfo:
        mutated.validate_prefix("system-head", CLEAN_FIXTURES["system-head"])
    assert "system-head" in str(excinfo.value)
