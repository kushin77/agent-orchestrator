"""Ecosystem module registry — one honest view of every module (issue #445).

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

See ``README.md`` for the contract and ``cli.py`` for the entry point.
"""

from __future__ import annotations

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
from .registry import build, findings, membership, render
from .vendoring import check_references, scan

__all__ = [
    "CATALOG_MODULE_NOT_MANDATORY",
    "CannotAssess",
    "DEFAULT_HUB",
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
    "build",
    "catalog_spec",
    "check_references",
    "findings",
    "load_hub",
    "membership",
    "pending_spec",
    "render",
    "scan",
    "sorted_refusals",
    "well_formed",
]
