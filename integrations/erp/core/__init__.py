"""ERP core document model — schemas, workflow data and validators (#647).

ERP-02 of the ERP module epic (#645): the ERPNext *doctype* surface
re-expressed as our own data-first document model. Nothing in this package
imports, edits or forks the durable execution engine — the workflows are data
the engine consumes, which is what lets the transactional spine (ERP-03, #648)
be built on the same model without either lane owning the other's files.

The package is a thin front door over four modules:

* :mod:`.schema` — the stdlib JSON-Schema subset validator the families are
  checked with, cross-checked against ``jsonschema`` by the suite;
* :mod:`.workflow` — the state/transition data model and its structural
  refusals (a state jump is refused by name);
* :mod:`.validators` — :class:`~.validators.DocumentModel`, the entry point that
  holds the schemas, the workflow data and the family rules together;
* :mod:`.errors` — the refusal vocabulary, on the house control-plane taxonomy.

The shipped assets live beside the code: ``schemas/*.json`` (the document
families and the workflow meta-schema) and ``workflows/*.yaml`` (each lifecycle
as data). ``provenance.json`` records the harvest the doctrine requires, and
``README.md`` states the contract the asset gate enforces.

---knowledge---
module_id: integrations.erp.core.__init__
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from . import errors, schema, validators, workflow  # noqa: F401

__all__ = ["errors", "schema", "validators", "workflow"]
