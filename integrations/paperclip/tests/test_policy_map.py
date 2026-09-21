"""The policy-map adapter suite (issue #1764).

Acceptances, each a test that can fail:

1. a real declared policy domain (``gdc`` and/or ``isolation``) is **visible to
   Paperclip** through this adapter against the REAL repository root — the row is
   surfaced with the registry's own ``source_file``;
2. the adapter consumes **only** ``PolicyRegistry.rows()`` — it re-reads neither
   ``governance/conformance/policy.yaml`` nor ``governance/policy/lease.py`` (its
   imports name the registry and nothing else in ``governance``), and an injected
   registry is surfaced verbatim even when its ``source_file`` does not exist;
3. fail honest — an invisible domain and an empty registry are **reported**, not
   omitted;
4. a registry that cannot be read is refused by name;
5. the projection is deterministic.

The real-root tests read the repository's own declarations; no test touches the
network or writes anything.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from governance.policy.registry import PolicyRegistry
from integrations.paperclip import policy_map as adapter

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The domains #1763 delivers; the goal needs at least one visible to Paperclip.
REAL_DOMAINS = ("gdc", "isolation")


class _Row:
    """A minimal stand-in for ``PolicyRow`` (attributes the adapter reads)."""

    def __init__(
        self,
        domain: str,
        source_file: str,
        enforcement_point: str = "",
        control_plane_visible: bool = True,
    ) -> None:
        self.domain = domain
        self.source_file = source_file
        self.enforcement_point = enforcement_point
        self.control_plane_visible = control_plane_visible


class _FakeRegistry:
    """A registry whose ``rows()`` returns exactly what the test hands it."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = list(rows)

    def rows(self) -> list[_Row]:
        return list(self._rows)


def _imported_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _file_read_calls(source: str) -> list[str]:
    """The file-reading calls in the module, if any (prose is ignored)."""
    calls: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            calls.append("open")
        elif isinstance(func, ast.Attribute) and func.attr in {"read_text", "read_bytes"}:
            calls.append(func.attr)
    return calls


# ── 1. a real domain is visible to Paperclip through the adapter ─────────────


def test_a_real_policy_domain_is_visible_to_paperclip() -> None:
    document = adapter.policy_map(REPO_ROOT)
    registry_rows = {row.domain: row for row in PolicyRegistry(REPO_ROOT).rows()}

    reported = [
        name
        for name in REAL_DOMAINS
        if name in registry_rows and registry_rows[name].control_plane_visible
    ]
    assert reported, (
        "neither gdc nor isolation is a visible policy domain in the registry "
        f"(saw: {sorted(registry_rows)})"
    )

    for name in reported:
        surfaced = adapter.domain_named(document, name)
        assert surfaced is not None, f"{name} is visible in the registry but absent from the map"
        assert surfaced["control_plane_visible"] is True
        # the registry's own source_file — the adapter adds no second answer
        assert surfaced["source_file"] == registry_rows[name].source_file


def test_every_registry_row_is_surfaced_in_order() -> None:
    document = adapter.policy_map(REPO_ROOT)
    assert [d["domain"] for d in document["domains"]] == [
        row.domain for row in PolicyRegistry(REPO_ROOT).rows()
    ]
    assert document["domain_count"] == len(document["domains"])
    assert document["visible_count"] == sum(
        1 for d in document["domains"] if d["control_plane_visible"]
    )


# ── 2. the adapter consumes the registry and nothing else ────────────────────


def test_adapter_surfaces_an_injected_registry_verbatim(tmp_path: Path) -> None:
    # The injected source_file does not exist, and repo_root is an empty tree:
    # a re-deriving adapter would drop or rewrite this row. It must not.
    assert not (tmp_path / "governance" / "policy" / "domains").exists()
    registry = _FakeRegistry([_Row("synthetic", "does/not/exist.yaml", "nowhere", True)])

    document = adapter.policy_map(tmp_path, registry=registry)

    assert document["domain_count"] == 1
    assert document["domains"][0]["source_file"] == "does/not/exist.yaml"
    assert document["domains"][0]["control_plane_visible"] is True


def test_the_adapter_imports_only_the_registry_from_governance() -> None:
    source = Path(adapter.__file__).read_text(encoding="utf-8")

    governance_imports = {
        name for name in _imported_modules(source) if name.split(".")[0] == "governance"
    }
    assert governance_imports == {"governance.policy.registry"}

    # It opens no file itself, so it cannot be re-reading
    # governance/conformance/policy.yaml or governance/policy/lease.py — the
    # docstring may *name* those paths, but a file READ would be the second
    # answer ADR-0012 forbids. The registry is the adapter's only source.
    assert _file_read_calls(source) == []


# ── 3. fail honest: nothing is silently dropped ──────────────────────────────


def test_an_invisible_domain_is_reported_not_omitted() -> None:
    registry = _FakeRegistry(
        [_Row("ghost", "missing.yaml", "not reporting: missing.yaml absent", False)]
    )

    document = adapter.policy_map(registry=registry)

    assert [d["domain"] for d in document["domains"]] == ["ghost"]
    assert document["domains"][0]["control_plane_visible"] is False
    assert document["visible_count"] == 0
    assert any("ghost" in note for note in document["notes"]), document["notes"]


def test_an_empty_registry_is_reported_as_empty() -> None:
    document = adapter.policy_map(registry=_FakeRegistry([]))

    assert document["domains"] == []
    assert document["domain_count"] == 0
    assert document["notes"], "an empty policy surface must say so"
    assert "empty" in document["notes"][0].lower()


# ── 4. a registry that cannot be read is refused by name ─────────────────────


def test_a_registry_without_rows_is_refused_by_name() -> None:
    with pytest.raises(adapter.PolicyMapRefused) as excinfo:
        adapter.policy_map(registry=object())
    assert excinfo.value.reason == "registry-unreadable"


# ── 5. determinism ───────────────────────────────────────────────────────────


def test_render_is_deterministic() -> None:
    first = adapter.render(adapter.policy_map(REPO_ROOT))
    second = adapter.render(adapter.policy_map(REPO_ROOT))
    assert first == second
    assert json.loads(first) == adapter.policy_map(REPO_ROOT)
