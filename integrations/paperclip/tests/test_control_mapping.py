"""The upstream control-verb mapping suite (issue #557, EPIC #551).

Five acceptances, and each one is a test that can fail:

1. every mapped verb names a real upstream route **and** a declared verb from the
   RC-2 registry;
2. an unmapped verb is an explicit ``UNMAPPED`` row — never silence, never an
   omission;
3. the two known irreversible mismatches are recorded (upstream ``terminate`` has
   no fleet counterpart; our fleet-wide ``pause`` has no per-agent counterpart);
4. the import direction obeys ADR-0016 (the seam never imports ``adapters/**``);
5. this is a mapping and grants no authority — the module issues reads only.

Everything upstream-facing is exercised **offline** through the seam's
``FixtureTransport``: the served OpenAPI document is a canned response replayed
from this file, so no test and no gate touches the network.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from integrations.paperclip import client as client_mod
from integrations.paperclip import control_mapping as mapping_mod
from integrations.paperclip.control_mapping import (
    MAPPED,
    MISMATCH,
    UNMAPPED,
    build_table,
    load_verbs,
    mapped_routes,
    normalize,
    probe_routes,
    read_only_method,
    stale_rows,
    unmapped_upstream_routes,
)

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = Path(mapping_mod.__file__).resolve()

#: The peer's served surface, written the way upstream declares it (no ``/api``
#: prefix, named parameters). This is **independent data**: it is what the probe
#: compares the module's route names against, so a route the module names but
#: upstream does not serve fails here rather than being asserted into existence.
SERVED_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "paperclipai"},
    "paths": {
        "/agents/{agentId}/pause": {"post": {"summary": "pause an agent"}},
        "/agents/{agentId}/resume": {"post": {"summary": "resume an agent"}},
        "/agents/{agentId}/clear-error": {"post": {}},
        "/agents/{agentId}/approve": {"post": {}},
        "/agents/{agentId}/terminate": {"post": {}},
        "/agents/{agentId}": {"delete": {}},
        "/agents/{agentId}/keys": {"post": {}, "delete": {}},
        "/agents/{agentId}/wakeup": {"post": {}},
        "/agents/{agentId}/heartbeat/invoke": {"post": {}},
        "/agents/{agentId}/config-revisions/{revisionId}/rollback": {"post": {}},
        "/agents/{agentId}/runtime-state/reset-session": {"post": {}},
        "/heartbeat-runs/{runId}/cancel": {"post": {}},
        "/heartbeat-runs/{runId}/runtime-requests/{requestId}/resolve": {"post": {}},
        "/heartbeat-runs/{runId}/watchdog-decisions": {"post": {}},
        "/heartbeat-runs/{runId}/log": {"get": {}},
        "/heartbeat-runs/{runId}/events": {"get": {}},
        "/heartbeat-runs/{runId}/provider-trace": {"get": {}},
        "/companies/{companyId}/dashboard": {"get": {}},
        "/companies/{companyId}/issues": {"get": {}, "post": {}},
        "/issues/{id}": {"patch": {}},
        "/health": {"get": {}},
        "/openapi.json": {"get": {}},
    },
}


def served(path: str = "/api/openapi.json", document=None):
    """A FixtureTransport replaying one served document. Offline by construction."""
    return client_mod.FixtureTransport(
        {
            "responses": [
                {
                    "method": "GET",
                    "path": path,
                    "status": 200,
                    "body": SERVED_OPENAPI if document is None else document,
                }
            ]
        },
        token="tok",
        run_id="run-1",
    )


@pytest.fixture(scope="module")
def registry():
    return load_verbs(ROOT)


@pytest.fixture(scope="module")
def table(registry):
    return build_table(ROOT, registry=registry)


# ---------------------------------------------------------------------------
# The vocabulary side is read, never copied
# ---------------------------------------------------------------------------


def test_the_table_is_built_from_the_rc2_registry_not_a_copy(registry, table):
    declared = [str(v["id"]) for v in registry["verbs"]]
    assert declared, "the RC-2 registry declared no verbs"
    assert [row.verb for row in table] == declared, (
        "the table must follow the registry's own order and cover exactly its verbs"
    )
    assert mapping_mod.route("GET /health").source, "the route inventory is empty"


def test_the_table_is_deterministic(registry):
    first = build_table(ROOT, registry=registry)
    second = build_table(ROOT, registry=registry)
    assert first == second


def test_a_registry_verb_this_mapping_has_not_classified_is_refused_by_name(registry):
    """The reason the YAML is read at runtime: a new verb cannot stay silent.

    A stub registry is enough for the first half — the verb is added to a copy of
    the real document, so the refusal is provoked by a verb the registry really
    could come to declare.
    """
    import copy

    stub = copy.deepcopy(registry)
    stub["verbs"].append(
        {
            "id": "fleet.invented",
            "source": "fleet/control.py",
            "local": "invented",
            "effect_class": "hold",
            "capability": "fleet:operate",
            "audit": "fleet.invented",
            "idempotent": True,
            "exposed": True,
            "refusals": [401, 403, 503],
        }
    )
    with pytest.raises(mapping_mod.UnclassifiedControlVerb) as caught:
        build_table(ROOT, registry=stub)
    message = str(caught.value)
    assert "fleet.invented" in message, (
        "an unclassified verb must be refused BY NAME, not by a generic failure"
    )
    assert "UNMAPPED" in message, "the refusal must say what the missing choice is"


def test_a_row_for_a_verb_the_registry_no_longer_declares_is_reported(registry):
    import copy

    assert stale_rows(registry) == (), "the real table has a row for every verb"
    stub = copy.deepcopy(registry)
    stub["verbs"] = [v for v in stub["verbs"] if v["id"] != "fleet.status"]
    assert stale_rows(stub) == ("fleet.status",), (
        "a row whose verb left the vocabulary describes a lever that is gone"
    )


def test_a_route_outside_the_inventory_is_refused_by_name(table):
    with pytest.raises(KeyError) as caught:
        mapping_mod.route("POST /agents/:id/reboot")
    assert "/agents/:id/reboot" in str(caught.value)
    for row in table:
        if row.upstream is not None:
            assert row.upstream.key in mapping_mod.route_keys(), row.verb


# ---------------------------------------------------------------------------
# Acceptance 1 — every mapped verb names a real route and a declared verb
# ---------------------------------------------------------------------------


def test_every_mapped_row_names_a_real_upstream_route_and_a_declared_verb(
    registry, table
):
    declared = {str(v["id"]) for v in registry["verbs"]}
    inventory = set(mapping_mod.route_keys())
    for row in table:
        assert row.verb in declared, f"{row.verb} is not declared by RC-2"
        if row.state in (MAPPED, MISMATCH):
            assert row.upstream is not None, f"{row.verb}: {row.state} names no route"
            assert row.upstream.key in inventory, (
                f"{row.verb} names {row.upstream.key}, which is not in the inventory"
            )
            assert row.why, f"{row.verb}: a {row.state} row must say why"


def test_every_recorded_mismatch_names_a_declared_verb_and_a_real_route(registry):
    declared = {str(v["id"]) for v in registry["verbs"]}
    inventory = set(mapping_mod.route_keys())
    for mismatch in mapping_mod.MISMATCHES:
        assert mismatch.upstream is not None, mismatch.id
        assert mismatch.upstream.key in inventory, mismatch.id
        assert mismatch.why, mismatch.id
        if mismatch.fleet_verb is not None:
            assert mismatch.fleet_verb in declared, (
                f"{mismatch.id} names {mismatch.fleet_verb}, which RC-2 does not declare"
            )


def test_the_table_and_the_mismatch_list_cannot_drift(table):
    """Every MISMATCH row has a recorded entry, and the two agree on its route."""
    rows = {r.verb: r for r in table if r.state == MISMATCH}
    entries = {m.fleet_verb: m for m in mapping_mod.MISMATCHES if m.fleet_verb}
    assert set(rows) == set(entries), (
        f"the table's MISMATCH rows {sorted(rows)} and the recorded mismatches "
        f"{sorted(entries)} disagree"
    )
    for verb, row in rows.items():
        assert row.upstream.key == entries[verb].upstream.key, verb
        assert row.why == entries[verb].why, verb


# ---------------------------------------------------------------------------
# Acceptance 2 — an unmapped verb is an explicit row, never silence
# ---------------------------------------------------------------------------


def test_every_declared_verb_has_a_row_and_every_row_has_a_state(registry, table):
    declared = {str(v["id"]) for v in registry["verbs"]}
    assert {row.verb for row in table} == declared, (
        "a declared verb with no row is the omission this module refuses"
    )
    assert all(row.state in mapping_mod.STATES for row in table)
    assert len(table) == len(declared), "a verb appears twice"


def test_an_unmapped_row_says_so_and_says_why(table):
    unmapped = [row for row in table if row.state == UNMAPPED]
    assert unmapped, "the vocabulary must have verbs with no upstream counterpart"
    for row in unmapped:
        assert row.upstream is None, row.verb
        assert row.why.strip(), f"{row.verb}: an UNMAPPED row with no reason is silence"


def test_the_state_distribution_is_pinned(registry, table):
    """A re-classification is visible here rather than in a reader's head.

    The distribution moved when the 14 verbs declared after #557's base were
    classified (#1262): one maps (``channel.follow``, the per-directive log tail
    upstream serves as ``GET /heartbeat-runs/:runId/log``) and thirteen have no
    upstream counterpart. Pinning the shape makes the next re-classification a
    deliberate edit here instead of a silent drift.
    """
    counts = {state: 0 for state in mapping_mod.STATES}
    for row in table:
        counts[row.state] += 1
    assert counts == {MAPPED: 9, MISMATCH: 8, UNMAPPED: 46}, counts
    assert len(table) == 63, "the RC-2 registry declares 63 verbs at #1262's base"


def test_the_verbs_declared_after_this_table_was_written_are_classified(table):
    """The regression #1262 names: ONE unclassified verb refuses the WHOLE table.

    Fourteen verbs were added to the registry after this table's base and none had
    a row, so :func:`build_table` raised on the first of them (``fleet.drop``) and
    every test needing the table errored — the mapping was red on master while no
    gate ran it. Pinned by name and state so the refusal cannot return silently,
    and so the one MAPPED row among them is not read as an unmapped one.
    """
    added = {
        "fleet.drop": UNMAPPED,
        "fleet.dead-letter": UNMAPPED,
        "channel.log": UNMAPPED,
        "channel.follow": MAPPED,
        "channel.kb": UNMAPPED,
        "channel.steer": UNMAPPED,
        "board.liveness": UNMAPPED,
        "board.dangling": UNMAPPED,
        "board.focus": UNMAPPED,
        "board.pool": UNMAPPED,
        "board.dispatch": UNMAPPED,
        "board.trigger": UNMAPPED,
        "board.queue": UNMAPPED,
        "closure.retire": UNMAPPED,
    }
    rows = {row.verb: row for row in table}
    assert set(added) <= set(rows), sorted(set(added) - set(rows))
    assert {verb: rows[verb].state for verb in added} == added
    assert rows["fleet.drop"].upstream is None, (
        "fleet.drop must name no route: paperclip serves no dead-letter store for a "
        "queued directive to be dropped into"
    )
    assert rows["channel.follow"].upstream.key == "GET /heartbeat-runs/:runId/log"
    for verb in added:
        assert rows[verb].why.strip(), f"{verb}: a row with no reason is silence"


def test_the_one_mapped_write_is_a_filing_not_an_action(table):
    rows = {r.verb: r for r in table}
    write = rows["fleet.override"]
    assert write.state == MAPPED
    assert write.upstream.key == "POST /companies/:companyId/issues"
    assert write.upstream.effect == "irreversible"
    assert write.upstream.scope == "company"
    mapped_writes = [
        r.verb for r in table if r.state == MAPPED and r.upstream.method != "GET"
    ]
    assert mapped_writes == ["fleet.override"], (
        "exactly one of our verbs maps to an upstream write, and it is the ticket filing"
    )


# ---------------------------------------------------------------------------
# Acceptance 3 — the two known irreversible mismatches are RECORDED
# ---------------------------------------------------------------------------


def _mismatch(mismatch_id):
    by_id = {m.id: m for m in mapping_mod.MISMATCHES}
    entry = by_id.get(mismatch_id)
    assert entry is not None, (
        f"{mismatch_id} is missing from control_mapping.MISMATCHES: the record of a "
        f"known mismatch was deleted, so the seam document would lose the row too"
    )
    return entry


def test_the_upstream_terminate_mismatch_is_recorded():
    """(a) upstream terminate has no fleet counterpart — recorded, not mapped."""
    entry = _mismatch("M-CTL-1")
    assert entry.upstream.key == "POST /agents/:id/terminate"
    assert entry.fleet_verb is None, (
        "M-CTL-1 must name no fleet verb: the mismatch IS that none exists"
    )
    assert entry.kind == "no-fleet-counterpart"
    assert entry.upstream.effect == "irreversible"
    assert "no verb in our vocabulary ends an agent" in entry.why.lower()
    # The reverse direction, computed rather than asserted: terminate is named by
    # no MAPPED row, so it is upstream-only. A lane that maps it later removes it.
    keys = {r.key for r in unmapped_upstream_routes()}
    assert "POST /agents/:id/terminate" in keys, (
        "upstream terminate must appear among the routes no verb maps"
    )
    assert "POST /agents/:id/terminate" not in {r.key for r in mapped_routes()}


def test_our_fleet_wide_pause_mismatch_is_recorded(table):
    """(b) our fleet-wide pause has no per-agent upstream counterpart."""
    entry = _mismatch("M-CTL-2")
    assert entry.fleet_verb == "fleet.pause"
    assert entry.upstream.key == "POST /agents/:id/pause"
    assert entry.kind == "scope"
    row = {r.verb: r for r in table}["fleet.pause"]
    assert row.state == MISMATCH, (
        "fleet.pause must be recorded as a MISMATCH, never silently mapped onto "
        "upstream's per-agent pause"
    )
    assert row.upstream.key == entry.upstream.key
    # The route is named (so the probe checks it is real) but not MAPPED.
    assert entry.upstream.key in {r.key for r in unmapped_upstream_routes()}
    assert entry.upstream.key not in {r.key for r in mapped_routes()}


def test_the_resume_half_of_the_pause_mismatch_is_recorded(table):
    """The brief named pause; the same divergence holds on resume."""
    entry = _mismatch("M-CTL-3")
    assert entry.fleet_verb == "fleet.resume"
    assert entry.upstream.key == "POST /agents/:id/resume"
    assert {r.verb: r for r in table}["fleet.resume"].state == MISMATCH


def test_every_mismatch_is_identified_and_unique():
    ids = [m.id for m in mapping_mod.MISMATCHES]
    assert len(ids) == len(set(ids)), "a duplicate mismatch id would hide a row"
    assert all(re.fullmatch(r"M-CTL-\d+", mid) for mid in ids), ids
    kinds = {m.kind for m in mapping_mod.MISMATCHES}
    assert "no-fleet-counterpart" in kinds
    assert "scope" in kinds


# ---------------------------------------------------------------------------
# Acceptance 4 — the import direction obeys ADR-0016
# ---------------------------------------------------------------------------


def test_the_module_never_imports_adapters():
    """ADR-0016: the seam is the base layer; ``adapters/**`` imports it, not the reverse."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    relative = set()
    absolute = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                relative.add("." * node.level + target)
            else:
                absolute.add(target)
        elif isinstance(node, ast.Import):
            absolute.update(alias.name for alias in node.names)
    assert not [name for name in relative if name.startswith(".adapters")], relative
    assert not [name for name in absolute if "adapters" in name], absolute
    assert relative <= {"..client", ".client", ".model"}, (
        f"the seam layer may import its own siblings only, saw {sorted(relative)}"
    )
    assert absolute == {
        "__future__",
        "dataclasses",
        "importlib.util",
        "pathlib",
        "re",
        "sys",
        "typing",
    }, f"unexpected module-level import in the seam: {sorted(absolute)}"


# ---------------------------------------------------------------------------
# Acceptance 5 — a mapping, never authority
# ---------------------------------------------------------------------------


def test_the_probe_is_offline_and_issues_reads_only():
    transport = served()
    result = probe_routes(transport, rows=build_table(ROOT))
    assert result.ok, f"the served document is missing {result.missing}"
    assert transport.requests, "the probe made no request, so nothing was checked"
    methods = [r["method"] for r in transport.requests]
    assert set(methods) == {"GET"}, methods
    assert not set(methods) & set(client_mod.MUTATING_METHODS), (
        "the mapping must never press an upstream control route"
    )
    assert [r["path"] for r in transport.requests] == ["/api/openapi.json"]
    assert transport.requests[0]["headers"].get("X-Paperclip-Run-Id") is None, (
        "a read is not part of a run, so it must not carry the run header"
    )


def test_the_probe_checks_every_route_the_table_names():
    transport = served()
    result = probe_routes(transport, rows=build_table(ROOT))
    named = {
        row.upstream.key
        for row in build_table(ROOT)
        if row.upstream is not None
    } | {m.upstream.key for m in mapping_mod.MISMATCHES if m.upstream is not None}
    assert set(result.checked) == named, "the probe must check what the table names"
    for key in result.checked:
        assert normalize(key) in result.declared, key


def test_the_probe_refuses_a_route_the_peer_does_not_serve():
    """Negative control: a dropped route is reported BY NAME, so the probe is real."""
    document = {
        **SERVED_OPENAPI,
        "paths": {
            path: ops
            for path, ops in SERVED_OPENAPI["paths"].items()
            if path != "/agents/{agentId}/terminate"
        },
    }
    assert "/agents/{agentId}/terminate" not in document["paths"]
    result = probe_routes(served(document=document), rows=build_table(ROOT))
    assert not result.ok
    assert result.missing == ("POST /agents/:id/terminate",), result.missing


def test_normalize_unifies_the_three_spellings_of_one_route():
    assert normalize("POST /agents/:id/pause") == "POST /agents/*/pause"
    assert normalize("post /api/agents/{agentId}/pause") == "POST /agents/*/pause"
    assert normalize("POST /agents/{id}/pause") == "POST /agents/*/pause"
    assert normalize("GET /companies/:companyId/dashboard") == normalize(
        "GET /api/companies/{id}/dashboard"
    )
    assert normalize("POST /agents/:id/pause") != normalize("POST /agents/:id/resume")


