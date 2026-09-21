"""governance.policy.registry — one row schema over every declared policy domain (issue #1763).

WHY this exists: every policy domain this repository enforces kept its own shape
in its own file — the GDC tag rules in ``governance/conformance/policy.yaml``,
the isolation leases in ``governance/policy/lease.py``, Cloudflare/GCS/Git rules
scattered across ``infra/`` and ``docs/`` — with no shared schema and no single
read path (RCA: ``docs/rca/2026-09-21-policy-centralization-gap-review.md``).

This module is the **structural sibling** of ``portal/server/settings.py``
(#1756), not an extension of it: settings aggregates *descriptive/observed*
config (``editable: false``), whereas policy is *prescriptive/enforced* state,
so the row carries an **enforcement point** and a **control-plane visibility**
flag instead of a value. Both are read fresh each call, never cached, and both
obey the same **honesty rule** — see below.

ONE FILE PER DOMAIN. A domain is registered by adding exactly ONE file,
``governance/policy/domains/<domain>.yaml``, whose **filename stem is the
domain name**. No code change, no import, no registration table: three sibling
lanes (#1765 git, #1766 cloudflare, #1767 gcs, #1768 local-dev) each add a file
and are thereby file-disjoint from one another and from this module. Discovery
is ``sorted()``, so the row order is deterministic.

The declared keys (documented in ``governance/policy/README.md``)::

    domain: gdc
    source_file: governance/conformance/policy.yaml
    enforcement_point: "governance/conformance gate at issue-file time"
    control_plane_visible: true

**Honesty rule** (mirrors ``settings.py``): a domain is never silently dropped.
A declaration whose ``source_file`` does not exist under ``repo_root`` is still
emitted — as a row carrying ``control_plane_visible: false`` and an
``enforcement_point`` that **names the missing path**. The same holds for a
domain file that is undecodable, that declares no ``source_file``, or whose
``domain:`` key disagrees with its filename stem: each becomes a row that says
so, so a caller iterating :meth:`PolicyRegistry.rows` sees every domain without
a second channel to check.

---knowledge---
module_id: governance.policy.registry
system: governance
app: policy
solution_class: pattern
patterns: [cross-domain-aggregator, one-file-per-domain, join-not-own, honesty-row]
derives_from: portal/server/settings.py
owner_sme: platform-sme
tier: L1
interfaces: [PolicyRow, PolicyRegistry, PolicyRegistry.rows, PolicyRegistry.aggregate]
invariants: "a domain whose declared source_file is absent is still emitted, control_plane_visible false, naming the missing path; discovery is sorted and nothing is cached"
gotchas: "governance/ and governance/policy/ ship no __init__.py — the package is namespace-style and imported from the repo root; the filename stem is the domain name, so renaming the file renames the domain"
related: ["#1763", "#1764", "#1765", "#1766", "#1767", "#1768", "#1769"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

SCHEMA = "ao.policy-registry/v1"

#: Where a domain is registered: one ``<domain>.yaml`` per domain.
DOMAINS_RELATIVE = Path("governance") / "policy" / "domains"

#: The scalar forms a ``control_plane_visible`` value may take to read as true.
#: Anything else — including an absent key — reads false (fail-closed).
_TRUE_STRINGS = ("true", "on", "yes")


@dataclass(frozen=True)
class PolicyRow:
    """One policy domain, projected into the registry's single row schema.

    ``enforcement_point`` names where the policy actually bites (a gate, a
    dispatch-time check, an apply pipeline). ``control_plane_visible`` is true
    only for a domain whose declared source exists and is declared visible; a
    domain the registry cannot stand behind is emitted with it false and its
    ``enforcement_point`` naming the reason.
    """

    domain: str
    source_file: str
    enforcement_point: str
    control_plane_visible: bool


def _read_yaml(path: Path) -> Any:
    """The declared mapping, or ``None`` when the file is unreadable/undecodable."""
    try:
        import yaml
    except ImportError:
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    except Exception:  # pragma: no cover - yaml.YAMLError isn't a ValueError
        return None


def _visible(value: Any) -> bool:
    """Fail-closed visibility: only an explicit true-ish value is visible."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _refusal(domain: str, source_file: str, reason: str) -> PolicyRow:
    """A row that names why the registry will not stand behind this domain."""
    return PolicyRow(
        domain=domain,
        source_file=source_file,
        enforcement_point=f"not reporting: {reason}",
        control_plane_visible=False,
    )


class PolicyRegistry:
    """Projects every ``governance/policy/domains/*.yaml`` into one row schema.

    Read fresh on every :meth:`rows` call — no cache, no second store (the
    posture ``portal.server.settings.SettingsAggregator`` takes). A domain file
    is never dropped: a declaration the registry cannot stand behind is emitted
    as a refusal row naming the reason (the honesty rule).
    """

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = Path(repo_root)

    def rows(self) -> List[PolicyRow]:
        """Every declared domain, in deterministic (filename-sorted) order."""
        directory = self.repo_root / DOMAINS_RELATIVE
        if not directory.is_dir():
            return []
        return [self._row_for(path) for path in sorted(directory.glob("*.yaml"))]

    def aggregate(self) -> List[PolicyRow]:
        """Alias of :meth:`rows` — the name ``settings.py`` uses for the same verb."""
        return self.rows()

    # -- one domain -> one row ------------------------------------------- #
    def _row_for(self, path: Path) -> PolicyRow:
        # The filename stem is the domain name; a `domain:` key is decorative.
        domain = path.stem
        declared = DOMAINS_RELATIVE.joinpath(path.name).as_posix()

        document = _read_yaml(path)
        if not isinstance(document, dict):
            return _refusal(domain, declared, f"{declared} is undecodable")

        stated = document.get("domain")
        if isinstance(stated, str) and stated.strip() and stated.strip() != domain:
            return _refusal(
                domain,
                declared,
                f"{declared} declares domain '{stated.strip()}' but its "
                f"filename names '{domain}' — the stem is the domain",
            )

        source_file = document.get("source_file")
        if not isinstance(source_file, str) or not source_file.strip():
            return _refusal(domain, declared, f"{declared} declares no source_file")
        source_file = source_file.strip()

        if not (self.repo_root / source_file).exists():
            return _refusal(domain, source_file, f"{source_file} absent")

        enforcement_point = document.get("enforcement_point")
        return PolicyRow(
            domain=domain,
            source_file=source_file,
            enforcement_point=(
                enforcement_point.strip()
                if isinstance(enforcement_point, str)
                else ""
            ),
            control_plane_visible=_visible(document.get("control_plane_visible")),
        )
