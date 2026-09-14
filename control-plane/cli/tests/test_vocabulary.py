"""The CLI's surface vs RC-2's registry — the mapping, checked rather than asserted.

The issue's acceptance is *"every verb maps to exactly one declared command id"*.
These tests are how that is enforced: the surface table is data, and every row is
resolved against the committed vocabulary. A registry edit that renames, withholds
or drops a verb the CLI speaks fails here — by name — instead of reaching an
operator as a 422 from the plane.
"""

from __future__ import annotations

import textwrap

import pytest

from _doubles import REGISTRY_RELATIVE, drop, registry_document, withhold

from aoctl import refusals, vocabulary

#: The verbs the issue names, in its own order.
ISSUE_VERBS = ("status", "verbs", "pause", "resume", "stop", "kill", "override", "audit")


@pytest.fixture()
def registry() -> vocabulary.Registry:
    return vocabulary.Registry.load()


def test_the_surface_is_exactly_the_issues_eight_verbs(registry):
    assert registry.verb_names() == ISSUE_VERBS


def test_every_verb_maps_to_exactly_one_declared_command_id(registry):
    findings = registry.check()
    assert findings == (), findings
    ids = [verb_id for _, verb_id in vocabulary.SURFACE]
    assert len(ids) == len(set(ids)), "two verbs speak the same command id"
    for verb, verb_id in vocabulary.SURFACE:
        assert verb_id in registry.verbs, f"{verb} -> {verb_id} is not declared"


def test_every_command_the_cli_speaks_is_exposed_remotely(registry):
    for verb, row in registry.surface():
        assert row.exposed, f"{verb} speaks {row.id}, which the registry withholds"


def test_the_command_ids_are_the_ones_the_issue_names(registry):
    assert {verb: registry.declared_id(verb) for verb in ISSUE_VERBS} == {
        "status": "fleet.status",
        "verbs": "fleet.verbs",
        "pause": "fleet.pause",
        "resume": "fleet.resume",
        "stop": "fleet.stop",
        "kill": "fleet.kill",
        "override": "fleet.override",
        # No `fleet.audit` is declared: the one exposed verb whose local name is
        # `audit` and whose subject is the fleet's own ledger is the board's.
        "audit": "board.audit",
    }


def test_the_irreversible_verb_is_the_one_the_registry_says_it_is(registry):
    assert registry.effect_class_of("override") == vocabulary.IRREVERSIBLE_CLASS
    assert registry.effect_class_of("override") in registry.effect_classes
    for verb in ISSUE_VERBS:
        if verb != "override":
            assert not registry.row_for_verb(verb).irreversible


def test_a_withheld_verb_is_reported_by_name(registry):
    document = withhold(registry_document(), "fleet.pause")
    edited = vocabulary.Registry(document=document)
    row = edited.row_for_verb("pause")
    assert row.exposed is False
    findings = edited.check()
    assert any("fleet.pause" in finding and "pause" in finding for finding in findings), findings
    assert "test: withheld" in " ".join(findings)


def test_a_dropped_verb_is_a_named_surface_drift(registry):
    edited = vocabulary.Registry(document=drop(registry_document(), "fleet.status"))
    with pytest.raises(vocabulary.SurfaceDrift) as caught:
        edited.row_for_verb("status")
    assert "fleet.status" in str(caught.value)
    assert any("fleet.status" in finding for finding in edited.check())


def test_an_unknown_cli_verb_is_refused_by_name():
    registry = vocabulary.Registry(document=registry_document())
    with pytest.raises(vocabulary.UnknownCliVerb) as caught:
        registry.row_for_verb("deploy")
    assert "deploy" in str(caught.value)


def test_an_unreadable_registry_is_a_named_error(tmp_path):
    with pytest.raises(vocabulary.VocabularyUnreadable):
        vocabulary.Registry.load(tmp_path / "absent.yaml")


def test_a_wrong_schema_is_a_named_error():
    document = registry_document()
    document["schema"] = "cmr.something-else/v9"
    with pytest.raises(vocabulary.VocabularyUnreadable) as caught:
        vocabulary.Registry(document=document)
    assert "cmr.control-verbs/v1" in str(caught.value)


def test_a_verb_without_an_effect_class_is_a_named_error():
    document = registry_document()
    document["verbs"][0].pop("effect_class")
    with pytest.raises(vocabulary.VocabularyUnreadable):
        vocabulary.Registry(document=document)


def test_the_clients_refusal_matrix_covers_the_registrys_closed_set(registry):
    """The plane's codes are RC-2's closed set; the client may not know fewer.

    Two of the client's statuses are **not** in the registry, and each is named
    where it comes from: ``400`` is the console's own request-shape refusal and
    ``404`` is the flag gate (ADR-0025 D1.1), which is not a verb-level refusal.
    """
    declared = set(registry.refusals)
    known = set(refusals.MATRIX)
    assert declared <= known, sorted(declared - known)
    assert known - declared == {400, 404}, sorted(known - declared)


def test_every_code_the_matrix_can_name_has_a_reason():
    for status, codes in refusals.MATRIX.items():
        for code in codes:
            assert code in refusals.REASONS, f"{status} can name {code}, which has no reason"


def test_the_registry_is_the_one_the_issue_names(registry):
    assert registry.path is not None
    assert str(registry.path).endswith(str(REGISTRY_RELATIVE))
    assert registry.verbs, "the vocabulary is empty"


def test_the_readme_lists_every_verb():
    """The README's table is a doc, so it is checked against the surface."""
    from pathlib import Path

    readme = Path(vocabulary.REPO_ROOT) / "control-plane" / "cli" / "README.md"
    text = readme.read_text(encoding="utf-8")
    for verb, verb_id in vocabulary.SURFACE:
        assert f"`{verb}`" in text, f"the README does not name {verb}"
        assert f"`{verb_id}`" in text, f"the README does not name {verb_id}"


def test_the_surface_table_renders_every_row():
    from aoctl import cli

    rendered = cli.surface_table(vocabulary.Registry(document=registry_document()))
    for verb, verb_id in vocabulary.SURFACE:
        assert verb in rendered and verb_id in rendered


def test_the_surface_table_survives_an_unreadable_registry():
    """``--help`` must work on a checkout whose vocabulary cannot be read."""
    from aoctl import cli

    rendered = cli.surface_table(None)
    for verb, verb_id in vocabulary.SURFACE:
        assert verb in rendered and verb_id in rendered
    assert "registry unavailable" in rendered


def test_the_module_docstring_states_the_audit_mapping():
    """The one mapping a reader would question is explained where it is made."""
    doc = vocabulary.__doc__ or ""
    assert "board.audit" in doc
    assert "fleet.audit" in textwrap.dedent(doc)
