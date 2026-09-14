"""The paperclip skills adapter: a ``SKILL.md`` registry and the MCP tool projection.

Upstream's *extensibility* family (skills as ``SKILL.md``, plugins/extensions and
MCP tool access) has no fleet-side producer today: nothing says which skill an
agent may load, or which MCP tool a run may call. This package is the thin
adapter that maps that family onto what the fleet already runs (EPIC #410, issue
#419, ADR-0013):

* :mod:`~paperclip.adapters.skills.registry` — the **declared** skill registry.
  Every loadable skill is declared with its origin and provenance (GR-10); an
  undeclared skill is *refused*, never silently loaded.
* :mod:`~paperclip.adapters.skills.projection` — the MCP tool surface is
  **projected, not duplicated**. The tool list is derived from the tool authority
  ``gateway/mcp/`` and written as a view; a tool that is callable but absent from
  the projection is a FAIL.
* :mod:`~paperclip.adapters.skills.loader` — the load gate. A skill or plugin
  requiring a capability (or tool) the agent's profile does not grant is refused
  at load, so extensibility can never widen an agent's authority: the profile
  stays the authority.
* :mod:`~paperclip.adapters.skills.frontmatter` — the ``SKILL.md`` loader. A
  skill is a **declaration plus a reference**, never a copied implementation
  (GR-10): a declaration directory may contain ``SKILL.md`` and nothing else.

The package is stdlib + PyYAML only, takes no upstream dependency, vendors no
upstream code, and never writes to a ledger.
"""

from __future__ import annotations

__all__ = ["frontmatter", "loader", "model", "projection", "registry"]
