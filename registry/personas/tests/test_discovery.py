"""Dir-scan discovery: adding a card file adds a persona with no code change.

Covers acceptance: file-per-persona discovery under registry/personas/cards/.
"""

import pytest

from registry import InvalidCardError, PersonaError, UnknownPersonaError


def test_adding_a_file_adds_a_persona(scratch):
    reg = scratch.write_registry(
        {
            "alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha"),
            "beta.yaml": scratch.card_yaml(id="beta", name="Beta"),
        }
    )
    first = reg.discover()
    assert set(first) == {("platform", "alpha"), ("platform", "beta")}

    # Adding a third card file is the ONLY change - no code change, no config.
    scratch.write({"gamma.yaml": scratch.card_yaml(id="gamma", name="Gamma")})
    second = reg.discover(refresh=True)
    assert len(second) == 3
    assert ("platform", "gamma") in second
    assert second[("platform", "gamma")]["name"] == "Gamma"


def test_filename_stem_must_match_id(scratch):
    reg = scratch.write_registry(
        {"wrongname.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    with pytest.raises(InvalidCardError):
        reg.discover()


def test_duplicate_identity_is_rejected(scratch):
    reg = scratch.write_registry(
        {
            "alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha"),
            "alpha2.yaml": scratch.card_yaml(id="alpha", name="Alpha clone"),
        }
    )
    with pytest.raises(PersonaError):
        reg.discover()


def test_discover_refuses_unknown_vocabulary_id(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", toolAllowlist=["not_a_tool"])}
    )
    with pytest.raises(InvalidCardError):
        reg.discover()


def test_get_unknown_persona_raises(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    with pytest.raises(UnknownPersonaError):
        reg.get("platform", "does-not-exist")
