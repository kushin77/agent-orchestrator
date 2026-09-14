"""Chat prompt modules: versioned, frozen, and required to cite their sources.

Issue #509 acceptance: each chat prompt module has a version, a frozen body and
an output schema that **requires** the citations envelope — a payload missing
citations must fail schema validation, and the registry must refuse a module
whose schema merely permits the envelope.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from registry.chat import envelope
from registry.chat.prompt_modules import (
    ChatPromptError,
    ChatPromptRegistry,
    IntegrityError,
    RenderError,
    UnknownTaskTypeError,
    VersionAlreadyPublishedError,
    module_digest,
)

VERSION_RE = re.compile(r"^v[0-9]+$")


def _modules(root: Path) -> list:
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((root / "modules").glob("*.yaml"))
    ]


def test_every_module_declares_a_version_a_frozen_body_and_a_schema(
    chat_root: Path,
) -> None:
    modules = _modules(chat_root)
    assert modules, "the chat package declares no prompt modules"
    for module in modules:
        assert VERSION_RE.match(module["version"]), module
        assert module["bodyRefs"], module
        for part, ref in module["bodyRefs"].items():
            body = (chat_root / "modules" / ref).resolve()
            assert body.is_file(), f"{module['taskType']} bodyRef {part} missing"
            assert body.read_text(encoding="utf-8").strip(), f"{part} body is empty"
        schema_path = (chat_root / "modules" / module["outputSchema"]).resolve()
        assert schema_path.is_file(), module["outputSchema"]
        json.loads(schema_path.read_text(encoding="utf-8"))


def test_every_module_schema_requires_the_citations_envelope(chat_root: Path) -> None:
    for module in _modules(chat_root):
        schema = json.loads(
            (chat_root / "modules" / module["outputSchema"]).resolve().read_text(
                encoding="utf-8"
            )
        )
        assert envelope.schema_requires_envelope(schema), module["taskType"]


def test_a_grounded_answer_without_the_envelope_fails_schema_validation(
    chat_root: Path,
) -> None:
    schema = json.loads(
        (chat_root / "output-schemas" / "grounded-answer.schema.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"answer": "AO-509 is in progress."}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"answer": "AO-509 is in progress.", "citations": []}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "answer": "AO-509 is in progress.",
                "citations": [{"fragment_id": "frag-1", "source_id": "kb:doc-1"}],
            },
            schema,
        )
    jsonschema.validate(
        {
            "answer": "AO-509 is in progress.",
            "citations": [{"fragment_id": "frag-1", "source_id": "ticket:AO-509"}],
        },
        schema,
    )


def test_a_refusal_without_the_envelope_fails_schema_validation(chat_root: Path) -> None:
    schema = json.loads(
        (chat_root / "output-schemas" / "refusal.schema.json").read_text(encoding="utf-8")
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "outcome": "no-data",
                "reason": "no supplied fragment answers the question",
                "reason_code": "NO_DATA_NO_SOURCE",
            },
            schema,
        )
    jsonschema.validate(
        {
            "outcome": "no-data",
            "reason": "no supplied fragment answers the question",
            "reason_code": "NO_DATA_NO_SOURCE",
            "citations": [],
        },
        schema,
    )


def test_the_manifest_digest_matches_every_published_module(chat_root: Path) -> None:
    registry = ChatPromptRegistry(chat_root)
    manifest = yaml.safe_load((chat_root / "manifest.yaml").read_text(encoding="utf-8"))
    published = 0
    for task_type, entry in manifest["taskTypes"].items():
        resolved = registry.resolve(task_type)
        assert resolved.version == entry["pinned"]
        assert module_digest(resolved.module) == entry["published"][-1]["digest"]
        published += 1
    assert published == len(manifest["taskTypes"]) >= 2


def test_publishing_a_version_twice_is_refused(scratch_package: Path) -> None:
    registry = ChatPromptRegistry(scratch_package)
    with pytest.raises(VersionAlreadyPublishedError):
        registry.publish("chat-answer", "v1")


def test_editing_a_published_module_is_an_integrity_violation(
    scratch_package: Path,
) -> None:
    module_path = scratch_package / "modules" / "chat-answer.v1.yaml"
    module = yaml.safe_load(module_path.read_text(encoding="utf-8"))
    assert registry_resolves(scratch_package, "chat-answer") == "v1"
    module["modelTierHint"] = "high"
    module_path.write_text(yaml.safe_dump(module, sort_keys=True), encoding="utf-8")
    registry = ChatPromptRegistry(scratch_package)
    with pytest.raises(IntegrityError):
        registry.resolve("chat-answer")


def registry_resolves(root: Path, task_type: str) -> str:
    return ChatPromptRegistry(root).resolve(task_type).version


def test_a_new_version_is_publishable_and_pinning_rolls_it_back(
    scratch_package: Path,
) -> None:
    source = (scratch_package / "modules" / "chat-answer.v1.yaml").read_text(
        encoding="utf-8"
    )
    candidate = yaml.safe_load(source)
    candidate["version"] = "v2"
    candidate["description"] = "The grounded answer module, second version."
    (scratch_package / "modules" / "chat-answer.v2.yaml").write_text(
        yaml.safe_dump(candidate, sort_keys=True), encoding="utf-8"
    )
    registry = ChatPromptRegistry(scratch_package)
    registry.publish("chat-answer", "v2")
    assert ChatPromptRegistry(scratch_package).resolve("chat-answer").version == "v2"
    registry.pin("chat-answer", "v1")
    assert ChatPromptRegistry(scratch_package).resolve("chat-answer").version == "v1"
    with pytest.raises(ChatPromptError):
        registry.pin("chat-answer", "v9")


def test_an_unregistered_task_type_is_refused(chat_root: Path) -> None:
    with pytest.raises(UnknownTaskTypeError):
        ChatPromptRegistry(chat_root).resolve("chat-improvise")


def test_render_refuses_a_leftover_placeholder(chat_root: Path) -> None:
    registry = ChatPromptRegistry(chat_root)
    with pytest.raises(RenderError):
        registry.render_prompt("chat-answer", {"tenant": "acme", "fragments": "(none)"})
    rendered = registry.render_prompt(
        "chat-answer",
        {"tenant": "acme", "fragments": "(none)", "question": "What is AO-509?"},
    )
    assert rendered["promptId"] == "chat-answer@v1"
    assert "What is AO-509?" in rendered["bodies"]["user"]


def test_the_module_schema_refuses_a_module_without_a_grounding_policy(
    chat_root: Path,
) -> None:
    registry = ChatPromptRegistry(chat_root)
    with pytest.raises(ChatPromptError):
        registry.register(
            {
                "taskType": "chat-other",
                "version": "v1",
                "bodyRefs": {"system": "../bodies/chat-answer.v1.system.md"},
                "outputSchema": "../output-schemas/grounded-answer.schema.json",
                "modelTierHint": "low",
            }
        )


def test_the_contract_check_refuses_a_schema_that_only_permits_the_envelope(
    scratch_package: Path,
) -> None:
    schema_path = scratch_package / "output-schemas" / "grounded-answer.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["required"] = ["answer"]
    schema["properties"]["citations"]["minItems"] = 0
    schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    report = ChatPromptRegistry(scratch_package).contract_report()
    violations = report["chat-answer.v1.yaml"]
    assert violations, "a schema that only permits the envelope was accepted"
    assert "citations envelope" in violations[0]


def test_the_contract_check_refuses_an_answering_policy_that_does_not_cite(
    scratch_package: Path,
) -> None:
    source = yaml.safe_load(
        (scratch_package / "modules" / "chat-answer.v1.yaml").read_text(encoding="utf-8")
    )
    source["version"] = "v2"
    source["groundingPolicy"] = "answer-without-citations"
    (scratch_package / "modules" / "chat-answer.v2.yaml").write_text(
        yaml.safe_dump(source, sort_keys=True), encoding="utf-8"
    )
    violations = ChatPromptRegistry(scratch_package).contract_report()["chat-answer.v2.yaml"]
    assert violations, "an answering-without-citations module was accepted"
    assert any("answer-without-citations" in violation for violation in violations)
