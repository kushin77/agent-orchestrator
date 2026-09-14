# `integrations/paperclip/reporting/` — the paperclip reporting half (issue #447)

Paperclip is the fleet's reporting agent (the orchestration half is hermes,
[ADR-0012](../../../docs/decision-records/ADR-0012-hermes-paperclip-boundary.md)).
This package is the reporting duty the persona card declares as the
`module-brief` capability: it composes **each mandatory module's distributable
brief** — the organized, per-module statement of *what every repo must carry, at
which pin, and whether it is current*.

It is an extension of the canonical paperclip module
([`../README.md`](../README.md)), not a parallel one.

## The files

| File | Role |
|---|---|
| `capability.py` | the capability contract: the id the card must declare, the tools it needs (`file_read` for the three read-only sources, `file_write` to freeze the artifact), the artifact's canonical home, and the sources the brief reads |
| `composer.py` | the deterministic composition — every rendered line is a claim carrying the registry row or cited path it resolves to |
| `model.py` | the claim book, citation resolution and the `BRIEF-*` refusal vocabulary; the three states and the `not-a-module` refusal are **imported** from `governance/modules/model.py` (issue #445), never restated |
| `cli.py` | `compose` / `check` / `claims` / `capability`, tri-state (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) |
| `tests/` | the suite: the contract, the composition, the claims, determinism and the CLI |

## What it reads, and what it refuses

Read-only sources: the module registry
([`../../../governance/modules/README.md`](../../../governance/modules/README.md),
issue #445), the committed board snapshot (`.board/snapshot.json`) and the hub
catalog (`vendor/CMR/catalog`). The brief writes nothing but its own artifact.

```bash
python3 integrations/paperclip/reporting/cli.py compose                # the brief
python3 integrations/paperclip/reporting/cli.py check                  # artifact vs a fresh composition
python3 integrations/paperclip/reporting/cli.py capability             # declaration vs the tools it needs
```

Refusals are named, never silent:

| Code | Refused |
|---|---|
| `BRIEF-CAPABILITY-UNDECLARED` | the card does not declare the capability |
| `BRIEF-CAPABILITY-TOOL-UNGRANTED` | the capability needs a tool the allowlist does not grant |
| `BRIEF-CLAIM-UNRESOLVED` | a brief line cites nothing, or cites something that is not there (the line is named) |
| `BRIEF-MODULE-NO-PIN` / `BRIEF-MODULE-NO-REV` | a module that cannot be briefed |
| `BRIEF-ASSET-NO-SEED` | a consumer asset that resolves to no seed |
| `BRIEF-PENDING-RENDERED-SHIPPED` | a pending module reported as shipped |
| `BRIEF-STALE` | the committed artifact differs from a fresh composition |

## Distribution

The brief, not a copy, travels with the assets: distribution stays with the
hub's existing channel (`controller/standards-sync.sh`,
`controller/standards-manifest.txt`, `templates/module/` seeds). This package
adds **no** second push mechanism and vendors nothing (GR-10 / ADR-0013 / NG4).

## Tests and gate

```bash
python3 -m pytest integrations/paperclip/reporting -q
bash scripts/check-module-brief.sh
```
