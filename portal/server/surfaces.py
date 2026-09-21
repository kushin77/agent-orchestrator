"""portal.server.surfaces — the portal-surfaces feed adapter (issue #350).


WHY this exists: the OS shell's Settings→Modules page needs the *fleet-wide*
surface catalog — which modules exist, what they are, which features each
declares — and today it has to pin a revision of the CMR feed inside its own
repository (``shared-frontend`` ``registry/portal-surfaces.pinned.json``). While
``kushin77/CMR#708`` was open that pin read ``state:"awaiting-upstream"`` with an
empty ``surfaces``, so the page had nothing to project. The pin-is-the-contract
rule (ADR-0024) is not the problem; a copy that the consumer must re-pin by hand
going stale is. A serving layer can do better: serve the same document over HTTP
so the shell reads the *deployed* revision of the feed instead of a baked-in
copy.

Cannibalize, do not duplicate. The document this adapter serves is **not** built
here: it is ``registry/portal-surfaces.pinned.json`` — the verbatim projection
of ``kushin77/CMR catalog/portal-surfaces.json`` at a recorded revision, with
the upstream blob's sha256 in the pin block (see that file's ``note`` for the
exact fetch and refresh procedure). This adapter reads it and never invents a
surface: a missing, unreadable or non-conforming pin is served as an explicitly
``unresolved`` document with an empty ``surfaces`` list and a ``note`` naming
the defect — empty *and honest*, never fabricated.

Offline by construction: no sockets, no network egress, no ``gh`` call. The
document is on disk, so the surface boots and serves with no terminal and no
network. The surface ships **feature-flag-gated OFF** (GR-5): the flag is
declared in ``infra/feature-flags/registry.yaml`` under ``surfaces`` and read
here through the same fail-closed reader the fleet projection uses
(``portal.server.fleet.read_surface_default``), and while it is off every
``/api/portal/*`` route is refused by the app.


---knowledge---
module_id: portal.server.surfaces
system: portal
app: server
solution_class: pattern
patterns: [pinned-feed-serving, empty-and-honest, feature-flag-gated-off]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [PortalSurfacesFeed, load_pinned_feed]
invariants: "a missing, unreadable or non-conforming pin is served as an explicitly unresolved document with an empty surfaces list, never fabricated"
gotchas: "the served document is registry/portal-surfaces.pinned.json - this adapter never invents a surface"
related: ["#350"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from portal.server.fleet import surface_enabled

#: The registry surface key that gates this endpoint family.
PORTAL_SURFACES = "portal_surfaces"
#: The pinned feed document this surface serves (repo-root relative).
PINNED_RELATIVE = Path("registry") / "portal-surfaces.pinned.json"
#: The only document schema this adapter serves.
SCHEMA = "cmr.portal-surfaces/v1"


def load_pinned_feed(path: Path | str) -> tuple[Optional[dict], str]:
    """Read the pinned feed document; return ``(document, problem)``.

    ``problem`` is ``""`` exactly when ``document`` is the parsed pinned
    document. Every failure mode — missing file, unreadable bytes, invalid
    JSON, a non-object body, the wrong ``schema``, or a ``surfaces`` value that
    is not a list of objects — is *reported* (never raised): the caller serves
    an honest "unresolved" document rather than a 500 or an invented list.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None, f"the pinned feed is unreadable at {path}"
    try:
        document = json.loads(raw)
    except ValueError:
        return None, f"the pinned feed at {path} is not valid JSON"
    if not isinstance(document, dict):
        return None, f"the pinned feed at {path} is not a JSON object"
    if document.get("schema") != SCHEMA:
        return None, (
            f"the pinned feed at {path} declares schema "
            f"{document.get('schema')!r}, expected {SCHEMA!r}"
        )
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, list) or not all(
        isinstance(surface, dict) for surface in surfaces
    ):
        return None, f"the pinned feed at {path} has no surfaces[] list"
    return document, ""


class PortalSurfacesFeed:
    """Projects the pinned portal-surfaces document over HTTP (issue #350).

    ``enabled`` is resolved from the feature-flag registry unless supplied
    explicitly (tests pass it; the server lets the registry decide).
    ``feed_path`` is overridable for the same reason — a test points it at a
    fixture — and otherwise is the repo's committed pin.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        registry_path: Optional[Path | str] = None,
        feed_path: Optional[Path | str] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.registry_path = Path(registry_path) if registry_path is not None else None
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                registry_path=self.registry_path,
                surface=PORTAL_SURFACES,
            )
        self.enabled = bool(enabled)
        self.feed_path = (
            Path(feed_path)
            if feed_path is not None
            else self.repo_root / PINNED_RELATIVE
        )

    # -- reads --------------------------------------------------------------
    def document(self) -> dict:
        """The document served at ``GET /api/portal/surfaces``.

        The pinned document is served verbatim (its ``pin`` block carries the
        revision and the upstream blob's sha256, so a consumer can audit what it
        received). When the pin cannot be read the answer is an explicitly
        ``unresolved`` document with an empty ``surfaces`` list and a ``note``
        naming the defect: the surface is allowed to be empty, it is never
        allowed to be invented.
        """
        document, problem = load_pinned_feed(self.feed_path)
        if document is None:
            return self._unresolved(problem)
        return document

    def surfaces(self) -> list[dict]:
        """The served surface entries — possibly empty, never fabricated."""
        document = self.document()
        surfaces = document.get("surfaces")
        return [surface for surface in surfaces if isinstance(surface, dict)]

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _unresolved(problem: str) -> dict:
        """The honest-empty answer: no surface, and a reason that names why."""
        return {
            "schema": SCHEMA,
            "pin": {
                "repo": "kushin77/CMR",
                "path": "catalog/portal-surfaces.json",
                "revision": None,
                "retrievedAt": None,
                "state": "unresolved",
            },
            "note": f"{problem} — no surface is invented here",
            "surfaces": [],
            "gaps": [],
        }
