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
| `controls.yaml` | the **declared acceptance policy**: which conditions are fatal, which judgments are recorded (#591) |
| `policy.py` | reads that policy; stamps every refusal with its declared condition and disposition |
| `module-registry.schema.json` | the **frozen** document and row shape, refusal vocabulary included (#591) |
| `schema.py` | the stdlib-only subset validator the generator runs on what it emits (#591) |
| `audit.py` | the **append-only** trail: one record per refusal and per judged name (#591) |
| `cli.py` | the machine surface |
| `sync/` | live admission pin probe (issue #889) |

## Live sync

`governance/modules/sync/` (issue #889, lane L10) is a live pin probe: on
every call `sync.live.probe` re-reads the real admission register (root
`module.json` `submodules`, via the same `registry.load_register` the build
uses) and the real standards pin (`cmr-pin.yaml`), and reports each tracked
target's (`hermes-agents`, `paperclip`, `ollama`) admission state **exactly
as recorded** — `requested` / `independent` / `undecided`, never upgraded to
a membership claim (the admission-register doctrine above: a claim, never
membership). An entry claiming an admission state outside that honest,
non-membership vocabulary is refused by name (`AdmissionOverclaim`), and a
tracked target absent from the register is refused by name
(`UnknownAdmissionTarget`). Tests: `governance/modules/sync/tests/` (offline,
a scratch copy of the real register + pin), including the negative control
for an overclaimed admission value.

## Three artifacts judge a build (issue #591)

A registry nothing judges is a report, not a control. Three declared artifacts are
part of the code path, each read rather than described:

1. **`controls.yaml`** — the acceptance policy. `registry.build` calls
   `policy.Policy.judge` on every refusal, so a refusal travels with the condition
   that governs it and the disposition it carries; a code the policy does not
   declare is CANNOT-ASSESS (a refusal nobody declared is a refusal nobody
   reviewed). A refusal is **always** `fatal`: `policy.load` refuses a policy that
   files a refusal code as `recorded`, and refuses one whose authority would let a
   claim confer membership (the `kushin77/CMR#952` drift guard).
2. **`module-registry.schema.json`** — the frozen row shape. `registry.build`
   validates the document it is about to return, so a malformed row never leaves
   the generator; the violation names its JSON path. The validator is a stdlib-only
   JSON-Schema subset and refuses a schema keyword it cannot enforce.
3. **`audit.py`** — the append-only trail. `build`/`verify --audit FILE` appends one
   record per refusal and per judged name, then proves the file grew rather than
   changed: the bytes read before the write must be an exact prefix afterwards.
   A trail of another shape is refused, never mixed into. Nothing is written unless
   a caller names a path — the registry reads this tree and writes nothing into it.

## CLI

```bash
python3 governance/modules/cli.py build [--out FILE] [--live] [--audit TRAIL]
python3 governance/modules/cli.py verify [--audit TRAIL]
python3 governance/modules/cli.py membership <name>
python3 governance/modules/cli.py vendoring [--registry FILE]
python3 governance/modules/cli.py probe [--live]
```

`--policy FILE` and `--schema FILE` override the two packaged artifacts (the gate
drives mutants through them); the defaults are always this package's files, so no
caller's working directory decides what judged a build.

Exit codes are the repository's tri-state convention: **0** OK, **1** NOT-OK (a
fatal refusal, or membership refused), **2** CANNOT-ASSESS — the hub catalog is
absent or unreadable, the policy cannot judge what the registry emits, or the
emitted document does not satisfy the frozen schema. CANNOT-ASSESS is never
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
hub is not there. It also provokes the three artifacts of §"Three artifacts judge
a build": a policy that would file a refusal as `recorded`, a policy missing a
declared code, a policy whose authority would let a claim confer membership, a
schema the emitted document violates, and a trail of another shape appended to.

## Boundary (NG4)

This package **reads** the hub and this repo, and writes nothing into either.
A need that lands on the hub (or a peer) is a direction issue on that board.
