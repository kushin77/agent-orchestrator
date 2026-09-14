"""The grounding assembler + citations envelope (issue #504, EPIC #500).

The assembler turns a turn's needs into a **cited, cache-safe** prefix: a pure
function of the logical fragment set (static first, delta last), with the token
budget it used and the hash of its cache footprint reported. The envelope maps
every fragment to a real source id and refuses a fabricated one.

Each test below fails if the behaviour it names regresses - the two negative
controls (a fabricated citation, a source injecting run identity into the
cacheable region) fail loudly rather than being repaired silently.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import sys

import pytest

from mcp.enterprise import CHAT_FAMILY_NAMES, WRITE_TOOLS
from mcp.grounding import (
    PLATFORM_SPEC,
    VOLATILE_KEYS,
    ApprovalProposal,
    Citation,
    CitationError,
    CitationsEnvelope,
    GroundingAssembler,
    GroundingError,
    GroundingRequest,
    Need,
    propose_approval,
    stable_payload,
)
from mcp.sources import (
    MODE_FIXTURE,
    MODE_PRODUCTION,
    STATUS_NO_DATA,
    STATUS_OK,
    Fragment,
    KbFixtureSource,
    SourceCatalog,
)


def prompt_cache():
    """The engine's prompt-cache discipline, imported from the checkout root."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module("engine.memory.prompt_cache")


def tree_state(root: str):
    """Every path under ``root`` with its content hash (a write detector)."""
    state = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                state[os.path.relpath(path, root)] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    return state


def _board_need(number: int = 504) -> Need:
    return Need("board", "ticket", f"#{number}", (("number", number),))


def _fleet_need() -> Need:
    return Need("fleet", "snapshot", "ao.bridge/v1")


def _ledger_need() -> Need:
    """A tenant-scoped need: it names no tenant, only what it wants read."""
    return Need("ledger", "tail", "this-session")


def _request(*needs: Need, delta: str = "What is the state of ticket 504?") -> GroundingRequest:
    return GroundingRequest(delta=delta, needs=tuple(needs))


# --------------------------------------------------------------------------- #
# byte stability + the cache discipline (the prompt_cache consumption)
# --------------------------------------------------------------------------- #
def test_the_grounded_prefix_is_byte_stable(chat_catalog):
    assembler = GroundingAssembler(chat_catalog)
    first = assembler.assemble(_request(_board_need(), _fleet_need()))
    second = assembler.assemble(_request(_board_need(), _fleet_need()))
    assert first.render() == second.render()
    assert first.cache_footprint == second.cache_footprint
    assert first.cache_footprint == prompt_cache().footprint(first.static_text)


def test_the_caller_order_of_needs_does_not_change_the_bytes(chat_catalog):
    assembler = GroundingAssembler(chat_catalog)
    forward = assembler.assemble(_request(_board_need(), _fleet_need()))
    reverse = assembler.assemble(_request(_fleet_need(), _board_need()))
    assert forward.render() == reverse.render()
    assert forward.cache_footprint == reverse.cache_footprint


def test_the_static_region_carries_no_dynamic_tokens(chat_catalog):
    turn = GroundingAssembler(chat_catalog, tenant_id="acme").assemble(
        _request(_board_need(), _fleet_need(), _ledger_need())
    )
    assert prompt_cache().scan_dynamic(turn.static_text) == []
    # the engine's own strict scan is what guarantees it
    prompt_cache().validate_static_region(turn.static_text)


