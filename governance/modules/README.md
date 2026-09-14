# `governance/modules` — the ecosystem module registry (issue #445)

One honest view of every module: **mandatory status, consumer assets, pin/rev,
owning repo, board ref and a health probe** — assembled by *reference* from the
hub's own registry, never by copying module code into this repository.

This is the missing middle between the hub's "what ships where" (`GR-18`
mandatory flags in `catalog/modules/<id>/module.json`, the `catalog/mandatory.tsv`
registry, `catalog/validate.py`, the seeds under `templates/module/`) and the
consumers of that truth in this repo: the PMO rollup (#403), the paperclip HTTP
projection (#413) and peer-board triage (#427).

## The four surfaces, in precedence order

| # | Surface | Role |
|---|---------|------|
| 1 | `vendor/CMR/catalog/mandatory.tsv` + `catalog/modules/*/module.json` | **the authority**: what a module is, and what is mandatory |
| 2 | `targets.json` (this package) | what the mandatory set *should* become, each target naming the blocking hub issue(s) |
| 3 | root `module.json` `submodules` | this repo's admission **claims** — which never confer membership |
| 4 | the in-tree vendoring scan | proof that 1–3 are references, not copies |

The catalog always wins. A declared target that lands in the hub catalog is
**derived** as registered (and refused by name if it landed without the
mandatory flag); a register entry the catalog does not carry is **refused**
however loudly it describes itself.

## Three states, never two — plus a refusal that is not a state

| State | Meaning |
|---|---|
| `registered-mandatory` | in the hub catalog with `mandatory: true` (and a `mandatory.tsv` row in lockstep) |
| `target-pending` | declared in `targets.json`, not yet in the hub catalog — `shipped: false`, with the blocking hub issue named |
| `catalog-module-not-mandatory` | in the hub catalog, not in the mandatory set |

`not-a-module` is deliberately **not** a state: it is the answer to a membership
query about a name the hub catalog does not carry — membership refused by name
(the `kushin77/CMR#952` drift guard: portfolio membership is not module
membership). It is reported in `not_modules`, counted separately, and never
folded into the module list.

## Modules

| Module | Responsibility |
|---|---|
| `model.py` | the frozen vocabulary: three states, the refusal, the refusal-code list, `Refusal` |
| `hub.py` | reads the authority and mirrors its gate — drift in either direction, duplicate ids, unseeded consumer assets |
| `registry.py` | assembles the document, the declared target set, the register, and the membership query |
| `vendoring.py` | the no-vendoring scan: references, never in-tree source |
| `health.py` | the probe spec + status per entry (`not-run` unless the probe actually ran) |
| `targets.json` | the declared target set and watch questions (package data) |
| `cli.py` | the machine surface |

## CLI

```bash
python3 governance/modules/cli.py build [--out FILE] [--live]
python3 governance/modules/cli.py verify
python3 governance/modules/cli.py membership <name>
python3 governance/modules/cli.py vendoring [--registry FILE]
python3 governance/modules/cli.py probe [--live]
```

Exit codes are the repository's tri-state convention: **0** OK, **1** NOT-OK (a
refusal, or membership refused), **2** CANNOT-ASSESS — the hub catalog is absent
or unreadable, so the registry cannot be built at all. CANNOT-ASSESS is never
reported as a pass, which is what makes the gate honest on a clean clone with no
submodule.

## Determinism

`render()` sorts keys, sorts every list and records no time: two builds over one
revision are byte-identical. The hub root is recorded **as given**
(`vendor/CMR` — never absolutised), so the document travels between checkouts.

## Tests and gate

```bash
python3 -m pytest governance/modules -q
bash scripts/check-module-registry.sh
```

The suite is hermetic — every mutation happens on a scratch hub in a tmp
directory, because `vendor/CMR` is read-only and is the authority being read.
The gate provokes every acceptance refusal in a scratch copy and requires each
to be refused **by name**; it returns CANNOT-ASSESS rather than a pass when the
hub is not there.

## Boundary (NG4)

This package **reads** the hub and this repo, and writes nothing into either.
A need that lands on the hub (or a peer) is a direction issue on that board.
