"""Company scope is a projection of our tenancy — ONE declared mapping (#413).

Upstream's company-scoped route shape is ``/api/companies/{companyId}/...``. That
``{companyId}`` is **not** a new tenancy: it is the fleet's own tenant/org id.
In this product an ``Org`` *is* the tenant
(``identity/rbac/model.py``: "In this product an Org *is* the tenant"), and the
onboarding row carries the same id (``identity/onboarding/model.py``:
"``Org.id == Tenant.id``"). So the mapping is the **identity** relation —
companyId == ``Org.id`` == ``Tenant.id`` — declared once, here.

The set of companies the fleet actually knows is *not* hand-listed here either:
it is read from the fleet's own on-disk tenant declaration — the telemetry budget
policy rail (``telemetry/budgets/config/policies.yaml``), which
``portal.server.livestore`` and ``integrations.paperclip.mapping.map_budgets``
already read. A company the fleet has not declared is refused (404); a declared
company that is not the caller's scope is refused (403). Fail closed throughout:
an unreadable declaration is an *empty* known set, never a permissive default.

---knowledge---
module_id: integrations.paperclip.api.company
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [mapping, declared_companies, tenant_for, resolve]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import mapping as _mapping

#: The id of the single, declared company mapping this surface speaks.
MAPPING_ID = "ao.paperclip.company/v1"

#: The fleet-side declaration of tenant scope (read-only). The same file the
#: budget rail and the console read; it is the fleet's own tenancy, not a copy.
DECLARED_SOURCE = Path("telemetry") / "budgets" / "config" / "policies.yaml"

#: The pointer into that declaration (documentation for a human reader).
DECLARED_SOURCE_POINTER = "/policies/*/tenantId"

#: The authority the upstream id maps onto (declared, not inferred at runtime).
TARGET_AUTHORITY = "identity/rbac.Org.id (== identity/onboarding.Tenant.id)"

#: The refusal codes this mapping produces, by name.
REFUSAL_CODES: Dict[str, str] = {"cross_company": "cross_tenant", "unknown_company": "not_found"}


def mapping() -> Dict[str, Any]:
    """The ONE declared mapping, as data (deterministic; no filesystem read).

    This is the object the OpenAPI document carries under
    ``x-company-mapping``; it declares the relation (identity), the target
    authority, the declared source the known set is read from, and the refusals.
    """
    return {
        "id": MAPPING_ID,
        "upstream": "companyId",
        "relation": "identity",
        "target_authority": TARGET_AUTHORITY,
        "declared_source": DECLARED_SOURCE.as_posix(),
        "declared_source_pointer": DECLARED_SOURCE_POINTER,
        "refusal": {
            "cross_company": {"status": 403, "code": REFUSAL_CODES["cross_company"]},
            "unknown_company": {"status": 404, "code": REFUSAL_CODES["unknown_company"]},
        },
    }


def declared_companies(root: Path) -> Tuple[str, ...]:
    """The fleet's declared tenant set, read from :data:`DECLARED_SOURCE`.

    Fails closed: an absent or unparseable declaration yields an empty tuple, so
    every company resolves as unknown rather than as permitted.
    """
    data = None
    try:
        data = _mapping.load_yaml_file(Path(root) / DECLARED_SOURCE)
    except (OSError, ValueError):
        return ()
    found = set()
    for entry in (data or {}).get("policies") or []:
        if not isinstance(entry, dict):
            continue
        tenant = str(entry.get("tenantId") or "").strip()
        if tenant:
            found.add(tenant)
    return tuple(sorted(found))


def tenant_for(company_id: str) -> str:
    """The fleet tenant/org id a company id projects onto (the identity relation)."""
    return company_id


def resolve(
    root: Path,
    company_id: str,
    *,
    caller_company: Optional[str] = None,
) -> str:
    """Resolve a path ``{companyId}`` onto a fleet tenant id, or refuse by name.

    Refused, in order: an empty id (400), a company the fleet has not declared
    (404 ``not_found``), and a declared company that is not the caller's own
    scope (403 ``cross_tenant``). The returned value is the fleet tenant/org id.
    """
    from . import errors as _errors

    if not str(company_id or "").strip():
        raise _errors.validation_error("a company id is required")

    known = declared_companies(root)
    if company_id not in known:
        raise _errors.not_found(
            "the company is not declared by the fleet tenancy "
            f"({DECLARED_SOURCE.as_posix()})"
        )
    if caller_company and caller_company != company_id:
        raise _errors.cross_company()
    return tenant_for(company_id)
