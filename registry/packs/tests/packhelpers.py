"""Shared helpers for the registry/packs pytest suite (issue #40).

Bootstraps sys.path so the ``packs`` package (under registry/) is importable,
and provides builders for valid/signed AgentPack documents plus the real
schema/catalog/public-key fixtures. Not a conftest (the drift gate keys on
conftest.py); test modules import this directly.
"""
import base64
import hashlib
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PACKS_DIR = os.path.dirname(TESTS_DIR)          # registry/packs
REGISTRY_DIR = os.path.dirname(PACKS_DIR)       # registry
if REGISTRY_DIR not in sys.path:
    sys.path.insert(0, REGISTRY_DIR)

import yaml  # noqa: E402

from packs import attestation  # noqa: E402

SCHEMA_PATH = os.path.join(PACKS_DIR, "agent-pack.schema.json")
CATALOG_PATH = os.path.join(PACKS_DIR, "pack-catalog.yaml")
PUBLIC_KEY_PATH = os.path.join(PACKS_DIR, "publisher-key.pem")
RELEASES_DIR = os.path.join(PACKS_DIR, "releases")
MANIFEST_PATH = os.path.join(PACKS_DIR, "versions", "manifest.yaml")
FIXTURES_DIR = os.path.join(PACKS_DIR, "tests", "fixtures")
UPSTREAM = "https://github.com/kushin77/agent-orchestrator"


def load_schema():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_catalog():
    with open(CATALOG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_public_key():
    with open(PUBLIC_KEY_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def keypair():
    return attestation.generate_keypair()


def tool_entry():
    data = b"kind: tool\nname: gh_issue\n"
    return {"ref": "worker-tools", "data": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest()}


def pack_doc(pack_id="worker-platform", version="1.0.0", category="coding",
             lifecycle="live", upstream=UPSTREAM, name="Worker platform",
             description="Default worker bundle", tags=None, deps=None,
             with_attestation=True):
    """A schema-valid AgentPack dict (dummy structural attestation)."""
    doc = {
        "schema": "agent-pack/v1",
        "id": pack_id,
        "version": version,
        "name": name,
        "category": category,
        "upstream": upstream,
        "publisher": "platform/execution",
        "lifecycle": lifecycle,
        "description": description,
        "tags": tags if tags is not None else [category],
        "dependencies": deps if deps is not None else [],
        "contents": {"tool": [tool_entry()]},
    }
    if with_attestation:
        doc["attestation"] = {
            "kid": "ao-pack-publisher-v1",
            "alg": "PS256",
            "signedAt": "2026-09-08T00:00:00Z",
            "signature": "QUFBQUFBQUFBQUFBQUFBQQ==",
        }
    return doc


def signed_doc(private_pem, pack_id="worker-platform", version="1.0.0",
               category="coding", lifecycle="live", **overrides):
    """A real, attestation-signed AgentPack dict (consumer-trust tests)."""
    doc = pack_doc(pack_id=pack_id, version=version, category=category,
                   lifecycle=lifecycle)
    doc.update(overrides)
    return attestation.sign_pack(doc, private_pem, kid="ao-pack-publisher-v1")


# -- Skill Studio fixtures (issue #640) ---------------------------------------

SKILL_CASES_PATH = os.path.join(FIXTURES_DIR, "skill-eval-cases.yaml")


def skill_evidence(cases=2, passed=2, failed=0, eval_id="codemod@1.0.0"):
    """A workbook-8-shaped eval-evidence block for a skill (issue #640)."""
    return {"harness": "registry/prompts/evals.py", "evalId": eval_id,
            "cases": cases, "passed": passed, "failed": failed}


def skill_content(skill_id="codemod", version="1.0.0"):
    return ("skill: %s\nversion: %s\nsteps:\n  - apply-patch\n  - run-tests\n"
            % (skill_id, version)).encode("utf-8")


def skill_entry(skill_id="codemod", version="1.0.0",
                category="code-authoring", lifecycle="published",
                evidence=None, content=None):
    """A schema-valid ``contents.skill`` entry (issue #640)."""
    from packs.skills import skill_artifact_entry  # noqa: E402
    return skill_artifact_entry(
        skill_id, version, category,
        content if content is not None else skill_content(skill_id, version),
        evidence=evidence if evidence is not None
        else skill_evidence(eval_id="%s@%s" % (skill_id, version)),
        lifecycle=lifecycle)


def skill_pack_doc(private_pem, pack_id="worker-platform", version="1.0.0",
                   skill_id="codemod", skill_version="1.0.0",
                   category="code-authoring", evidence=None):
    """A signed pack that bundles exactly one published skill (issue #640)."""
    doc = pack_doc(pack_id=pack_id, version=version)
    doc["contents"] = {"skill": [skill_entry(
        skill_id=skill_id, version=skill_version, category=category,
        evidence=evidence)]}
    return attestation.sign_pack(doc, private_pem, kid="ao-pack-publisher-v1")
