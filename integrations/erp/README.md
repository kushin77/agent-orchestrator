# `integrations/erp/` — the ERP module (EPIC #645, issue #646)

This directory is the control plane's **ERP module**: a declared, mandatory,
flag-gated module whose entire data contract is served by the knowledge indexer.
The integration-module assembly precedent is
[`integrations/paperclip/`](../paperclip/README.md); the feature-by-feature gap
this module is built against is
[`docs/ERP-MODULE-GAP-ANALYSIS.md`](../../docs/ERP-MODULE-GAP-ANALYSIS.md).

The epic's children build *into* this module — each of them lands in its own lane
beside this file. This lane delivers what they build on: the manifest, the
catalogue, and the indexer data contract that makes the catalogue the module's
only source of domain knowledge.

## What the module declares

| File | Role |
|---|---|
| [`module.yaml`](module.yaml) | the manifest: `mandatory: true`, feature flag `erp-module` default `off` (GR-5), `data_source: indexer`, the catalogue pointers, and the indexer sources that serve them |
| [`catalog/`](catalog/) | the ERP domain catalogue — the module's **single** store of domain facts |
| [`catalog/schema/`](catalog/schema/) | the frozen schemas the manifest and every catalogue file are validated against |
| [`catalog/tests/`](catalog/tests/) | the suite for the validator, including its provoked refusals |

The module has no runtime of its own: it is a declaration plus a catalogue. That
is deliberate, and it is what lets every child lane consume the same facts
without inventing its own copy of them.

## No second store: the catalogue is the only one

No ERP domain fact is declared in this README, in the manifest, or in the
indexer's source registry. The manifest **points** at the catalogue and names the
indexer globs that carry it; the catalogue is where the facts live.

That is a rule with a gate behind it, not an aspiration:

* every glob the manifest declares must be registered **verbatim** in
  [`governance/knowledge/sources.py`](../../governance/knowledge/sources.py), and
  at least one of them must be a glob under the catalogue — so "the module is
  indexer-fed" cannot outrun the indexer;
* every domain fact the catalogue declares is scanned for on the module's
  declaration surface (the manifest, this README, the source registry), and a
  restatement is refused **by name**;
* two files declaring one id, a family or a child issue the module map does not
  declare, and a pillar the map names that is not a directory of this repository
  are each refused by name;
* every catalogue file must carry the manifest's GR-10 provenance declaration
  verbatim, so there is one declaration rather than one per file to drift.

Consumers are deliberately out of scope. The children's code **reads** the
catalogue; the rule binds the *declaration* surface. A reader is not a store, and
holding a reader to it would forbid reading the catalogue at all.

## Everything the module knows is indexer-fed

```bash
python3 governance/knowledge/cli.py build      # regenerate the catalogue
python3 governance/knowledge/cli.py validate   # the index's own gate of record
python3 governance/knowledge/cli.py query --kind pattern-template
```

[`governance/knowledge/`](../../governance/knowledge/README.md) is the single
catalogue of this repository's institutional knowledge. This module is a *source*
in it — never a second index beside it. `sources.py` is the one place a source is
added, and the manifest must name exactly the globs registered there.

## Verification

```bash
python3 integrations/erp/catalog/cli.py verify   # the module's own refusals
python3 integrations/erp/catalog/cli.py vocabulary
bash scripts/check-erp-module.sh                 # the gate, provoked
make erp-module                                  # the same gate, via make
make verify                                      # the gate of record
```

[`scripts/check-erp-module.sh`](../../scripts/check-erp-module.sh) runs the checks
above **and** provokes each refusal against a scratch copy of the tree: a manifest
that stops declaring its admission, a feature flag that defaults on, a data source
that is not the indexer, a catalogue file that loses its provenance, a document
fact restated on the declaration surface, and a manifest glob the indexer does not
carry. Each provocation must be refused *by name*, and a clean copy must be
refused nothing — a check whose pass and fail paths collapse is a formality.

## Provenance (GR-10)

Upstream `frappe/erpnext` is a **pattern source only**: it is GPL-3.0, no upstream
code is copied into this repository, and no upstream repository is vendored. Every
catalogue file records the upstream entity its pattern was read from, and carries
the manifest's provenance declaration; a reading clone, if any, lives under
`.research/`, which is gitignored.

## The flag

The module ships **OFF**. `erp-module` defaults `off`, and no part of the module
is tenant-visible while it is off (GR-5); promotion is a reviewed go-live that
flips it. The central promotion row in
[`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml)
lands with the surface that becomes reachable (ERP-07, #652), together with the
matching `infra/terraform` variable that
[`scripts/check-feature-flags.py`](../../scripts/check-feature-flags.py) holds in
lock-step. A row declared here would gate nothing that runs.

## Working in this module

One issue = one lane = one branch, and no two lanes share a file
([`docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md)). Ownership of the
epic's children is on the board; the module's own declaration surface — this
README, the manifest and the catalogue — is ERP-01's.
