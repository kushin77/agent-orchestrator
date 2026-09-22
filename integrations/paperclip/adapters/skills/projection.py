"""The MCP tool projection — the callable surface, **projected not duplicated**.

The tool authority is ``gateway/mcp/``: ``gateway/mcp/tools.py`` builds the tool
registry (the callable set) and ``gateway/mcp/model.py`` declares the closed
``MCP_ALLOWLIST_KEYS`` vocabulary. This module **derives** the surface from that
authority and writes it as a view (``mcp_tools.json``).

Deriving matters because it is the only thing that keeps a hand-maintained list
from drifting away from the code. The rules, enforced by
:func:`projection_findings` and by the gate:

* every callable tool must appear in the projection — a callable-but-unprojected
  tool is a FAIL naming the tool;
* the projection must not name a tool the authority does not declare — a
  projected-but-uncallable entry is a FAIL too;
* the authority's registry and its closed allowlist vocabulary must agree — if
  ``gateway/mcp/tools.py`` and ``gateway/mcp/model.py`` disagree, that is a FAIL
  naming the drift.

Import failure is **CANNOT-ASSESS**, never a pass: a projection that cannot be
derived from the authority proves nothing (no-false-green doctrine).

---knowledge---
module_id: integrations.paperclip.adapters.skills.projection
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [callable_tools, allowlist_vocabulary, build_projection, projection_path, render_projection, load_projection, projection_findings, write_projection]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

from .model import (
    MCP_SOURCE_FILES,
    MCP_SOURCE_FUNCTION,
    PROJECTION_PATH,
    CannotAssess,
    ToolProjection,
)

#: The projection document's schema id.
PROJECTION_SCHEMA = "paperclip-mcp-projection/v1"


def _import_authority(root: Path):
    """Import the tool authority from ``gateway/mcp/`` under ``root`` (fail closed).

    The authority is read **from the given root**: any module already cached under
    ``gateway`` is dropped and the root is put first on ``sys.path``, so a second
    call against a different root re-imports it instead of silently reusing the
    first root's tool set. Without that, a check pointed at a mutated authority
    would answer for the wrong tree — the false-green this gate exists to stop.
    """
    root_str = str(root)
    for name in [name for name in sys.modules if name == "gateway" or name.startswith("gateway.")]:
        del sys.modules[name]
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)
    try:
        from gateway.mcp import model as mcp_model  # type: ignore
        from gateway.mcp import tools as mcp_tools  # type: ignore
    except Exception as exc:  # pragma: no cover - guarded, never vacuous
        raise CannotAssess(
            "skills: cannot import the MCP tool authority %s from %s (%s)"
            % (MCP_SOURCE_FUNCTION, root, exc)
        ) from exc
    return mcp_tools, mcp_model


def callable_tools(root: Path) -> Tuple[str, ...]:
    """The callable tool set, read from the authority's built registry."""
    mcp_tools, _ = _import_authority(root)
    try:
        registry = mcp_tools.build_registry()
        names = tuple(sorted(registry.names()))
    except Exception as exc:  # pragma: no cover - guarded, never vacuous
        raise CannotAssess("skills: cannot build the MCP tool registry (%s)" % exc) from exc
    return names


def allowlist_vocabulary(root: Path) -> Tuple[str, ...]:
    """The authority's closed tool vocabulary (``MCP_ALLOWLIST_KEYS``)."""
    _, mcp_model = _import_authority(root)
    try:
        return tuple(str(key) for key in mcp_model.MCP_ALLOWLIST_KEYS)
    except Exception as exc:  # pragma: no cover - guarded, never vacuous
        raise CannotAssess(
            "skills: cannot read MCP_ALLOWLIST_KEYS from gateway/mcp/model.py (%s)" % exc
        ) from exc


def build_projection(root: Path) -> ToolProjection:
    """Derive the projection view from the live authority (deterministic)."""
    return ToolProjection(
        schema=PROJECTION_SCHEMA,
        derived_from=MCP_SOURCE_FILES,
        source_function=MCP_SOURCE_FUNCTION,
        tools=callable_tools(root),
    )


def projection_path(root: Path) -> Path:
    return root / PROJECTION_PATH


def render_projection(projection: ToolProjection) -> str:
    """The canonical on-disk rendering of the projection (byte-stable)."""
    return json.dumps(projection.as_dict(), indent=2, sort_keys=True) + "\n"


def load_projection(root: Path) -> ToolProjection:
    """Read the on-disk projection view (fail closed on a malformed view)."""
    path = projection_path(root)
    if not path.is_file():
        raise CannotAssess(
            "skills: no MCP projection at %s (the surface must be projected, not assumed)" % path
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CannotAssess("skills: %s is not valid JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise CannotAssess("skills: %s must be a JSON object" % path)
    derived_from = data.get("derived_from")
    if not isinstance(derived_from, list) or not all(isinstance(d, str) for d in derived_from):
        raise CannotAssess("skills: %s declares no 'derived_from' source list" % path)
    tools = data.get("tools")
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise CannotAssess("skills: %s must carry a flat 'tools' name list" % path)
    return ToolProjection(
        schema=str(data.get("schema", "")),
        derived_from=tuple(derived_from),
        source_function=str(data.get("source_function", "")),
        tools=tuple(tools),
    )


def projection_findings(root: Path) -> List[str]:
    """Every way the projection has drifted from the tool authority, named."""
    findings: List[str] = []
    callable_names = set(callable_tools(root))
    vocabulary = set(allowlist_vocabulary(root))

    for name in sorted(callable_names - vocabulary):
        findings.append(
            "callable tool %r is absent from gateway/mcp/model.py MCP_ALLOWLIST_KEYS "
            "(the authority's registry and vocabulary disagree)" % name
        )
    for name in sorted(vocabulary - callable_names):
        findings.append(
            "MCP_ALLOWLIST_KEYS names %r but gateway/mcp/tools.py does not register it "
            "(the authority's registry and vocabulary disagree)" % name
        )

    view = load_projection(root)
    if view.source_function != MCP_SOURCE_FUNCTION:
        findings.append(
            "the projection claims source_function %r, not the tool authority %r"
            % (view.source_function, MCP_SOURCE_FUNCTION)
        )
    if tuple(view.derived_from) != MCP_SOURCE_FILES:
        findings.append(
            "the projection claims derived_from %s, not the tool authority %s"
            % (list(view.derived_from), list(MCP_SOURCE_FILES))
        )

    projected = set(view.tools)
    for name in sorted(callable_names - projected):
        findings.append(
            "callable MCP tool %r is absent from the projection %s "
            "(the projection must be a view, not a hand-maintained allowlist)"
            % (name, PROJECTION_PATH)
        )
    for name in sorted(projected - callable_names):
        findings.append(
            "the projection names %r but it is not callable in the tool authority "
            "(a projected-but-uncallable tool is drift)" % name
        )
    if sorted(projected) != list(view.tools):
        findings.append("the projection's tool list is not sorted (not canonical)")

    canonical = render_projection(build_projection(root))
    on_disk = projection_path(root).read_text(encoding="utf-8")
    if canonical != on_disk:
        findings.append(
            "the projection %s is not byte-identical to the derived view "
            "(re-run the projection instead of editing it)" % PROJECTION_PATH
        )
    return findings


def write_projection(root: Path) -> str:
    """Write the derived projection view; returns the canonical rendering."""
    rendering = render_projection(build_projection(root))
    projection_path(root).write_text(rendering, encoding="utf-8")
    return rendering
