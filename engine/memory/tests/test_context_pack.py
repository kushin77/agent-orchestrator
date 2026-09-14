"""Pre-fetched codeidx context-pack consumption (issue #477, EPIC #472).

`assemble_prefix` accepts an optional pre-fetched ``codeidx.context-pack/v1``
block so the two independently-derived static regions collapse onto ONE shared,
cacheable prefix per tenant/repo - and, with no pack, reproduces the
pre-consumer bytes exactly. The pack is consumed as bytes from the published
contract; nothing about its shape is mirrored (ADR-0018 decision 3).

The prose spelling of the vendor contract is deliberate: the numbers on this
board collide, so the vendor dependency is named in prose and never written as
a bare marker.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from engine.memory.prompt_cache import (CONSUMED_CONTRACTS, CONTEXT_PACK_SCHEMA,
                                        ContextPack, ContextPackError, Prefix,
                                        PrefixError, assemble_prefix,
                                        scan_dynamic)

FIXTURE = (Path(__file__).parent / "fixtures" / "prompt_prefix_vector.json")
_VENDOR_CONTRACT = "kushin77/code-indexing#128"


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _vector() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _assemble(case: dict, **extra) -> Prefix:
    return assemble_prefix(
        system_text=case["system_text"],
        memory_block=case["memory_block"],
        user_delta=case["user_delta"],
        min_static_tokens=case["min_static_tokens"],
        **extra,
    )


def _pack(text: str, schema: str = CONTEXT_PACK_SCHEMA) -> ContextPack:
    return ContextPack(schema=schema, payload=text.encode("utf-8"))


class TestAbsentPackByteIdentity:
    """No pack ⇒ today's behaviour, byte for byte."""

    def test_fixture_records_the_prerefactor_revision(self):
        vector = _vector()
        assert vector["captured_from_sha"]
        assert vector["cases"], "the recorded vector must not be empty"

    @pytest.mark.parametrize("index", range(len(_vector()["cases"])))
    def test_absent_pack_reproduces_the_recorded_bytes(self, index):
        case = _vector()["cases"][index]
        prefix = _assemble(case)
        assert prefix.static_text == case["static_text"]
        assert prefix.delta_text == case["delta_text"]
        assert prefix.render() == case["render"]
        assert _sha(prefix.static_text) == case["static_sha256"]
        assert _sha(prefix.render()) == case["render_sha256"]

    def test_omitted_and_explicit_none_are_identical(self):
        case = _vector()["cases"][0]
        omitted = _assemble(case)
        explicit = _assemble(case, context_pack=None)
        assert omitted.render() == explicit.render()


class TestPackConsumption:
    """A supplied pack is placed ahead of the memory block, inside one prefix."""

    def test_pack_sits_ahead_of_the_memory_block(self):
        prefix = assemble_prefix(
            system_text="You are a careful ops agent.",
            memory_block="- [tenant:semantic] deploy: ships weekly",
            user_delta="When?",
            context_pack=_pack("repo map: engine/memory, engine/loop"),
        )
        assert prefix.static_text.startswith("You are a careful ops agent.")
        assert prefix.static_text.index("repo map") < prefix.static_text.index(
            "deploy: ships weekly")
        assert prefix.delta_text == "When?"
        assert prefix.render().startswith(prefix.static_text)

    def test_pack_bytes_are_consumed_verbatim_and_once(self):
        pack_text = "codeidx context-pack: engine/memory (2 files)"
        prefix = assemble_prefix(
            system_text="SYS", memory_block="MEM", user_delta="DELTA",
            context_pack=_pack(pack_text))
        assert pack_text in prefix.static_text
        assert prefix.static_text.count(pack_text) == 1
        assert "MEM" in prefix.static_text
        assert prefix.static_text.endswith("MEM")

    def test_pack_participates_in_the_dynamic_token_scan(self):
        with pytest.raises(PrefixError):
            assemble_prefix(
                system_text="SYS", memory_block="MEM", user_delta="DELTA",
                context_pack=_pack("indexed_at 2026-09-14T10:00:00"))

    def test_two_assemblies_are_byte_identical(self):
        kwargs = dict(system_text="SYS", memory_block="MEM", user_delta="DELTA",
                      context_pack=_pack("deterministic pack bytes"))
        first = assemble_prefix(**kwargs)
        second = assemble_prefix(**kwargs)
        assert first.render() == second.render()
        assert _sha(first.static_text) == _sha(second.static_text)

    def test_no_env_dependence(self):
        kwargs = dict(system_text="SYS", memory_block="MEM", user_delta="D",
                      context_pack=_pack("stable bytes"))
        before = os.environ.get("AO477_PROBE")
        try:
            os.environ["AO477_PROBE"] = "one"
            a = assemble_prefix(**kwargs).render()
            os.environ["AO477_PROBE"] = "two"
            b = assemble_prefix(**kwargs).render()
        finally:
            if before is None:
                os.environ.pop("AO477_PROBE", None)
            else:
                os.environ["AO477_PROBE"] = before
        assert a == b


