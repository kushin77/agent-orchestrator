"""integrations.paperclip.policy_map — deliver the policy registry to Paperclip (issue #1764).

`integrations/paperclip/` declares ADR-0012's rule — **map the policy, do not
couple the runtime** — but until this module no adapter read this repository's
*policy declarations*: Paperclip had to re-derive policy state, or run blind.

This module is the missing read path. It consumes **one** source and nothing
else: ``governance.policy.registry.PolicyRegistry(repo_root).rows()`` — the
registry that projects every ``governance/policy/domains/*.yaml`` declaration
into a single :class:`~governance.policy.registry.PolicyRow` schema (#1763).
It re-derives no domain: it does not import ``governance.policy.lease``, does not
open ``governance/conformance/policy.yaml``, and does not decide for itself
whether a declared source exists. Those are the registry's facts, and reading
them twice would be the second answer ADR-0012 forbids.

It is a **mapping, not authority** (the control-verb module's rule, #557): it
names the declared domains and the registry's own ``source_file`` /
``enforcement_point`` / ``control_plane_visible`` for each, and mutates nothing.

Fail honest (the registry's honesty rule, carried through unchanged): a domain is
never silently dropped. If the registry declares no domains, or a domain is not
control-plane visible, the mapped document says so in ``notes`` — an empty
``domains`` list is *reported as empty*, never emitted as silence. A registry
object that cannot be read fails closed by name (:class:`PolicyMapRefused`).

The document is deterministic (``render`` twice yields identical bytes), so a
gate or a reviewer can diff it. Import direction obeys ADR-0016: this is the
seam (``integrations/paperclip/``) and it imports no ``adapters/**``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List, Mapping, Optional

#: this file -> paperclip -> integrations -> repository root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy.registry import PolicyRegistry  # noqa: E402  (bootstrap above)

#: The document schema id, matching the seam's ``ao.<surface>/v1`` convention.
SCHEMA = "ao.paperclip.policy-map/v1"

#: The single source this adapter projects — named so a reader can verify it.
PROJECTION_SOURCE = "governance.policy.registry.PolicyRegistry.rows"

#: Where domains are declared (the registry's own contract, restated for a note).
DOMAINS_DECLARATION_DIR = "governance/policy/domains"


class PolicyMapRefused(Exception):
    """The adapter refused to map. ``reason`` is a machine-readable token."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _domain_row(row: Any) -> dict[str, Any]:
    """One registry row, projected into the mapped document's domain shape."""
    return {
        "domain": str(getattr(row, "domain", "")),
        "source_file": str(getattr(row, "source_file", "")),
        "enforcement_point": str(getattr(row, "enforcement_point", "")),
        "control_plane_visible": bool(getattr(row, "control_plane_visible", False)),
    }


def _notes(domains: List[dict[str, Any]]) -> List[str]:
    """The honesty notes: an empty surface, and every invisible domain, named."""
    if not domains:
        return [
            "the policy registry declared no domains under "
            f"{DOMAINS_DECLARATION_DIR}/ — Paperclip is seeing an EMPTY policy "
            "surface, not an absent one"
        ]
    notes: List[str] = []
    for domain in domains:
        if not domain["control_plane_visible"]:
            notes.append(
                f"domain {domain['domain']!r} is NOT visible to the control plane: "
                f"{domain['enforcement_point']}"
            )
    return notes


def policy_map(
    repo_root: Optional[Path | str] = None, *, registry: Optional[Any] = None
) -> dict[str, Any]:
    """Paperclip's view of the declared policy domains, read from the registry.

    ``registry`` is injected for tests and for a caller that already holds one;
    when omitted, a :class:`PolicyRegistry` is built over ``repo_root`` (default:
    this repository). The adapter reads **only** ``registry.rows()`` — it never
    re-derives a domain from the files the registry already speaks for.
    """
    if registry is None:
        registry = PolicyRegistry(repo_root or ROOT)
    rows_method = getattr(registry, "rows", None)
    if not callable(rows_method):
        raise PolicyMapRefused(
            "registry-unreadable", "the registry exposes no rows() method"
        )
    try:
        rows = list(rows_method())
    except Exception as exc:  # noqa: BLE001  (fail closed, by name)
        raise PolicyMapRefused("registry-unreadable", str(exc)) from exc

    domains = [_domain_row(row) for row in rows]
    return {
        "schema": SCHEMA,
        "projection_source": PROJECTION_SOURCE,
        "domain_count": len(domains),
        "visible_count": sum(1 for d in domains if d["control_plane_visible"]),
        "domains": domains,
        "notes": _notes(domains),
    }


def domain_named(document: Mapping[str, Any], name: str) -> Optional[dict[str, Any]]:
    """The mapped domain with this name, or ``None`` — never a synthesized row."""
    for domain in document.get("domains", []):
        if domain.get("domain") == name:
            return dict(domain)
    return None


def render(document: Mapping[str, Any]) -> str:
    """The document as deterministic JSON text (same input -> byte-identical)."""
    return json.dumps(document, indent=2, sort_keys=False) + "\n"
