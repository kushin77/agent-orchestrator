"""Schema and config validation — a config we cannot trust is never a pass (#147)."""

from __future__ import annotations

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
    "governance_cto_overlay_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
engine = _conftest.engine


def test_shipped_config_validates(make_repo):
    root = make_repo("shipped")
    config = engine.load_config(root)
    assert config.repo_name == "agent-orchestrator"
    assert config.repo_owner == "kushin77"
    assert set(config.layers) == set(engine.LAYERS)
    assert list(config.non_negotiable) == list(engine.SIGNALS)
    for layer_id in engine.LAYERS:
        assert config.layers[layer_id].checks, f"{layer_id} declares no checks"


def test_missing_layer_is_refused(make_repo):
    def mutate(document):
        del document["layers"]["support"]

    root = make_repo("no-support", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="support"):
        engine.load_config(root)


def test_dropped_non_negotiable_signal_is_refused(make_repo):
    def mutate(document):
        document["non_negotiable"] = [
            name for name in document["non_negotiable"] if name != "path_integrity"
        ]

    root = make_repo("dropped-signal", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="fewer than 4 item"):
        engine.load_config(root)


@pytest.mark.parametrize("signal", ["shell_syntax", "secret_scan", "protected_files", "path_integrity"])
def test_each_non_negotiable_signal_is_required(make_repo, signal):
    """No signal can be dropped, and none can be swapped for something else.

    With four unique values allowed and four required, the only config that
    validates is one that lists all four — which is the point of the baseline.
    """

    def dropped(document):
        document["non_negotiable"] = [name for name in document["non_negotiable"] if name != signal]

    with pytest.raises(engine.ConfigError, match="fewer than 4 item"):
        engine.load_config(make_repo(f"dropped-{signal}", mutate=dropped))

    def substituted(document):
        document["non_negotiable"] = [
            name for name in document["non_negotiable"] if name != signal
        ] + ["bash_continuity"]

    with pytest.raises(engine.ConfigError, match="not one of"):
        engine.load_config(make_repo(f"substituted-{signal}", mutate=substituted))


def test_validator_enforces_the_keywords_it_implements():
    validator = engine.SchemaValidator(
        {
            "type": "object",
            "required": ["items"],
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "allOf": [{"contains": {"const": "keep"}}],
                }
            },
        }
    )
    validator.validate({"items": ["drop", "keep"]})
    with pytest.raises(engine.ConfigError, match="must contain"):
        validator.validate({"items": ["drop"]})
    with pytest.raises(engine.ConfigError, match="unknown key"):
        validator.validate({"items": ["keep"], "extra": 1})
    with pytest.raises(engine.ConfigError, match="required key"):
        validator.validate({})


def test_wrong_type_is_refused(make_repo):
    def mutate(document):
        document["layers"]["executive"]["blocking"] = "yes"

    root = make_repo("wrong-type", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="expected type"):
        engine.load_config(root)


def test_unknown_key_is_refused(make_repo):
    def mutate(document):
        document["repo"]["nickname"] = "ao"

    root = make_repo("unknown-key", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="unknown key"):
        engine.load_config(root)


def test_unimplemented_check_is_refused(make_repo):
    def mutate(document):
        document["layers"]["executive"]["checks"].append({"id": "adr-check-typo"})

    root = make_repo("unknown-check", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="does not implement"):
        engine.load_config(root)


def test_version_pattern_is_enforced(make_repo):
    def mutate(document):
        document["overlay"]["version"] = "1.0"

    root = make_repo("bad-version", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="does not match"):
        engine.load_config(root)


def test_contradicting_tier_entry_is_refused(make_repo):
    def mutate(document):
        document["tiers"]["standard"]["executive"] = "advisory"

    root = make_repo("contradiction", mutate=mutate)
    with pytest.raises(engine.ConfigError, match="contradiction"):
        engine.load_config(root)


def test_schema_keyword_the_engine_cannot_enforce_is_refused(make_repo):
    root = make_repo(
        "bad-schema", schema_mutate=lambda text: text + "\nunknownKeyword: true\n"
    )
    with pytest.raises(engine.EngineError, match="does not implement"):
        engine.load_config(root)


def test_missing_config_cannot_be_assessed(make_repo):
    root = make_repo("no-config", drop_config=True)
    with pytest.raises(engine.ConfigError, match="not found"):
        engine.load_config(root)


def test_non_mapping_config_is_refused(make_repo):
    root = make_repo("list-config")
    (root / "governance" / "cto-overlay" / engine.CONFIG_NAME).write_text(
        "- one\n- two\n", encoding="utf-8"
    )
    with pytest.raises(engine.ConfigError, match="mapping"):
        engine.load_config(root)


def test_severity_resolution_precedence(make_repo):
    def mutate(document):
        document["tiers"]["critical"]["support"] = "advisory"
        document["layers"]["support"]["enabled"] = False

    root = make_repo("severity", mutate=mutate)
    config = engine.load_config(root)
    assert config.effective_severity("executive", "standard") == "blocking"
    assert config.effective_severity("devops", "standard") == "warning"
    assert config.effective_severity("devops", "critical") == "blocking"
    assert config.effective_severity("support", "critical") == "disabled"