def test_the_delta_is_last_and_the_spec_is_first(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(
        _request(_board_need(), delta="Who owns the ledger?")
    )
    rendered = turn.render()
    assert rendered.startswith("You are a grounded enterprise assistant")
    assert rendered.rstrip().endswith("Who owns the ledger?")
    assert rendered.index("Who owns the ledger?") > rendered.index("[grounding]")


def test_volatile_payload_fields_never_reach_the_cacheable_prefix(chat_catalog):
    turn = GroundingAssembler(chat_catalog, tenant_id="acme").assemble(
        _request(_ledger_need())
    )
    assert turn.fragments, "the fixture ledger has a chain to ground on"
    for key in VOLATILE_KEYS:
        assert f'"{key}"' not in turn.static_text
    assert "ts" in turn.fragments[0].payload, "the tool result keeps the timestamp"
    assert "ts" not in stable_payload(turn.fragments[0])


def test_a_source_injecting_run_identity_is_refused_loudly():
    fragment = Fragment(
        family="board",
        locator="#1",
        authority=".board/snapshot.json",
        revision="0" * 16,
        payload={"number": 1, "session_id": "abc123"},
    )
    with pytest.raises(GroundingError) as error:
        stable_payload(fragment)
    assert "session_id" in str(error.value)
    assert "cacheable" in str(error.value)


# --------------------------------------------------------------------------- #
# the budget report
# --------------------------------------------------------------------------- #
def test_the_turn_reports_the_token_budget_it_used(chat_catalog):
    turn = GroundingAssembler(chat_catalog, budget_tokens=4000).assemble(
        _request(_board_need())
    )
    budget = turn.budget.as_dict()
    assert budget["limit"] == 4000
    assert budget["staticTokens"] > 0 and budget["deltaTokens"] > 0
    assert budget["totalTokens"] == prompt_cache().estimate_tokens(turn.render())
    assert budget["withinBudget"] is True
    assert turn.as_dict()["cacheFootprint"] == turn.cache_footprint


def test_a_fragment_that_does_not_fit_is_named_not_dropped_silently(chat_catalog):
    needs = _request(_board_need(), _fleet_need())
    full = GroundingAssembler(chat_catalog).assemble(needs)
    assert full.truncated == ()
    # a budget one fragment-line short of the full block must drop that line
    trimmed = "\n".join(full.static_text.splitlines()[:-1])
    budget = prompt_cache().estimate_tokens(trimmed)
    partial = GroundingAssembler(chat_catalog, budget_tokens=budget).assemble(needs)
    assert partial.truncated, "the dropped fragment must be named"
    assert len(partial.fragments) < len(full.fragments)
    assert set(partial.truncated) & set(partial.envelope.source_ids()) == set()
    assert partial.budget.static_within_budget


def test_a_budget_that_admits_no_fragment_is_refused_loudly(chat_catalog):
    with pytest.raises(GroundingError) as error:
        GroundingAssembler(chat_catalog, budget_tokens=1).assemble(
            _request(_board_need())
        )
    assert "admits no fragment" in str(error.value)


# --------------------------------------------------------------------------- #
# the citations envelope
# --------------------------------------------------------------------------- #
def test_every_fragment_maps_to_a_real_source_id(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(
        _request(_board_need(), _fleet_need())
    )
    assert turn.status == STATUS_OK
    assert len(turn.envelope.citations) == len(turn.fragments)
    for citation, fragment in zip(turn.envelope.citations, turn.fragments):
        assert citation.source_id == fragment.source_id
        assert citation.source_id == (
            f"{fragment.family}:{fragment.locator}@{fragment.revision}"
        )
        assert citation.revision == fragment.revision
        assert citation.kind in ("tool_call", "bridge_family", "ticket")
        assert citation.source_id in turn.render()
    # the witness the envelope validates against is the set actually read
    turn.envelope.validate(turn.envelope.source_ids())


def test_a_fabricated_source_id_is_refused():
    envelope = CitationsEnvelope(
        citations=(
            Citation(
                source_id="board:#504@deadbeefdeadbeef",
                family="board",
                authority=".board/snapshot.json",
                revision="deadbeefdeadbeef",
                kind="ticket",
            ),
        )
    )
    with pytest.raises(CitationError) as error:
        envelope.validate(["board:#504@c0ffee00c0ffee00"])
    assert "fabricated provenance" in str(error.value)
    assert "board:#504@deadbeefdeadbeef" in str(error.value)


def test_a_granted_source_id_is_accepted():
    citation = Citation(
        source_id="board:#504@c0ffee00c0ffee00",
        family="board",
        authority=".board/snapshot.json",
        revision="c0ffee00c0ffee00",
        kind="ticket",
    )
    CitationsEnvelope(citations=(citation,)).validate([citation.source_id])


def test_read_sources_without_a_citation_are_refused():
    with pytest.raises(CitationError) as error:
        CitationsEnvelope(citations=()).validate(["board:#504@c0ffee00c0ffee00"])
    assert "refuses to drop provenance" in str(error.value)


def test_a_model_citing_a_source_it_was_not_given_is_refused(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(_request(_board_need()))
    turn.verify_response(turn.envelope.source_ids())  # what it was given: fine
    with pytest.raises(CitationError) as error:
        turn.verify_response(["budget:acme@0000000000000000"])
    assert "not given" in str(error.value)


def test_fragments_carry_the_tool_call_id_when_the_turn_had_one(chat_catalog):
    need = Need("board", "ticket", "#504", (("number", 504),), call_id="call-abc123")
    turn = GroundingAssembler(chat_catalog).assemble(_request(need))
    assert turn.envelope.citations[0].call_id == "call-abc123"
    assert turn.as_dict()["callIds"] == ["call-abc123"]


# --------------------------------------------------------------------------- #
# honesty: NO_DATA, the declared fake, and the selection rule
# --------------------------------------------------------------------------- #
def test_an_absent_source_is_reported_as_no_data_with_its_reason(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(_request(_board_need(999999)))
    assert turn.status == STATUS_NO_DATA
    assert turn.fragments == ()
    assert turn.envelope.citations == ()
    assert turn.why() and "999999" in turn.why()[0]
    assert "[NO_DATA]" in turn.static_text


def test_no_data_never_renders_an_empty_success(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(
        _request(Need("codeidx", "query", "<symbol>"))
    )
    assert turn.status == STATUS_NO_DATA
    assert turn.why(), "a NO_DATA turn always says why"
    assert "kushin77/code-indexing" in turn.why()[0]
    assert turn.as_dict()["noData"] == list(turn.why())


def test_a_tenant_scoped_read_without_a_verified_tenant_is_refused(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(_request(_ledger_need()))
    assert turn.status == STATUS_NO_DATA
    assert "verified session tenant" in turn.why()[0]


def test_a_need_may_not_name_a_tenant(chat_catalog):
    need = Need("ledger", "tail", "globex", (("tenant_id", "globex"),))
    with pytest.raises(GroundingError) as error:
        GroundingAssembler(chat_catalog, tenant_id="acme").assemble(_request(need))
    assert "tenant selector" in str(error.value)


def test_the_fixture_surface_is_labelled_fixture_only():
    """The criterion: a fixture backend is labelled in code, not by habit."""
    from mcp import fixtures

    assert fixtures.FIXTURE_ONLY is True
    assert fixtures.FixtureBridge.fixture_only is True
    assert fixtures.FIXTURE_NOTE.startswith("fixture-only")
    assert "refused on a production grounding path" in fixtures.FIXTURE_NOTE
    assert {fixtures.fixture_catalog.__name__} == {"fixture_catalog"}


def test_the_production_path_refuses_the_declared_fake_index(chat_root, fixture_bridge):
    production = SourceCatalog.from_repo_root(
        chat_root, mode=MODE_PRODUCTION, bridge=fixture_bridge
    )
    production.add(KbFixtureSource(chat_root))
    assert "gateway/mcp/kb.py" in production.source("kb-fixture").authority
    result = production.call("kb-fixture", "query", repo="acme/payments")
    assert result.status == STATUS_NO_DATA
    assert "fixture-only" in result.reason
    assert "ADR-0018" in result.reason

    fixture_mode = SourceCatalog.from_repo_root(
        chat_root, mode=MODE_FIXTURE, bridge=fixture_bridge
    )
    fixture_mode.add(KbFixtureSource(chat_root))
    assert fixture_mode.call("kb-fixture", "query", repo="acme/payments").status == STATUS_OK


def test_the_production_path_refuses_a_fixture_bridge(chat_root, fixture_bridge):
    production = SourceCatalog.from_repo_root(
        chat_root, mode=MODE_PRODUCTION, bridge=fixture_bridge
    )
    for family in ("registry", "fleet"):
        result = production.call(family, "list" if family == "registry" else "snapshot")
        assert result.status == STATUS_NO_DATA
        assert "fixture-only" in result.reason


def test_the_grounded_family_is_never_the_declared_fake(chat_catalog):
    # the fixture catalogue is the only place the fake index is reachable, and
    # it is reachable there *under its own family* - never as a grounding
    # source for a real read
    turn = GroundingAssembler(chat_catalog).assemble(
        _request(Need("board", "ticket", "#504", (("number", 504),)))
    )
    assert all(fragment.family == "board" for fragment in turn.fragments)


class _RecordingCatalog(SourceCatalog):
    """A catalogue that records which families were read (selection proof)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = []

    def call(self, family, operation, **kwargs):
        self.reads.append((family, operation))
        return super().call(family, operation, **kwargs)


def test_grounding_selects_only_what_the_turn_needs(chat_root, fixture_bridge):
    catalog = SourceCatalog.from_repo_root(
        chat_root, mode=MODE_FIXTURE, bridge=fixture_bridge
    )
    recording = _RecordingCatalog(root=catalog.root, mode=MODE_FIXTURE)
    for family in catalog.families():
        recording.add(catalog.source(family))
    turn = GroundingAssembler(recording).assemble(_request(_board_need()))
    assert [read[0] for read in recording.reads] == ["board"]
    assert "board" in turn.sources_read
    assert "fleet" in turn.sources_skipped and "budget" in turn.sources_skipped
    assert "fleet" not in turn.sources_read


def test_an_unreadable_authority_is_no_data_and_names_the_home(tmp_path):
    catalog = SourceCatalog.from_repo_root(str(tmp_path), mode=MODE_PRODUCTION)
    turn = GroundingAssembler(catalog).assemble(_request(_board_need()))
    assert turn.status == STATUS_NO_DATA
    assert ".board/snapshot.json" in turn.why()[0]


# --------------------------------------------------------------------------- #
# read vs propose (ADR-0023)
# --------------------------------------------------------------------------- #
def test_a_wanted_action_is_a_proposal_and_never_a_write(chat_root, fixture_bridge):
    catalog = SourceCatalog.from_repo_root(
        chat_root, mode=MODE_FIXTURE, bridge=fixture_bridge
    )
    turn = GroundingAssembler(catalog, tenant_id="acme").assemble(_request(_board_need()))
    before = tree_state(chat_root)
    proposal = propose_approval(
        turn,
        action="ticket.comment",
        target="#504",
        rationale="the grounding shows the ticket is blocked",
        requested_by="agent-a",
    )
    assert isinstance(proposal, ApprovalProposal)
    assert proposal.as_dict() == {
        "kind": "approval-proposal",
        "action": "ticket.comment",
        "target": "#504",
        "tenantId": "acme",
        "rationale": "the grounding shows the ticket is blocked",
        "requestedBy": "agent-a",
        "requiresApproval": True,
    }
    assert tree_state(chat_root) == before
    assert WRITE_TOOLS == ()
    assert turn.tenant_id == "acme"
    assert not hasattr(catalog, "write") and not hasattr(catalog, "append")


def test_a_proposal_without_a_rationale_is_refused(chat_catalog):
    turn = GroundingAssembler(chat_catalog).assemble(_request(_board_need()))
    with pytest.raises(GroundingError):
        propose_approval(
            turn, action="ticket.comment", target="#504", rationale="  ", requested_by="a"
        )


def test_the_spec_is_a_cache_safe_constant():
    assert PLATFORM_SPEC == PLATFORM_SPEC.strip()
    assert prompt_cache().scan_dynamic(PLATFORM_SPEC) == []
    # the family is ten distinct ids: eight newly declared + the two reused
    assert len(CHAT_FAMILY_NAMES) == 10
    assert len(set(CHAT_FAMILY_NAMES)) == 10
