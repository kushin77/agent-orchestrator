# `integrations/erp/auth/` — tenant access to ERP data

The ERP module's **tenant-access lane** (issue [#653](https://github.com/kushin77/agent-orchestrator/issues/653),
EPIC [#645](https://github.com/kushin77/agent-orchestrator/issues/645), ERP-08):
tenant-scoped access to ERP data, ERPNext's role-based permission model mapped
onto the platform's tenant identity and RBAC, and field-level policy
declarations guardrails can enforce.

## What the lane answers

| Question | Answer | Where |
|---|---|---|
| Which tenant may this principal touch? | Exactly its own — always | [`model.py`](model.py), [`scope.py`](scope.py) |
| What may these ERP roles do to this document? | What the platform grants for the translated permission | [`roles.py`](roles.py) → `identity/rbac` |
| Which fields may this principal see or set? | What the field policy declares, enforced by omission | [`policies.py`](policies.py) |

## The three properties the lane is built on

1. **The tenant gate is an invariant, not a rule.** `Request.tenant` is a
   *claim*; `Principal.tenant` is the *authority*. They are compared before any
   role, policy or contract is consulted, and a mismatch is refused
   (`cross-tenant`). There is no parameter that turns the gate off, and no
   declaration in either catalogue can express "allowed across tenants" — the
   action vocabulary has no entry that could mean it. The negative control
   drives this with a principal holding **every** role, because the privileged
   principal is the one that must fail.

2. **This layer cannot escalate past the platform.** ERPNext role names are
   *translated* into `identity/rbac`'s `resource:action` language, and
   `identity/rbac` decides — its scope gate first, then its permission gate, the
   order its own README documents as the safe one. There is no allow path that
   skips the contract, so a role map cannot grant what the platform does not
   grant. Two controls show it from both sides: an ERP role that grants `read`
   while the platform binding lacks `erp.<kind>:read` is `permission-denied`,
   and a subject with no binding in the tenant is `scope-denied`.

3. **Every refusal is provoked, by name.** [`negative_control.py`](negative_control.py)
   provokes all **18** codes of the closed refusal vocabulary. A provocation that
   refuses under a *different* code fails — "something went wrong" is not
   evidence that the rule under test is what refused — and the coverage is
   computed against `model.REFUSALS`, so the two cannot drift. That is also why
   there is no `field-read-denied`: a withheld read is communicated by
   `Decision.redacted`, and a code for it would have been unreachable.

## Field-level policy: enforced by omission, and load-bearing both ways

A field whose policy denies `read` is **absent** from `Decision.projection`, not
blanked, and named in `Decision.redacted`. Absent and empty are different
claims, and only one of them is true.

`effect` is a word from `guardrails/policy`'s `DecisionLevel` — *consumed*, never
re-typed — and it is load-bearing in both directions, with each mistake refused
by name:

| Shape | `read` / `write` | effect the loader requires |
|---|---|---|
| hidden | `false` / `false` | `block` |
| write-only | `false` / `true` | `block` |
| read-only | `true` / `false` | `block` |
| advisory | `true` / `true` | `warn` or `log` |

A denial declared `log` would tell guardrails to allow-and-observe an action this
module refuses — the two layers would disagree about the same request. An
advisory declared `block` withholds nothing — a rule that cannot fail (GR-12).
An advisory that fires is reported on the decision, so a consulted action
resolves to allow, allow-with-a-warning, or deny, rather than passing silently
(AO-GR-19).

## Lane boundary (what this package consumes, and what it does not touch)

* **`identity/rbac`** is consumed as a *contract* — its README declares a freeze:
  later lanes import the names, never edit the files. This lane redefines no
  role, no permission and no part of the two-gate flow. `identity/` has no
  `__init__.py` (a later identity-phase lane owns adding one), so the package is
  reached through the seam in [`contract.py`](contract.py).
* **`guardrails/policy`** is consumed for its decision vocabulary, read from the
  file that defines it (`guardrails/policy/decision.py`) rather than by importing
  the package that re-exports the engine. Field policies are declared in this
  lane; making them *fire* inside the guardrails engine is that pillar's.
* **`integrations/erp/core`** is consumed for its schema validator
  (`schemas.py`), so there is one answer to "is this declaration well-formed",
  not two.
* **Not this lane's**: the REST surface (ERP-06, #651), the portal mount (ERP-07,
  #652), the database/transaction spine (ERP-03, #649). This lane ships the
  authorization layer those surfaces must call, and nothing that serves traffic.

Nothing here imports `integrations/erp/crm` or `integrations/erp/catalog`: the
doctype surface is declared locally in [`catalog/roles.json`](catalog/roles.json)
so this lane is testable offline, and the indexer-fed replacement of that
declaration is a change of *source*, not of code.

## Harvest provenance (GR-10)

Upstream ERPNext is **GPL-3.0** and is a **pattern source only**. No upstream
code is copied, vendored or translated. The two harvested shapes and what was
built instead are recorded in [`catalog/provenance.json`](catalog/provenance.json),
and [`provenance.py`](provenance.py) refuses a record that claims copied code
(`harvest-code-copied`) or names no replacement (`harvest-incomplete`) — so the
record cannot become decorative.

## Running it

```bash
python3 -m pytest integrations/erp/auth/tests -q     # the suite
bash scripts/check-erp-auth.sh                       # the gate (suite + check + mutant)
python3 -m integrations.erp.auth.cli check           # declarations, schemas, golden path, controls
python3 -m integrations.erp.auth.cli demo            # the golden-path transcript, as JSON
python3 -m integrations.erp.auth.cli roles           # the validated role map
python3 -m integrations.erp.auth.cli fields          # the validated field policies
python3 -m integrations.erp.auth.negative_control    # provoke every refusal
```

The CLI follows the repository's tri-state contract: `0` OK, `1` NOT-OK,
`2` CANNOT-ASSESS. A declaration or contract that will not load is **never** a
pass, and it is not a failure either — the module cannot report on a catalogue it
could not read.

## Layout

| Path | Role |
|---|---|
| [`model.py`](model.py) | the closed action and refusal vocabularies, `Principal`, `Request`, `Decision`, `Refused` |
| [`contract.py`](contract.py) | the two consumed contracts and the seams to reach them |
| [`roles.py`](roles.py) | the ERPNext role→permission table and its loader |
| [`policies.py`](policies.py) | field-level declarations, validated and enforced |
| [`scope.py`](scope.py) | the middleware: the tenant gate, the platform's two gates, projection |
| [`schemas.py`](schemas.py) | the frozen schemas and the step that enforces them |
| [`provenance.py`](provenance.py) | the GR-10 record and its enforcement |
| [`platform_fixture.py`](platform_fixture.py) | a deterministic, offline platform store for the golden path and the controls |
| [`cli.py`](cli.py) | `check` / `demo` / `roles` / `fields`, tri-state |
| [`negative_control.py`](negative_control.py) | one provocation per refusal, coverage computed |
| [`catalog/`](catalog) | the role map, the field policies and the harvest record |
| [`schema/`](schema) | the frozen JSON Schemas for those three declarations |
| [`tests/`](tests) | the suite (71 tests) |
