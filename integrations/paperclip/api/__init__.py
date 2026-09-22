"""The paperclip HTTP surface projection (issue #413, ADR-0013 / ADR-0016).

The fleet already projects its state onto the upstream paperclip shapes through
the deterministic mapper (``integrations/paperclip/mapping.py``) and authenticates
the boundary (``integrations/paperclip/auth/``). What is missing is the
**surface**: the paperclip-shaped HTTP description an operator (or the upstream
UI) reads — ``GET /api/health``, ``GET /api/openapi.json``, the company-scoped
route shape ``/api/companies/{companyId}/...`` and the error taxonomy.

This subpackage owns exactly that projection, and it owns nothing else. Three
rules shape every file here:

* **The contracts are the source.** The OpenAPI document is *emitted* from the
  frozen seam schemas in ``docs/contracts/paperclip/`` (via
  ``integrations.paperclip.mapping.load_schemas``) plus the adapter's own
  ``to_dict`` shapes (introspected from ``integrations/paperclip/model.py``), so
  a document that drifts from its contract is refused by name.
* **Company scope is a projection of our tenancy.** ``{companyId}`` is the
  fleet's own ``Org``/``Tenant`` id through ONE declared mapping
  (:mod:`~integrations.paperclip.api.company`); a cross-company read is refused
  (403) and an undeclared company is refused (404).
* **The taxonomy is mapped, not invented.** The seven statuses are the seam's
  own typed errors (``integrations.paperclip.model.ERROR_BY_STATUS``) with the
  fleet's existing wire codes (:mod:`~integrations.paperclip.api.taxonomy`).

``/api/health`` names real dependencies (the claim ledger is readable, the
ticket projection is fresh) and reports a non-ok state — 503 when a dependency
is unreachable — rather than a hard-coded ``ok``.

Stdlib-only, like the rest of the canonical adapter module.

---knowledge---
module_id: integrations.paperclip.api.__init__
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

__all__ = ["company", "contracts", "errors", "health", "openapi", "surface", "taxonomy"]