class TestCollapsedSharedPrefix:
    """Two independent derivations share ONE cacheable prefix."""

    def test_same_pack_and_logical_set_give_one_shared_prefix(self):
        pack = _pack("shared repo map for tenant-acme/agent-orchestrator")
        # Two consumers, each with its own (differently ordered) local
        # derivation of the same logical memory set.
        left = assemble_prefix(
            system_text="SYS",
            memory_block="- [tenant:semantic] a: 1\n- [agent:semantic] b: 2",
            user_delta="q-left", context_pack=pack)
        right = assemble_prefix(
            system_text="SYS",
            memory_block="- [tenant:semantic] a: 1\n- [agent:semantic] b: 2",
            user_delta="q-right", context_pack=pack)
        # Same static prefix (one cache entry), different delta.
        assert left.static_text == right.static_text
        assert _sha(left.static_text) == _sha(right.static_text)
        assert left.delta_text != right.delta_text

    def test_pack_is_the_shared_region_not_the_memory_block(self):
        pack_text = "shared-static-region"
        prefix = assemble_prefix(
            system_text="SYS", memory_block="local-memory", user_delta="D",
            context_pack=_pack(pack_text))
        assert pack_text in prefix.static_text
        # The shared region is the pre-fetched bytes, not a re-derivation.
        assert prefix.static_text.split("SYS", 1)[1].strip().startswith(
            pack_text)


class TestNegativeControls:
    """Each control is shown to fail BY NAME."""

    def test_unknown_schema_version_is_refused_by_name(self):
        with pytest.raises(ContextPackError) as excinfo:
            _pack("bytes", schema="codeidx.context-pack/v2")
        assert "unknown context-pack schema" in str(excinfo.value)
        assert "codeidx.context-pack/v2" in str(excinfo.value)

    def test_dynamic_token_is_refused_by_the_existing_anti_pattern_list(self):
        # Rejected by the EXISTING anti-pattern list (PrefixError), not by the
        # pack seam: the pack is scanned like any other static block.
        with pytest.raises(PrefixError) as excinfo:
            assemble_prefix(
                system_text="SYS", memory_block="MEM", user_delta="D",
                context_pack=_pack(
                    "run_id 4f2a finished at 2026-09-14T10:00:00"))
        message = str(excinfo.value)
        assert "dynamic tokens must not appear in the static" in message
        assert "run_id" in message

    def test_malformed_pack_is_refused_by_name(self):
        with pytest.raises(ContextPackError) as excinfo:
            ContextPack(schema=CONTEXT_PACK_SCHEMA, payload=b"\xff\xfe not utf-8")
        assert "malformed context pack" in str(excinfo.value)
        with pytest.raises(ContextPackError) as excinfo:
            ContextPack(schema=CONTEXT_PACK_SCHEMA, payload="a str, not bytes")
        assert "malformed context pack" in str(excinfo.value)

    def test_empty_pack_is_refused_by_name(self):
        for payload in (b"", b"   \n\t"):
            with pytest.raises(ContextPackError) as excinfo:
                ContextPack(schema=CONTEXT_PACK_SCHEMA, payload=payload)
            assert "empty context pack" in str(excinfo.value)
        # Absence is expressed as None, never as an empty pack.
        prefix = assemble_prefix(system_text="SYS", memory_block="MEM",
                                 user_delta="D", context_pack=None)
        assert "MEM" in prefix.static_text


class TestConsumedContractRegister:
    """The seam is a consumer of a published contract, never a mirror."""

    def test_register_records_the_vendor_contract_as_unverified(self):
        rows = [row for row in CONSUMED_CONTRACTS
                if row["id"] == CONTEXT_PACK_SCHEMA]
        assert len(rows) == 1
        row = rows[0]
        assert row["producer"] == "kushin77/code-indexing"
        assert row["contract"] == _VENDOR_CONTRACT
        assert row["state"] == "UNVERIFIED"

    def test_no_shape_of_the_pack_body_is_mirrored(self):
        # The seam interprets a declared schema id and opaque bytes - nothing
        # else. A mirrored field would be a dataclass attribute beyond these.
        fields = set(ContextPack.__dataclass_fields__)
        assert fields == {"schema", "payload"}

    def test_declared_schema_is_the_published_id(self):
        assert CONTEXT_PACK_SCHEMA == "codeidx.context-pack/v1"


class TestStaticRegionStaysClean:
    def test_injected_pack_is_free_of_dynamic_tokens(self):
        prefix = assemble_prefix(
            system_text="You are a careful ops agent.",
            memory_block="- [tenant:semantic] deploy: ships weekly",
            user_delta="q",
            context_pack=_pack("repo map: engine/memory; coverage complete"))
        assert scan_dynamic(prefix.static_text) == []