def test_no_public_function_can_mutate_upstream():
    """The structural half of acceptance 5, independent of the transport."""
    mutating_call = re.compile(
        r"\.request\(\s*[\"'](?:POST|PATCH|PUT|DELETE)[\"']", re.IGNORECASE
    )
    for name in mapping_mod.__all__:
        member = getattr(mapping_mod, name)
        if not inspect.isfunction(member):
            continue
        source = inspect.getsource(member)
        assert not mutating_call.search(source), (
            f"{name} calls upstream with a mutating method"
        )
        if "transport" in inspect.signature(member).parameters:
            assert name == "probe_routes", (
                f"{name} takes a transport: the module's only transport user is the "
                "read-only probe, and a second one is a finding"
            )


def test_the_module_refuses_a_mutating_method_by_name():
    for method in client_mod.MUTATING_METHODS:
        with pytest.raises(ValueError) as caught:
            read_only_method(method)
        assert method in str(caught.value)
        assert "may only issue" in str(caught.value)
    assert read_only_method("get") == "GET"
    with pytest.raises(ValueError):
        read_only_method("HEAD")


def test_the_public_surface_is_the_mapping_and_nothing_that_acts():
    expected = {
        "MAPPED",
        "MISMATCH",
        "UNMAPPED",
        "STATES",
        "READ_ONLY_METHODS",
        "UPSTREAM_ROUTES",
        "MISMATCHES",
        "MappingRow",
        "Mismatch",
        "ProbeResult",
        "UpstreamRoute",
        "UnclassifiedControlVerb",
        "build_table",
        "load_verbs",
        "mapped_routes",
        "normalize",
        "probe_routes",
        "read_only_method",
        "registry_verbs",
        "route",
        "route_keys",
        "stale_rows",
        "unmapped_upstream_routes",
    }
    assert set(mapping_mod.__all__) == expected, (
        "the exported surface changed: a new name here means a new way to use the "
        "mapping, and this lane's contract is that it has none but the reads"
    )
    for name in mapping_mod.__all__:
        assert hasattr(mapping_mod, name), name
    assert mapping_mod.READ_ONLY_METHODS == ("GET",)
