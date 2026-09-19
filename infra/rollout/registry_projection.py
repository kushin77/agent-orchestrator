"""infra.rollout.registry_projection — project a promotion's live state into the
surface declaration the portal reads (issue #967).

WHY this exists. The go-live driver records a genuine promotion in
``infra/rollout/live-state.yaml`` (schema enforced by
``infra/rollout/model.py::validate_live_state_doc``). The portal decides
whether a ``surfaces.*`` route answers from
``infra/feature-flags/registry.yaml``'s ``surfaces.<name>.default`` (see
``portal/server/fleet.py::read_surface_default``). Before this module, nothing
in the repo turned the first into the second: a completed, owner-approved
go-live could leave the surface dark until a human hand-edited the registry
declaration (the gap #954 closed for the *plan*, one level up).

This module is a **pure function** of ``live-state.yaml`` (+ the go-live plan,
to know which ``surfaces.*`` flags are real go-live surfaces at all): it never
writes ``registry.yaml``, never touches disk itself, and takes no I/O
dependency of its own — callers pass in already-loaded YAML documents (dicts).
``portal/server/fleet.py::read_surface_default`` is the one caller that
consumes it at read time (issue #967's consumer): a surface promoted to
``full`` in live-state now reads as effectively promoted even before a human
edits the registry's static ``default`` field, closing exactly the manual step
the issue names. The registry's own ``default``/``promoted`` fields are left
untouched by design (GR-28: the registry stays the reviewed IaC document; this
is a *runtime* read-time projection layered on top of it, not a build-time
rewrite).

Fail closed, the same posture every other reader in this rollout pillar takes
(``model.py``, ``surface_state.py``): a live-state document that is not a
mapping, or a flag entry that is not a mapping, projects to the same result as
an absent flag — "never promoted" — never an exception and never an
assumed promotion.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

#: The registry-section prefix this module projects (mirrors
#: ``infra/rollout/checks/check_rollout.py``'s ``surfaces.`` branch).
SURFACES_PREFIX = "surfaces."

#: The only stage that means "served to everyone" (mirrors
#: ``infra/rollout/model.py``'s closed stage vocabulary).
LIVE_STAGE = "full"


def project_surface(surface: str, live_state_doc: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The effective ``{"stage", "promoted", "live"}`` for one ``surfaces.<surface>`` flag.

    * absent from live-state (the only committed file that may record a flag
      above ``off`` — GR-28) -> ``stage="off"``, ``promoted=False``,
      ``live=False``: never promoted;
    * present at any non-``off`` stage -> ``promoted=True``. Only ``stage ==
      "full"`` is ``live=True`` — a canary/gradual promotion is real but not
      yet serving everyone, so it must not read as fully live;
    * a malformed document, or a malformed entry, projects to the same
      "never promoted" result rather than raising — the caller (a portal
      route resolving a flag at request time) must never crash on an
      unexpected live-state shape; the check gate is what makes a malformed
      live-state a build-time finding instead.
    """
    entry = _flag_entry(live_state_doc, f"{SURFACES_PREFIX}{surface}")
    if entry is None:
        return {"stage": "off", "promoted": False, "live": False}
    stage = entry.get("stage")
    if not isinstance(stage, str) or not stage:
        return {"stage": "off", "promoted": False, "live": False}
    return {"stage": stage, "promoted": stage != "off", "live": stage == LIVE_STAGE}


def _flag_entry(live_state_doc: Optional[Mapping[str, Any]], flag: str) -> Optional[Mapping[str, Any]]:
    if not isinstance(live_state_doc, Mapping):
        return None
    flags = live_state_doc.get("flags")
    if not isinstance(flags, Mapping):
        return None
    entry = flags.get(flag)
    return entry if isinstance(entry, Mapping) else None


def plan_surface_names(plan_doc: Optional[Mapping[str, Any]]) -> set:
    """Every ``surfaces.<name>`` the go-live plan names, with the prefix stripped.

    Used to scope a projection (or a control) to real go-live surfaces rather
    than an arbitrary caller-supplied name — the same set
    ``infra/rollout/checks/check_rollout.py::_plan_named_flags`` computes,
    narrowed to the ``surfaces.`` prefix.
    """
    named: set = set()
    if not isinstance(plan_doc, Mapping):
        return named
    phases = plan_doc.get("phases")
    if not isinstance(phases, Mapping):
        return named
    for body in phases.values():
        if not isinstance(body, Mapping):
            continue
        surfaces = body.get("surfaces")
        if not isinstance(surfaces, list):
            continue
        for surface in surfaces:
            if not isinstance(surface, Mapping):
                continue
            flag = surface.get("flag")
            if isinstance(flag, str) and flag.startswith(SURFACES_PREFIX):
                named.add(flag[len(SURFACES_PREFIX):])
    return named


def project_all(
    live_state_doc: Optional[Mapping[str, Any]],
    plan_doc: Optional[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """``{surface_name: project_surface(...)}`` for every plan-named ``surfaces.*`` flag."""
    return {name: project_surface(name, live_state_doc) for name in sorted(plan_surface_names(plan_doc))}


def registry_matches_projection(registry_surfaces: Optional[Mapping[str, Any]], surface: str, projected: Mapping[str, Any]) -> bool:
    """True when the registry's declared ``promoted`` already reflects ``projected``.

    A surface that live-state has promoted (``projected["promoted"]`` True)
    but whose registry row still declares ``promoted: false`` (or is absent)
    is the unprojected-promotion finding #967 asks to make detectable. A
    surface live-state has never promoted is never a finding regardless of
    the registry row: the registry may legitimately declare a surface
    promoted ahead of a canary (the row is reviewed IaC, not itself a claim
    about live traffic).
    """
    if not projected.get("promoted"):
        return True
    if not isinstance(registry_surfaces, Mapping):
        return False
    entry = registry_surfaces.get(surface)
    if not isinstance(entry, Mapping):
        return False
    return entry.get("promoted") is True
