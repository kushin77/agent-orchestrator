"""Ecosystem module registry — one honest view of every module (issue #445).

---knowledge---
module_id: governance.modules
system: governance
app: modules
solution_class: class
patterns: [append-only-ledger, offline-hermetic, deterministic]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#445", "#591"]
do_not_duplicate: null
---knowledge---

The registry federates four surfaces into one deterministic document:

* the **hub catalog** — ``vendor/CMR/catalog/mandatory.tsv`` and
  ``catalog/modules/*/module.json``, read-only, the authority on membership and
  mandatory status;
* this repository's **declared target set** — what the mandatory set should
  become, each target naming the blocking hub issue(s);
* this repository's **sub-module admission register** (root ``module.json``
  ``submodules``) — claims, which never confer membership;
* the **in-tree vendoring scan** — proof that all of the above are references,
  never copies.

It stores references (id, repo, pin, path), reports **three states never two**,
refuses membership by name for anything the hub catalog does not carry, and is
byte-stable across two builds over one revision.

Three artifacts judge every build, and all three are part of the code path
(issue #591): the **declared acceptance policy** (`controls.yaml` via `policy`)
stamps every refusal and says which conditions are fatal, the **frozen schema**
(`module-registry.schema.json` via `schema`) is enforced on the document the
generator is about to return, and the **append-only audit trail** (`audit`)
records every refusal and every judged name, deterministically and offline.

See ``README.md`` for the contract and ``cli.py`` for the entry point.
"""

from __future__ import annotations

from . import audit, policy, schema
from .audit import SCHEMA as AUDIT_SCHEMA
from .health import catalog_spec, pending_spec, well_formed
from .hub import DEFAULT_HUB, HubCatalog, HubModule, Seed, load as load_hub
from .model import (
    CATALOG_MODULE_NOT_MANDATORY,
    NOT_A_MODULE,
    REFUSAL_CODES,
    REGISTERED_MANDATORY,
    SCHEMA,
    STATES,
    TARGET_PENDING,
    CannotAssess,
    Refusal,
    sorted_refusals,
)
from .registry import build, by_disposition, findings, membership, render
from .schema import DEFAULT_SCHEMA, validate as validate_document
from .vendoring import check_references, scan

__all__ = [
    "AUDIT_SCHEMA",
    "CATALOG_MODULE_NOT_MANDATORY",
    "CannotAssess",
    "DEFAULT_HUB",
    "DEFAULT_SCHEMA",
    "HubCatalog",
    "HubModule",
    "NOT_A_MODULE",
    "REFUSAL_CODES",
    "REGISTERED_MANDATORY",
    "Refusal",
    "SCHEMA",
    "STATES",
    "Seed",
    "TARGET_PENDING",
    "audit",
    "build",
    "by_disposition",
    "catalog_spec",
    "check_references",
    "findings",
    "load_hub",
    "membership",
    "pending_spec",
    "policy",
    "render",
    "scan",
    "schema",
    "sorted_refusals",
    "validate_document",
    "well_formed",
]
