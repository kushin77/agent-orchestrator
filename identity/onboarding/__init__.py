"""Tenant onboarding + provisioning controller core (issue #14, work item 10).

Operator-only, idempotent, all-or-nothing tenant provisioning. Public surface
(import this package as ``identity.onboarding`` - ``identity/`` has no
``__init__.py`` and acts as a PEP-420 namespace package, mirroring
``identity/rbac``):

- ``model`` - tenant/IdP-mapping/seed/job/customization data types.
- ``store`` - ``InMemoryStore`` (persistence seam) + ``FileStore`` (JSON).
- ``provisioning`` - the idempotent, all-or-nothing ``provision()`` pipeline.
- ``jobs`` - the provisioning-job runner (retry + step audit).
- ``customization`` - per-tenant instruction customization within the
  allowed-fields allowlist.
- ``ready`` - the tenant ready-check completeness report.
- ``registry_assets`` - resolves platform registry seed assets (read-only).
- ``cli`` - the operator CLI (run as ``python3 -m identity.onboarding.cli``);
  deliberately never an anonymous HTTP endpoint.
"""

from identity.onboarding import (
    customization,
    jobs,
    model,
    provisioning,
    ready,
    registry_assets,
    store,
)

__all__ = [
    "customization",
    "jobs",
    "model",
    "provisioning",
    "ready",
    "registry_assets",
    "store",
]
